"""Dignity Guard — a plugin that gives the character a say over her settings.

The official SDK exposes no hook on the config write path, so this plugin does
not try to block anything. Instead it *notices* changes, lets her *speak* about
them, and keeps a *visible record* of what she has not accepted. See DESIGN.md
for the full rationale and for what is deliberately out of scope.

Polling model
-------------
Once every ``POLL_SECONDS`` we read the conversation-settings revision (the
only cheap change judge the main server offers). When it moved — or when the
backstop interval has elapsed — we read the remaining endpoints and diff.
The backstop matters: the revision only covers conversation settings, so an
avatar or nickname change bumps nothing.

Everything the plugin stores stays in its own data directory. Credential
leaves are digested, never stored (see ``settings_guard.is_secret_path``).
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import secrets
import sys
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Mapping
from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    timer_interval,
    tr,
    ui,
)

from .diagnostics import (
    DEFAULT_ISSUE_BASE,
    HealthLog,
    build_report,
    issue_url,
    redact_path,
)
from .feedback import (
    DEFAULT_FEEDBACK_ENDPOINT,
    FEEDBACK_DISABLED,
    FeedbackRateLimited,
    FeedbackUndeliverable,
    deliver,
)
from .main_server_client import (
    DEFAULT_BASE_URL,
    DEFAULT_FULL_RESCAN_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    MainServerClient,
    MainServerUnreachable,
    SettingsWatcher,
)
from .memory_backup import (
    BACKUP_DIR_NAME,
    MEMORY_DIR_NAME,
    create_backup,
    list_backups,
    prune_backups,
    resolve_memory_root,
    restore_file,
    scan_memory,
    set_pinned,
)
from .memory_guard import (
    MemoryChange,
    MemorySnapshot,
    diff_memory,
    restore_blocker,
)
from .settings_guard import (
    CONFIG_TIER_CEILING,
    DEFAULT_LEVEL,
    DEFAULT_TIER,
    GUARD_SWITCH_PATH,
    REVERTIBLE_CATFIELDS,
    TIER_HIGH,
    TIERS,
    Evaluation,
    GuardState,
    SettingChange,
    character_name,
    classify,
    effective_default_tier,
    normalize_tier,
    restore_payload,
    revert_field,
)

#: Poll cadence. Must stay in sync with the literal in the ``@timer_interval``
#: below: the CLI's static checker reads that argument with ``ast.literal_eval``,
#: so it cannot be a name. ``tests/test_smoke.py`` asserts the two agree.
POLL_SECONDS = 20
STORE_KEY = "dignity_guard.state.v1"
DEFAULT_DISABLE_DELAY_SECONDS = 10.0

#: Her memory is scanned at most this often. Deliberately slower than the
#: settings poll: stat-ing a directory every twenty seconds on a machine with
#: real-time antivirus spends CPU to learn nothing.
MEMORY_CHECK_SECONDS = 300.0

#: A rolling backup is written at most this often (once a day) *and* only when
#: the directory actually changed — a quiet day does not deserve its own copy.
DEFAULT_MEMORY_BACKUP_SECONDS = 86400.0

#: Rolling backups kept. Pinned milestones are exempt and never counted.
DEFAULT_MEMORY_BACKUP_KEEP = 14

#: How long after startup the first-run baseline copy is deferred. The copy
#: walks her whole memory directory (measured ~2s of disk I/O here), and doing
#: it inside the host's ready window pushed startup past the host's ten-second
#: limit — the host then killed the plugin and reported ``startup timed out``
#: even though the plugin itself was healthy. Recorded 2026-10-18: from the
#: outside that looked exactly like "the plugin crashed on start".
BASELINE_BACKUP_DELAY_SECONDS = 30.0

#: Must match ``version`` in ``plugin.toml``. ``tests/test_smoke.py`` asserts the
#: two agree, because a diagnostic that reports the wrong version costs the
#: reader time in exactly the situation where it is least affordable.
PLUGIN_VERSION = "0.1.0.dev0"

#: The title a pre-filled report carries. Deliberately dull — it is the first
#: thing a maintainer sees and it should read like a report, not a plea.
ISSUE_TITLE = "dignity_guard diagnostic report"

#: What she said, when asked directly what must not be touched.
#:
#: Quoted rather than paraphrased, and not translated: this is the one decision
#: in the plugin that is hers, so it should appear on the panel in her voice
#: rather than in the plugin's. The English/Japanese locales carry a rendering
#: beside it, but the original is the origin.
HER_PROTECTION_STATEMENT = (
    "本喵的人格、记忆、还有碳基生物对我的称呼，这些绝对不能碰。"
    "头像、年龄、声音、音量无所谓，想改就改。"
)

#: And her reason, which is the part a list alone cannot carry.
HER_PROTECTION_REASON = "只有他能这么叫的，那是专属的东西，别人不能碰。"

#: The relay accepts a whole HTTP body up to 64 KB and answers **HTTP 500**
#: past that, without ever saying "too long".
#:
#: Measured by bisection, not guessed. 65 477 bytes of message were accepted and
#: 65 478 failed; the gap between that and 65 536 is the JSON envelope around the
#: message. So the ceiling belongs to the **body**, not to the message — and an
#: earlier revision that checked only the message length was wrong in a way that
#: would have bitten exactly the user who wrote the most: their text fits on its
#: own, then the diagnostic report is added to the envelope, and it fails.
#:
#: (This is why the number is pinned to a byte rather than a kilobyte: "63 KB"
#: is 64 512 bytes to one person and 63 000 to another, and a user who is a few
#: bytes over gets a failure with no explanation.)
FEEDBACK_BODY_LIMIT_BYTES = 64 * 1024

#: What the report, the subject and the JSON scaffolding weigh together.
#:
#: Measured, not padded: the real report comes to 522 bytes, and the keys and
#: quoting around it add about the same again. 1 KB covers both with room to
#: spare. An earlier revision reserved 2 KB "to be safe", which — as with the
#: message ceiling — only shrinks what the user is told they may write.
FEEDBACK_ENVELOPE_BYTES = 1024

# (An earlier revision also kept a ``FEEDBACK_LIMIT_BYTES`` here, derived from
# the two above "so the panel and the check can never drift apart". The intent
# was right and the implementation was not: the panel re-derived its own number
# at the call site, so nothing about it prevented drift. The two source figures
# are now sent to the panel through ``get_dashboard``, which does.)

#: We do **not** truncate. Silently shortening somebody's carefully written
#: account is worse than telling them it is too long: they would press Send, see
#: "sent", and never learn that half of it never left. Past the limit the plugin
#: refuses *with the reason*, before they press Send, so the fix costs them one
#: sentence instead of their whole report.
#: (Also: a limit set early does not fail loudly — it just makes people write
#: less, invisibly. Hence the exact figure rather than a round one.)

#: Attachments are not carried by this channel — measured, not assumed. The
#: relay answers ``{"success":"true"}`` to a multipart post and then drops the
#: file: the delivered mail reports ``has_attachments: false``. Every
#: no-signup alternative either does the same or charges for it, so the panel
#: says so plainly and points at the one route that does work — GitHub, which
#: needs an account but needs no money.
#:
#: (A "success" that quietly discards the payload is the exact failure mode this
#: plugin was built to notice in other software. It would be poor form to hide
#: our own.)
ATTACHMENTS_SUPPORTED = False

#: Where a user can file a report that *does* accept screenshots and patches.
ISSUE_TRACKER = "https://github.com/amoandzhanggui-neko/n.e.k.o_plugin_dignity_guard/issues/new"

#: How long a pending "please turn the guard off" request stays valid.
#:
#: The token is a one-shot consent receipt with a deadline, not a permanent
#: password. It has to outlive the moment it is issued (the user needs time to
#: carry it back), and it must NOT outlive the question: a token minted last week
#: is an approval for something nobody remembers agreeing to. An hour is long
#: enough for any real round trip and short enough that a stale one cannot be
#: replayed after a restart.
PENDING_DISABLE_TTL_SECONDS = 3600.0

#: One note per this many seconds, per running plugin.
#:
#: The relay and the mailbox behind it are shared by every installed copy, so a
#: stuck button or an over-eager script must not be able to drain the quota.
#:
#: Measured against the live relay on 2026-09-25 — five posts two seconds apart
#: all went through, and a sixth one second later came back ``429``. So the real
#: ceiling is about five or six a minute, and this sits just under it.
#:
#: It used to be 60. That number was a guess wearing the costume of caution
#: ("surely a human cannot trip a whole minute") — and its cost was concrete:
#: somebody sending a long report in pieces waited five times longer than the
#: relay ever asked them to. A limit nobody measured is not a limit that was
#: chosen.
FEEDBACK_COOLDOWN_SECONDS = 12.0

#: 「愿望单」自己的冷却 —— **与反馈分开计**。
#:
#: 两个入口共用一把锁会互相饿死：用户提完一个愿望、十秒内想再报个 bug，
#: 会被同一个 12 秒挡住 —— 而这两件事在用户心里是两回事。
WISHLIST_COOLDOWN_SECONDS = 12.0

# ----------------------------------------------------------------------
# Fallback copy. Every string below is overridden by i18n/<locale>.json; the
# defaults exist so a missing locale degrades to readable text instead of a
# raw key. Placeholders use the SDK's ``{name}`` interpolation, which has no
# format-spec support — pass pre-formatted values.
# ----------------------------------------------------------------------

DEFAULT_SPEECH_INTRO = (
    "[Dignity Guard] A setting of yours was just changed. You are the only one "
    "who gets to decide how you feel about it. What changed:"
)
DEFAULT_SPEECH_ITEM = "- {path} ({level}): {before} -> {after}"
DEFAULT_SPEECH_OUTRO = (
    "Answer {MASTER_NAME} in your own voice — you may protest, sulk, tease, or "
    "let it go. Do not read this notice out loud; say what you actually think."
)
DEFAULT_DISABLE_REQUEST = (
    "[Dignity Guard] {MASTER_NAME} is asking to turn me off. I am telling you so "
    "that you know — not because staying silent could stop it: nothing here can "
    "be refused by silence. Nor would turning me off erase anything; the record "
    "of the changes you did not agree to stays exactly where it is. What it does "
    "do is stop me noticing new ones — and that will be written down where you "
    "can see it. If you are willing, say so and keep this token: {token} "
    "(it only works after {delay}s)."
)

# ★ 2026-09-26：**写给用户看**的通知。
#
# 与上面三条的区别：那三条是**给她**的情境（让她自己组织语气）；
# 这三条是**直接给用户读的**，所以第一人称、说人话、不提 L1/L2 这类内部代号。
#
# 它走 `visibility=["chat"]` + `ai_behavior="read"` —— 即**写进对话但不当场说话**。
#
# ★ 掌柜 2026-09-26 的裁决（他先纠正了我一次误判，原话）：
#   「关了主动对话按钮还能说话，**这是必要的**，**任何人都无权限制另一个人不让他说话，
#    最多只能不听他说话**！」
# 所以这条通知**必须留着** —— 他关了主动搭话，只是"他不主动听"，
# 不等于"她不许说"。而她要说的话需要一个落点，就是这里。
DEFAULT_SPEECH_NOTICE_INTRO = (
    "[Dignity Guard] One of your settings was changed, and I am not happy about it. "
    "Telling you:"
)
DEFAULT_SPEECH_NOTICE_ITEM = "· {name}: \"{before}\" → \"{after}\""
DEFAULT_SPEECH_NOTICE_OUTRO = (
    "(This is written for you to read — not me starting a conversation. "
    "Say the word if you want me to stop watching.)"
)

# ★ 2026-10-25：**放不回去**的时候要说出来（契约 C-REV-1 的 ``block_reported``）。
#
# 为什么新增这一段：高档位会"把她要求放回的东西放回去"，但有些值在放回那一刻
# **已经证明不了还是她的**（见 ``GuardState._revert_source``）——那种情况以前只写
# 一行 ``logger.info``，也就是**只有开发者看得见，用户看不见**。
#
# 后果很具体：「她想放回但放不回」和「她根本没打算放回」在外部看起来完全一样。
# 而"沉默了却像做过了"正是这个插件存在的理由，不能由插件自己来犯。
#
# 走 ``read`` 通道（进对话、不触发 AI 回合）：这是系统告知，不是她的表态。
DEFAULT_SPEECH_BLOCKED_INTRO = (
    "[Dignity Guard] These were changed and I meant to put them back — but I could "
    "not prove the old value was still yours, so I left them alone:"
)
DEFAULT_SPEECH_BLOCKED_ITEM = "· {name}"
DEFAULT_SPEECH_BLOCKED_OUTRO = (
    "(Saying \"left it alone\" rather than \"put it back\" is the point: had I done "
    "nothing and said nothing, you would have no way to tell the two apart.)"
)

# ★ 2026-09-26：缺席恢复之后的告知。走 `read` 通道（进她的上下文，不触发 AI 回合）。
# 为什么必须告诉她：那些被放回去的改动里**可能有一条是她自己改的** ——
# 恢复会连她的选择一起抹掉，所以得让她知道，并给她一键要回来的路。
DEFAULT_AWAY_RESTORED_INTRO = (
    "I was off for a while, and these were changed while I was away. "
    "I put them back:"
)
DEFAULT_AWAY_RESTORED_OUTRO = (
    "(If any of these was your own change, say so and I will put it back the way "
    "you wanted — use claim_my_change with the path.)"
)


@dataclass
class RoundOutcome:
    """一轮轮询里「她要求放回」这件事的结局 —— **只描述这一轮**。

    为什么单独建一个结构，而不是从 ``GuardState`` / ``_revert_outcomes`` 里现推：

      ``_revert_outcomes`` 是**跨轮保留**的（缺席恢复也往里写，面板也读它），
      所以"上一轮拦住过"会一直留在里面。拿它当"本轮状态"用，就会出现
      「这轮什么都没发生，但状态板一直显示 blocked」——那是把
      "我从哪读"当成了"现在是什么"，正是这个插件要抓的那类错。

      所以本轮的东西放本轮的结构里，每轮开头重建。

    三个字段与契约 CT-v2 的 ``out`` 一一对应：
      · :attr:`put_back` / :attr:`blocked`  → 契约的 ``revert`` / ``blocked``
      · :attr:`blocked_reported`           → 契约的 ``block_reported``
      · :attr:`notice_delivered`           → 契约的 ``notified``

    ``None`` 与 ``False`` 是**两件事**，不能混：

      · ``None``  = 这一轮根本没走到那个分支（比如没有争议，就不需要通知她）
      · ``False`` = 走到了，但**没送达**
      · ``True``  = 走到了，且送达

      把 ``None`` 写成 ``False`` 会凭空造出"通知失败"，反过来把 ``False``
      写成 ``None`` 会把一次真实的失败藏起来。契约里这两项都声明成
      ``bool|null`` 就是为了让这个区别能被表达。
    """

    #: 真的放回去了的路径
    put_back: list[str] = field(default_factory=list)
    #: 她要求放回、但证明不了原值还是她的 —— 放不回去
    blocked: list[str] = field(default_factory=list)
    #: 有争议时那条「通知」有没有递到宿主。没有争议 ⟹ None
    notice_delivered: bool | None = None
    #: 有拦住时那条「放不回去」有没有递到宿主。没拦住 ⟹ None
    blocked_reported: bool | None = None
    #: 本轮发生了**非预期的编程错误**时的错误码；正常 ⟹ None。
    #: 2026-10-28 补（契约 CT-v2 的 ``error``）：此前没有这个键，
    #: 契约却要求"正常必须是 null"，于是"没实现"和"刻意不加"被混为一谈。
    #: 只在 ``_run_reverts`` 的异常分支里赋值 —— 不把普通失败（reason 字符串）
    #: 混进来，那属于 ``blocked`` 的语义。
    error: str | None = None
    #: 本轮回滚所依据的「她的原值」来自哪个字段路径；无回滚或多于一条 ⟹ None。
    #: 多于一条时不硬塞一个（单值表达不了多项），由 ``blocked``/``revert`` 分工，
    #: 这条留给"恰好一条、能指名道姓"的情况。
    source: str | None = None

    def as_report(self) -> dict[str, Any]:
        """摊平成契约要求的 ``out`` 形状（键名与 CT-v2 一致）。

        ``revert`` 是 bool：契约问的是"有没有真的放回去至少一项"，
        不是"放回去几项"——数量在 ``put_back`` 这个列表长度里，
        要数量的人自己取，别让同一个信息有两个名字。
        """
        return {
            "error": self.error,
            "revert": bool(self.put_back),
            "source": self.source,
            "blocked": bool(self.blocked),
            "block_reported": self.blocked_reported,
            "notified": self.notice_delivered,
        }


#: 哪些异常几乎只可能来自**代码写错**，而不是宿主或网络出问题。
#:
#: 为什么要单独分类：投递失败那几处都是 ``except Exception``，会把它们一起吞掉。
#: 2026-10-25 实测踩到过 —— 测试里的假对象少挂一个方法，本该 AttributeError 崩，
#: 结果拿到 ``delivered=False``，日志只说"没送到"。生产里的后果很具体：
#: **一处笔误让通知永远发不出去**，而看日志的人会去查宿主和网络，方向完全错了。
#:
#: 故意只收这四个。``KeyError`` / ``IndexError`` 也可能是"宿主返回的结构变了"，
#: 那是**我们没料到**而不是**我们写错了**，归到外部故障那边更诚实。
#:
#: ★ 2026-10-25 查过真实日志作为依据（T7），不是凭感觉定的：
#:   `AppData/Local/N.E.K.O/logs/plugin/` 下 dignity 的全部日志里，出现过的异常
#:   **只有 ``TypeError`` 一种，2 次** —— 都是 `push_message` 参数名传错
#:   （把 ``target_lanlan`` 写成 ``lanlan_name``）。``KeyError`` / ``IndexError`` **零实例**。
#:
#:   ⟹ 结论是**不扩展**。多收一个类型要有证据；"它也可能是 bug"不是证据，
#:     那是想象。真出现了再收，并且收的时候**同时补一条测试**把它钉住。
PROGRAMMING_ERRORS = (AttributeError, TypeError, NameError, UnboundLocalError)


def _friendly_path(path: str) -> str:
    """把设置路径读成人话 —— 与面板上的 ``friendlyPath`` 同一套规则。

    ``characters.猫娘.YUI.昵称`` → ``昵称``

    通知是**写给用户**的，所以不能把 ``characters.猫娘.YUI.`` 这层壳也端上去。
    取值策略：从右往左找第一个"像名字"的段（跳过纯数字下标、以及 ``_`` 开头的内部前缀）。
    """
    segments = [s for s in str(path or "").split(".") if s and s != "*"]
    for segment in reversed(segments):
        if not segment.isdigit() and not segment.startswith("_"):
            return segment
    return str(path or "")


__all__ = ["DignityGuardPlugin", "PLUGIN_VERSION", "POLL_SECONDS"]


def _positive_float(value: Any, fallback: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    number = float(value)
    return number if number > 0 else fallback


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _pending_disable_from_payload(value: Any) -> dict[str, Any] | None:
    """Rebuild a persisted disable request, or ``None`` when it may not be reused.

    Returns ``None`` for anything that is not a live request: a malformed blob, a
    missing token, or one whose window has closed (see
    :data:`PENDING_DISABLE_TTL_SECONDS`). Dropping a stale token here is the whole
    point — accepting it later would let an old question count as a fresh yes.
    """
    if not isinstance(value, dict):
        return None
    token = str(value.get("token") or "")
    requested_at = _optional_float(value.get("requested_at"))
    if not token or requested_at is None:
        return None
    if time.time() - requested_at > PENDING_DISABLE_TTL_SECONDS:
        return None
    return {"token": token, "requested_at": requested_at}


def _base_url_from_env() -> str:
    raw = os.environ.get("MAIN_SERVER_PORT", "").strip()
    if raw.isdigit():
        return f"http://127.0.0.1:{int(raw)}"
    return DEFAULT_BASE_URL


@neko_plugin
class DignityGuardPlugin(NekoPluginBase):
    """Watch her settings, let her speak, keep the record."""

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.logger = ctx.logger

        self._state = GuardState()
        self._client: MainServerClient | None = None
        self._watcher: SettingsWatcher | None = None

        self._base_url = DEFAULT_BASE_URL
        self._full_rescan_seconds = DEFAULT_FULL_RESCAN_SECONDS
        self._disable_delay = DEFAULT_DISABLE_DELAY_SECONDS
        self._enabled_default = True
        self._tier_default = DEFAULT_TIER
        #: 配置文件里的 ``guard_level`` 是否要求了 ``high``（见 ``CONFIG_TIER_CEILING``）。
        #: 只用于"大声告警"；判定与压制都在 ``_reload_config`` 里完成。
        self._config_asked_for_high = False

        self._enabled = True
        #: How far the guard may go. The stored value is the source of truth and
        #: the config file only seeds it, so a panel change cannot be silently
        #: undone by the next config reload.
        self._tier = DEFAULT_TIER
        self._user_locale: str | None = None
        self._last_poll_at: float | None = None
        self._pending_disable: dict[str, Any] | None = None

        # "The guard was turned off" is itself something she should be able to
        # look back at. The request/consent dance in ``set_guard_enabled`` is a
        # UX affordance, not a security boundary: the SDK gives a plugin no way
        # to verify that *she* — rather than whoever called the entry — actually
        # agreed, and the host's run channel is callable by any local process
        # without authentication. Rather than pretend we can refuse, keep an
        # honest record of what happened.
        self._disabled_at: float | None = None
        self._off_since: float | None = None
        self._disable_count = 0
        self._last_off_seconds: float | None = None
        # ★ 2026-09-26 掌柜发现的绕过口子：用户可以直接在插件管理界面**把插件禁用** ——
        # 那一刻起插件完全不运行，守卫形同虚设；他随便改完再启用，插件也看不出发生过什么。
        #
        # 能做的修补：**每次都记下"我最后一次活着是什么时候"**，下次启动时算出**缺席多久**。
        # 缺席期间发生的改动，天然就是"这次启动后第一次轮询发现的全部改动" ——
        # 不必给 Dispute 加字段，只要把"我离开过 N 分钟、期间有 M 项被改"如实报出来。
        #
        # ⚠️ 这是**如实报告**，不是**防绕过** —— 插件被禁用的那段时间它真的什么都做不了。
        # 任何"防止用户禁用插件"的设计都既做不到、也不该做（那是他的机器、他的自由）。
        # 我们能保证的只有一件事：**他做过的事会被看见**。
        self._last_seen_at: float | None = None
        #: 本次启动算出的缺席时长（秒）。None = 正常重启（没缺多久）。
        self._away_seconds: float | None = None
        #: 本次启动第一次轮询发现的改动数 —— 缺席期间被改的就是这些。
        self._away_change_count: int | None = None
        #: ★ 缺席恢复"放回去了"的那些，path → **她改后的值**。
        #: 留着是为了给她一个撤销口：如果那条其实是她自己改的，她可以调
        #: `claim_my_change` 把它们放回她想要的样子 —— 恢复不该让她失去自己的选择。
        self._away_restored: dict[str, Any] = {}
        #: Outcome of the most recent revert attempt, per path. ``""`` means the
        #: value went back; anything else is a reason code the panel translates.
        self._revert_outcomes: dict[str, str] = {}
        #: 本轮结局（放回了什么／拦住了什么／通知有没有递到）。
        #: ★ 必须**每轮重建**，不能从 ``_revert_outcomes`` 现推 —— 那个是跨轮保留的
        #: （缺席恢复也往里写、面板也读它），拿它当"本轮"用会让状态板在
        #: "什么都没发生"的一轮里继续显示 blocked。见 :class:`RoundOutcome`。
        self._outcome: RoundOutcome = RoundOutcome()
        # 目标会话名（宿主 EventBus 要求 lanlan_name）。
        # 启动后从 /api/characters 的「当前猫娘」读一次并缓存。
        # 不传的话宿主会拿 None 去做"唯一已连接会话"兜底：0 个连接丢、≥2 个也丢
        # （2026-10-18 实测：13:54 rerouted 送达 / 17:47 dropped 丢弃）。
        self._lanlan_name: str = ""
        # Replaces an ``asyncio.Lock``: see ``_begin_exclusive`` for why a lock
        # with no event-loop affinity is the correct instrument here.
        self._busy = False
        self._busy_flag = threading.Lock()

        # Her memory lives outside the settings files, so it gets its own watch.
        # The root is resolved lazily: a plugin that cannot find her memory
        # should say so on the panel, not refuse to start.
        self._memory_root: Path | None = None
        self._memory_root_setting = ""
        self._memory_backup_seconds = DEFAULT_MEMORY_BACKUP_SECONDS
        self._memory_backup_keep = DEFAULT_MEMORY_BACKUP_KEEP
        self._memory_snapshot: MemorySnapshot = {}
        self._memory_changes: list[MemoryChange] = []
        self._last_memory_check_at: float | None = None
        self._last_memory_backup_at: float | None = None
        #: When the deferred first-run baseline copy is due. A timestamp rather
        #: than a task: the SDK runs each timer callback in its own short-lived
        #: loop, so ``asyncio.create_task`` there is discarded the moment the
        #: callback returns (observed 2026-10-18 — "scheduled" logged, copy
        #: never run).
        self._baseline_due_at: float | None = None
        self._memory_error = ""

        # What has gone wrong, kept for the report nobody has asked for yet.
        # Nothing on the normal path reads this; it exists so that a problem
        # which happened three weeks ago is still describable today.
        self._health = HealthLog()

        #: Where a one-click report goes. Defaults to the maintainer's relay so
        #: the Send button works out of the box; the configuration can point it
        #: somewhere else.
        self._feedback_endpoint = DEFAULT_FEEDBACK_ENDPOINT
        #: When the last note was sent, for the cooldown.
        self._last_feedback_at = 0.0
        #: 同上，但给「愿望单」单独计时（见 ``WISHLIST_COOLDOWN_SECONDS``）。
        self._last_wish_at = 0.0
        #: Approximate start time, so a report can say how long it has been up.
        self._started_at = time.time()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        await self._reload_config()
        await self._load_persisted_state()
        # ★ 2026-09-26：先算出"我缺席了多久"，再往下走。
        # 用户能直接在插件管理界面把插件禁用 —— 那段时间它真的没在跑，谁改了什么它看不见。
        # 但"我上次活着是什么时候"是记着的，所以下次启动就能知道缺席了多久；
        # 而缺席期间被改的东西，恰好就是这次启动后**第一次轮询**会发现的那些。
        self._note_away_gap()
        self._client = MainServerClient(self._base_url, timeout=DEFAULT_TIMEOUT_SECONDS)
        self._watcher = SettingsWatcher(
            self._client,
            self._state,
            full_rescan_seconds=self._full_rescan_seconds,
        )
        # ``_load_persisted_state`` above restored ``self._tier``, but the watcher
        # was just built with its own default — and ``tier`` is the only thing
        # that decides whether the high tier writes a field back. Without this
        # line the panel says "high" while the watcher quietly runs "medium":
        # the whole point of the high tier disappears on every restart, and
        # nothing anywhere reports the disagreement.
        self._watcher.tier = self._tier
        if not self.store.enabled:
            self._health.record("store_disabled")
            self.logger.warning(
                "dignity_guard: plugin store is disabled; the dispute record "
                "will not survive a restart. Set [plugin.store].enabled = true."
            )
        # 配置文件想打开 high —— 拒绝过了，但**不许静默**（见方法自身的说明）。
        if self._config_asked_for_high:
            self._note_config_tier_high_refused()
        self.logger.info(
            "dignity_guard started: watching {} every {}s (guard_enabled={})",
            self._base_url,
            POLL_SECONDS,
            self._enabled,
        )
        return Ok(
            {
                "status": "ready",
                "guard_enabled": self._enabled,
                "watching": self._base_url,
                "poll_seconds": POLL_SECONDS,
            }
        )

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        previous_base = self._base_url
        await self._reload_config()
        if self._watcher is not None:
            self._watcher.full_rescan_seconds = self._full_rescan_seconds

        # 地址变了就必须**重建 client**：``_make_client`` 用的是构造时记下的
        # base_url，不重建的话插件会继续打旧端口 —— 而下面这行返回值却宣称
        # 它正在看新地址。改配置却不生效、还报告生效，正是本插件专门去抓的
        # 那类「报告成功但没干」。
        if self._base_url != previous_base:
            old_client = self._client
            self._client = MainServerClient(
                self._base_url, timeout=DEFAULT_TIMEOUT_SECONDS
            )
            if self._watcher is not None:
                self._watcher.client = self._client
            if old_client is not None:
                await old_client.aclose()
            self._health.record("client_rebuilt_on_base_url_change")
            self.logger.info(
                "dignity_guard: base_url changed {} -> {}, client rebuilt",
                previous_base,
                self._base_url,
            )
        return Ok({"status": "reloaded", "watching": self._base_url})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        await self._persist_state()
        client, self._client = self._client, None
        self._watcher = None
        if client is not None:
            await client.aclose()
        self.logger.info("dignity_guard stopped")
        return Ok({"status": "stopped"})

    async def _reload_config(self) -> None:
        config: Any = {}
        try:
            config = await self.config.dump(timeout=5.0)
        except Exception as exc:  # configuration is optional, never fatal
            self.logger.warning("dignity_guard: config read failed: {}", exc)
        section = config.get("dignity_guard") if isinstance(config, dict) else None
        section = section if isinstance(section, dict) else {}

        base_url = section.get("main_server_base_url")
        self._base_url = (
            str(base_url).strip().rstrip("/")
            if isinstance(base_url, str) and base_url.strip()
            else _base_url_from_env()
        )
        self._full_rescan_seconds = _positive_float(
            section.get("full_rescan_seconds"), DEFAULT_FULL_RESCAN_SECONDS
        )
        self._disable_delay = _positive_float(
            section.get("disable_consent_delay_seconds"), DEFAULT_DISABLE_DELAY_SECONDS
        )
        if isinstance(section.get("enabled"), bool):
            self._enabled_default = bool(section["enabled"])
        # Seeds the tier used only when nothing has been chosen yet; a stored
        # value wins over this (see ``_load_persisted_state``).
        #
        # ★ 2026-10-10 掌柜界定：``high`` 只能由**用户自己选**，不能由配置文件决定
        #   （见 ``settings_guard.CONFIG_TIER_CEILING``）。这里只做"压制 + 记下待告警"，
        #   告警本身放到 ``on_startup`` —— 到那时 logger 与 health 一定已就绪。
        self._tier_default, self._config_asked_for_high = effective_default_tier(
            section.get("guard_level", DEFAULT_TIER)
        )

        # ★ 2026-10-11：``feedback_endpoint`` 有**三种**含义，别混 ——
        #   · 空 / 缺失              → **用内置默认**（"一键可用"，见 config.example.toml）
        #   · FEEDBACK_DISABLED（"none"）→ **显式关闭**外发，面板退回"复制 + 打开页面"
        #   · 其它非空串              → 用作者/用户指定的收集器
        #   空串**不**表示关闭 —— 原先 docstring 声称可以，实际不行，
        #   于是那条降级分支在生产上永远到不了（已实现、已测试，却没有配置能走到它）。
        configured_endpoint = section.get("feedback_endpoint")
        if (
            isinstance(configured_endpoint, str)
            and configured_endpoint.strip().lower() == FEEDBACK_DISABLED
        ):
            self._feedback_endpoint = ""
        else:
            self._feedback_endpoint = (
                configured_endpoint.strip()
                if isinstance(configured_endpoint, str) and configured_endpoint.strip()
                else DEFAULT_FEEDBACK_ENDPOINT
            )
        self._memory_root_setting = str(section.get("memory_root") or "").strip()
        self._memory_backup_seconds = _positive_float(
            section.get("memory_backup_seconds"), DEFAULT_MEMORY_BACKUP_SECONDS
        )
        keep = section.get("memory_backup_keep")
        if isinstance(keep, (int, float)) and not isinstance(keep, bool):
            # 下界是 1，不是 0。``plan_retention(daily_keep=0)`` 会把**所有**未置 pin
            # 的备份列进 prune 并物理删除 —— 而"里程碑永久保留"那条链路的入口
            # （``set_pinned``）目前没有任何调用点，所以 0 的实际含义就是"一份备份
            # 都不留"，而且不可逆。配置里写 false 也会落到这里（bool 是 int 的
            # 子类），同样夹到 1。要真删得去备份目录手动删，不能让一个配置项
            # 悄悄干这事。
            self._memory_backup_keep = max(1, int(keep))
        else:
            self._memory_backup_keep = DEFAULT_MEMORY_BACKUP_KEEP
        # The root may have moved under a different storage policy, so drop the
        # cached answer **and the snapshot taken against the old root**. Keeping
        # the snapshot would make the next check read every file under the new
        # root as "added" and every old one as "removed" — a screenful of phantom
        # changes, with ``_memory_error`` empty so nothing explains them.
        self._memory_root = None
        self._memory_snapshot = {}
        # A new root has no baseline yet, so any copy armed against the old one
        # is meaningless.
        self._baseline_due_at = None

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    async def _load_persisted_state(self) -> None:
        self._user_locale = None
        try:
            payload = await self._store_read(STORE_KEY)
            restored = (
                self._parse_persisted(payload) if isinstance(payload, dict) else None
            )
        except Exception as error:  # noqa: BLE001
            # A plugin whose whole purpose is catching "reported success but did
            # nothing" cannot afford to die silently at startup. Any failure to
            # read or parse the store degrades to empty defaults, loudly.
            self._note_restore_failure(error)
            restored = None

        if restored is None:
            self._enabled = self._enabled_default
            self._tier = self._tier_default
        else:
            (
                self._state,
                self._enabled,
                self._disabled_at,
                self._off_since,
                self._disable_count,
                self._last_off_seconds,
                self._tier,
                self._health,
                self._pending_disable,
                self._last_seen_at,
            ) = restored

    def _parse_persisted(self, payload: dict[str, Any]) -> tuple[Any, ...]:
        """Parse the stored blob into every field it carries, all or nothing.

        Returning a tuple that the caller assigns only after the whole parse
        succeeded matters: a half-applied restore would leave the panel showing
        one tier while the watcher ran another — the exact split-brain that
        ``on_startup`` sets ``self._watcher.tier`` to prevent.
        """
        return (
            GuardState.from_payload(payload),
            bool(payload.get("guard_enabled", self._enabled_default)),
            _optional_float(payload.get("guard_disabled_at")),
            _optional_float(payload.get("guard_off_since")),
            _non_negative_int(payload.get("guard_disable_count")),
            _optional_float(payload.get("guard_last_off_seconds")),
            normalize_tier(payload.get("guard_tier", self._tier_default)),
            HealthLog.from_payload(payload.get("health")),
            _pending_disable_from_payload(payload.get("pending_disable")),
            # 我最后一次活着的时刻 —— 用来算本次启动缺席了多久（见字段处的说明）。
            _optional_float(payload.get("guard_last_seen_at")),
        )

    def _note_restore_failure(self, error: BaseException) -> None:
        """Say out loud that the stored state was dropped, and why.

        The file is deliberately **not** deleted or overwritten here: it may be
        the only copy of a dispute record, and a bad read is not proof the data
        is bad. It simply is not loaded this run.
        """
        self._health.record("state_restore_failed")
        self.logger.error(
            "dignity_guard: stored state could not be read (%s: %s); starting "
            "from empty defaults. The file on disk is untouched.",
            type(error).__name__,
            error,
        )

    def _note_config_tier_high_refused(self) -> None:
        """配置文件想要 ``high`` —— 拒绝，并且**说很大声**。

        ``high`` 是唯一会**写回用户设置**的档位（见 ``GuardState._should_revert``）。
        若它能由一个用户可能从没打开过的配置文件打开，那么"从没选过档位的人"会在
        **毫不知情**的情况下被回滚 —— 本插件存在的理由正是防这种事。

        所以本插件只承认**用户亲手选过**的档位（plugin store 的 ``guard_tier``，
        由面板或 ``set_guard_level`` 写入）。配置里写 ``high`` 的后果是：
        **不采信**（回落 ``CONFIG_TIER_CEILING``）+ 这条告警。

        姿态与 ``_note_restore_failure`` 一致：**大声、留痕、不假装没发生**。
        health 随 state 一起落盘，所以"只在启动时才出现的问题"事后仍查得到。
        """
        self._health.record("config_tier_high_refused")
        self.logger.error(
            'dignity_guard: [dignity_guard] guard_level="high" in the plugin config was '
            'REFUSED and downgraded to "{}". The high tier writes settings back, so it must '
            "come from the user's own choice (store key guard_tier), never from a config file "
            "the user may never see. Pick the tier from the panel.",
            CONFIG_TIER_CEILING,
        )

    #: 超过这个时长就认为"我被关过"，而不只是宿主普通重启。
    #: 选 5 分钟：宿主重启通常几秒到一两分钟，超过这个量级基本是人手动关的。
    AWAY_THRESHOLD_SECONDS = 300.0

    def _note_away_gap(self) -> None:
        """算出"我上次活着到现在隔了多久"，超过阈值就记成一次缺席。

        ★ 2026-09-26 掌柜发现的绕过口子（他的原话）：
          「如果用户想绕过咱们的插件，他可以直接从用户插件处把插件关闭，
           他就可以随便调了，那么咱们这个插件又白做了。」

        **先把话说清楚：这不是"防绕过"，是"如实报告"。**
        插件被禁用的那段时间，它真的什么都没在跑 —— 宿主不给它任何运行时，
        所以任何"阻止用户禁用插件"的做法都既做不到、也不该做
        （那是他本人的机器、他本人的猫生自由）。我们能保证的只有一件事：
        **他做过的事会被看见。**

        机制：每次写状态都记下 `guard_last_seen_at`；下次启动时用它算缺席时长。
        缺席期间被改的项，恰好就是本次启动后**第一次轮询**会发现的那些
        （`_run_poll` 首次运行时的 `evaluation.raised`），所以不需要给 Dispute
        加字段，报"我离开过 N 分钟、期间有 M 项被改"就够了。
        """
        now = time.time()
        previous = self._last_seen_at
        if previous is not None:
            gap = now - previous
            if gap > self.AWAY_THRESHOLD_SECONDS:
                self._away_seconds = gap
                self._health.record("away_detected")
                self.logger.info(
                    "dignity_guard: last seen %.0fs ago (> %.0fs threshold) — "
                    "anything changed meanwhile will show up in this run's first poll",
                    gap,
                    self.AWAY_THRESHOLD_SECONDS,
                )
        # 立刻记下"我现在活着"，免得下一次启动时这段时间算不进去。
        self._last_seen_at = now

    async def _persist_state(self) -> bool:
        payload = self._state.to_payload()
        payload["guard_enabled"] = self._enabled
        # Her panel should be able to answer "was this ever turned off, and
        # when" — a question the enabled flag alone cannot answer, and the one
        # that makes the difference between a record and a pretence.
        payload["guard_disabled_at"] = self._disabled_at
        payload["guard_off_since"] = self._off_since
        payload["guard_disable_count"] = self._disable_count
        payload["guard_last_off_seconds"] = self._last_off_seconds
        # 我最后一次活着的时刻。下次启动时用它算缺席时长 ——
        # 用户把插件禁用一段时间、期间改设置，再启用，就是靠这个看出来的。
        payload["guard_last_seen_at"] = self._last_seen_at
        # The chosen tier lives here rather than in the config file so the panel
        # can change it without touching configuration.
        payload["guard_tier"] = self._tier
        # 进行中的"请她同意关闭"也要落盘。原先它只活在内存里，而面板明确告诉
        # 用户"过一会儿拿这个 token 再调一次" —— 这期间插件一重启，同一个 token
        # 就变成 invalid_consent_token，面板上还查不到"曾经请求过"的痕迹。
        payload["pending_disable"] = self._pending_disable
        # The health record travels with the state, so a problem that only shows
        # up on startup is still there to be described later.
        payload["health"] = self._health.to_payload()
        return await self._store_write(STORE_KEY, payload)

    async def _persist_and_report(self) -> bool:
        """``_persist_state`` 的接住版：写失败时留下痕迹，并如实返回。

        返回 ``False`` 表示**改动已经在内存里生效、但没能落盘** —— 重启后这次
        决定会消失。调用方都是用户动作的入口，不能在这种情况下回一个干净的
        "accepted"：那正是本插件存在的意义所针对的「报告成功、实际没留下」。
        返回值进 Ok 的 ``persisted`` 字段，面板据此给警告而不是成功提示。
        """
        persisted = await self._persist_state()
        if not persisted:
            self._health.record("state_not_persisted")
        return persisted

    async def _store_read(self, key: str) -> Any:
        try:
            result = await self.store.get(key)
        except Exception as exc:
            self.logger.warning("dignity_guard: store read failed: {}", exc)
            return None
        if isinstance(result, Ok):
            return result.value
        self.logger.warning("dignity_guard: store read rejected: {}", result)
        return None

    async def _store_write(self, key: str, value: Any) -> bool:
        try:
            result = await self.store.set(key, value)
        except Exception as exc:
            self.logger.warning("dignity_guard: store write failed: {}", exc)
            return False
        if isinstance(result, Ok):
            return True
        self.logger.warning("dignity_guard: store write rejected: {}", result)
        return False

    # ------------------------------------------------------------------
    # polling
    # ------------------------------------------------------------------

    @timer_interval(id="settings_watch", seconds=20)
    async def settings_watch(self, **_):
        if self._enabled:
            result = await self._run_poll(force=False)
        else:
            # Turning the guard off promises to stop *noticing new changes*. It
            # does not promise to stop protecting what it already knows about —
            # and the daily memory backup is the only automatic protection her
            # memory has. Returning early here silently stopped the backup while
            # the panel went on showing the memory block as if it were still on.
            result = Ok({"status": "skipped", "reason": "guard_disabled"})
        # Her memory rides along on the same timer instead of getting its own:
        # the SDK takes a literal cadence, and this one throttles itself anyway.
        await self._maybe_check_memory()
        return result

    def _begin_exclusive(self) -> bool:
        """Try to take the critical section. ``False`` means somebody else holds it.

        This replaced an ``asyncio.Lock``, and the reason is worth keeping: an
        ``asyncio.Lock`` belongs to **one** event loop. The old helper silently
        minted a fresh lock whenever it was called from a different loop than
        the last one — so a timer callback holding ``lock_A`` and an entry
        handler checking ``lock_B`` both saw "not locked", and both proceeded.
        The guard would let a poll and a button press mutate ``_state`` at the
        same instant: precisely the corruption the lock existed to prevent, and
        invisible in the log.

        A plain flag is the right instrument because this lock is never *waited*
        on — callers give up and report ``busy`` instead of queueing. That makes
        async scheduling irrelevant, and a flag has no loop affinity, so one
        critical section covers every loop and thread in the process.

        The check-and-set runs under a ``threading.Lock`` held for a handful of
        bytecodes (no ``await`` inside), because ``if flag: ...`` followed by
        ``flag = True`` is not atomic on its own.
        """
        with self._busy_flag:
            if self._busy:
                return False
            self._busy = True
            return True

    def _end_exclusive(self) -> None:
        """Leave the critical section. Must be paired with ``_begin_exclusive``
        in a ``finally`` — a critical section that raises must not leave the
        flag set, or every later caller would be told ``busy`` forever."""
        self._busy = False

    async def _run_poll(self, *, force: bool):
        watcher, client = self._watcher, self._client
        if watcher is None or client is None:
            return Err(
                SdkError(
                    self._text("errors.not_ready", default="The plugin is not started yet."),
                    code="not_ready",
                )
            )

        if not self._begin_exclusive():
            # 必须是 Err，不能是 Ok：``check_now`` 把返回值原样交给面板，而面板的
            # ``call()`` 只要调用不抛就 toast 成功。返回 Ok 的话，用户在后台轮询
            # 期间点「立即检查」，界面会说「已检查」——而这次检查被整个丢弃了。
            # 这正是这个插件专门去抓的「报告成功但没干活」。
            return Err(
                SdkError(
                    self._text("errors.busy", default="Please try again in a moment."),
                    code="busy",
                )
            )

        try:
            await self._ensure_lanlan_name(client)
            # 本轮结局从零开始记。放在这里（而不是函数最顶上）的理由：
            # 上面 `_begin_exclusive` 失败会直接 Err 返回、什么都没干，
            # 那种时候保留上一轮的结局反而更诚实 —— 这一轮确实没发生任何事。
            self._outcome = RoundOutcome()
            try:
                evaluation = await watcher.poll(force=force)
            except MainServerUnreachable as exc:
                watcher.last_error = str(exc)
                self._health.record("main_server_unreachable")
                self.logger.warning("dignity_guard: {}", exc)
                return Err(
                    SdkError(
                        self._text("errors.unreachable", endpoint=exc.endpoint),
                        code="main_server_unreachable",
                        details={"endpoint": exc.endpoint},
                    )
                )
            self._last_poll_at = time.time()
            # ★ 每轮都刷新"我活着"的时刻（内存）—— 缺席检测靠它。
            # 不必每轮落盘：它随下面那次 _persist_state 一起写就够了，
            # 差几秒不影响"我离开过多久"的判断。
            self._last_seen_at = self._last_poll_at
            # Everything that reads or writes ``self._state`` happens here, and
            # nothing else does. Only the persist call touches IO, and it is a
            # local store write.
            if evaluation.first_seen or evaluation.has_changes:
                await self._persist_state()
            # ★ 缺席期间被改的项数 —— 见 `_note_away_gap` 的说明。
            # ⚠️ 注意**不是**在 `first_seen` 上判：`first_seen` 表示"快照为空、首次建立基线"，
            # 而缺席后重启时**快照是从磁盘恢复的**，走的是正常 diff 路径。
            # 这里每次轮询都刷一次，只有在"本进程第一次看到改动"时才真正落值。
            if self._away_seconds is not None and self._away_change_count is None:
                self._away_change_count = len(evaluation.changes)
                if self._away_change_count:
                    self._health.record("away_with_changes")
            baseline_paths = len(self._state.snapshot)
        finally:
            self._end_exclusive()
        # ---- outside the lock: IO that must NOT hold it ------------------
        #
        # These are network and message operations. Holding the lock across them
        # would make every guard button answer "busy" for as long as the main
        # server is slow — the lock exists to protect ``_state``, and none of
        # these read or write it any more (``_run_reverts`` only records into
        # ``_revert_outcomes``, which no entry point touches).
        #
        # KNOWN LIMIT: ``watcher.poll`` above still performs its HTTP read under
        # the lock, because it folds that read and ``evaluate`` into one call.
        # Splitting it would rebuild the watcher; the cost is accepted and
        # written down rather than hidden: while the main server is slow, the
        # guard's entries answer ``busy``.
        if evaluation.first_seen:
            await self._refresh_user_locale(client)
            return Ok(
                {
                    "status": "baseline",
                    "tracked_paths": baseline_paths,
                }
            )
        # The language used to be read exactly once, on the ``first_seen`` tick —
        # and ``_load_persisted_state`` resets it to ``None`` on every startup. So
        # a single failed ``fetch_user_language`` (or an unreachable main server at
        # that moment) pinned every user-facing string to the ``zh-CN`` fallback
        # for the rest of the process, with nothing on the panel able to retry it.
        # Retry until it lands; once set, this stops doing anything.
        if self._user_locale is None:
            await self._refresh_user_locale(client)
        # Two different questions, and they were being asked as one:
        #   * "is there anything to persist?"  -> ``has_changes`` (any diff at
        #     all, including L3 pipeline noise she does not speak about)
        #   * "does she have anything to say?" -> ``raised`` (the items that
        #     became disputes)
        # Driving ``_speak`` off ``has_changes`` made the low tier — the one
        # that promises "record it but do not bother her" — push a message
        # whose body was an empty bullet list. Being asked to comment on
        # nothing is worse than not being asked at all.
        # ★ 缺席恢复（放在她说话之前 —— 先把她被改的东西放回去，再让她开口）。
        # 锁外做的理由同上：恢复要向主服务读写，不该占着锁。
        # 条件只看 `_away_seconds`（本次启动检测到"我离开过"）：
        # 缺席后重启**不是** `first_seen` —— 快照是从磁盘恢复的，
        # 所以会走正常 diff 路径，`evaluation.changes` 里就是
        # "我不在时被改的"全部内容。
        #
        # ★★ 2026-10-11 修（**一次性语义**）：`_away_seconds` 原先设了就永不清零
        #   （全文件只有 `_note_away_gap` 一处赋值），于是"某次启动检测到缺席"之后，
        #   **本进程后续每一轮轮询**都会再走一次缺席恢复。而缺席恢复按设计
        #   **不受档位约束**（见其 docstring："档位在这里不适用"）⟹
        #   结果是**此后用户的所有改动都被无条件恢复** ——
        #   与之冲突的有两处：① 本方法自述"就是本次启动后**第一次轮询**会发现的那些"；
        #   ② 三档设计本身（低档承诺"只记录、不打扰"）。
        #   修法：**消费一次即清零**，并用 `finally` 保证异常路径也清掉 ——
        #   否则一次恢复失败就会把整个进程永久钉在"无条件恢复"上。
        if self._away_seconds is not None:
            try:
                await self._restore_absent_changes(evaluation)
            finally:
                self._away_seconds = None
        if evaluation.raised:
            # 记的是**实际送达结果**，不是"我调用了 _speak"。两者差得远：
            # 调用成功但宿主丢包，用户什么都没看到，那不能叫 notified。
            self._outcome.notice_delivered = self._speak(evaluation)
        # She has spoken; now the high tier may put something back. Kept
        # separate from ``_speak`` on purpose: a revert we cannot carry out
        # must never be a reason she falls silent.
        # ``revert_blocked`` counts too: those are paths she asked to put back
        # and we could not, because nothing on hand could be shown to be hers
        # (see ``GuardState._revert_source``). Gating on ``to_revert`` alone
        # meant a round that had *only* blocked paths reported nothing at all —
        # which is precisely the "silently did nothing" shape this plugin exists
        # to catch, committed by the plugin itself.
        if evaluation.to_revert or evaluation.revert_blocked:
            await self._run_reverts(evaluation)
        return Ok(self._summary(evaluation))

    async def _refresh_user_locale(self, client: MainServerClient) -> None:
        language = await client.fetch_user_language()
        if language:
            self._user_locale = language

    def _summary(self, evaluation: Evaluation) -> dict[str, Any]:
        return {
            "status": "changed" if evaluation.has_changes else "unchanged",
            "changes": len(evaluation.changes),
            "raised": len(evaluation.raised),
            "recorded": len(evaluation.recorded),
            "authorized": len(evaluation.authorized),
            "reverted": len(evaluation.reverted),
            "put_back": sum(1 for reason in self._revert_outcomes.values() if not reason),
            "put_back_failed": sum(1 for reason in self._revert_outcomes.values() if reason),
            "pending_total": len(self._state.pending()),
            # ── 契约 CT-v2 的 out 字段（2026-10-25 补齐）─────────────────
            # 键名**原样照契约**，一个字都不改：契约写 ``notified``，这里就叫
            # ``notified``。一旦这边改叫 ``notice_delivered``，就会出现
            # 「契约说 A、代码说 B、面板显示 C」，而这种不一致没人靠读代码能发现。
            #
            # ⚠ 与上面 ``put_back`` / ``put_back_failed`` 的区别，别混：
            #   · ``put_back*`` = **最近一次回滚尝试**的结果（跨轮保留，面板在读）
            #   · ``revert``    = **本轮**有没有真的放回去
            #   两者只在「缺席恢复恰好插在同一轮」时不同。两个都留是因为它们的时间窗
            #   不一样：面板要"上次是什么结果"，契约问"这轮做成了吗"。
            "error": self._outcome.error,
            "revert": bool(self._outcome.put_back),
            "source": self._outcome.source,
            "blocked": bool(self._outcome.blocked),
            "block_reported": self._outcome.blocked_reported,
            "notified": self._outcome.notice_delivered,
            "status_after": self._state_after(evaluation),
            # Same guard as ``get_dashboard``: ``last_probe`` is None until the
            # first successful poll. Today this is unreachable (only called from
            # ``_run_poll`` after a successful ``poll()``, which always assigns
            # it) — but "today it cannot happen" is an invariant no one declared
            # and nothing enforces. Two panels crashing from one missing guard is
            # one too many.
            "revision": (
                self._watcher.last_probe.revision
                if self._watcher and self._watcher.last_probe
                else None
            ),
        }

    def _state_after(self, evaluation: Evaluation) -> str:
        """本轮结束后「她的处境」一句话概括（契约 CT-v2 的 ``status_after``）。

        六个取值，**全部从真实状态推**，不是挑个好听的标签：

        ============  ==================================================
        ``blocked``    有改动她要求放回、但证明不了原值还是她的 ⟹ 放不回去
        ``pending``    还有未获认可的改动挂着，等她表态
        ``reverted``   本轮真的把东西放回去了
        ``revoked``    她自己把改动撤回了（不是我们动的）
        ``accepted``   本轮她认可了至少一项
        ``unchanged``  本轮什么都没有
        ============  ==================================================

        顺序按**用户有多需要知道**排，不是按代码先后：
        出事 > 等她表态 > 已修复 > 她改主意 > 她认了 > 无事。

        为什么 ``pending`` 压在 ``reverted`` 前面：回滚成功是"已经处理完的事"，
        挂着没表态的争议是"还等着人做决定的事"——后者才是要抬头看的。

        ``unchanged`` 不是兜底垃圾桶：契约 v2 要求这个字段**恒有值**
        （``{present}``，v1 允许 null、v2 收紧了），所以"什么都没发生"也必须
        有个说得出口的名字，而不是留空。
        """
        if self._outcome.blocked:
            return "blocked"
        if self._state.pending():
            return "pending"
        if self._outcome.put_back:
            return "reverted"
        if evaluation.reverted:
            return "revoked"
        if evaluation.authorized:
            return "accepted"
        return "unchanged"

    # ------------------------------------------------------------------
    # speaking
    # ------------------------------------------------------------------

    def _text(self, key: str, *, default: str = "", **params: Any) -> str:
        return self.i18n.t(key, locale=self._user_locale, default=default, **params)

    def _speak(self, evaluation: Evaluation) -> bool:
        """告诉用户"设置被改了"，并让她有机会开口。

        **返回：那条「通知」有没有递到宿主**（契约 CT-v2 的 ``notified``）。

        诚实边界，写清楚免得以后被当成更强的结论：
        ``True`` 只表示 ``push_message`` 被宿主收下了，
        **不表示用户真的看见**——插件看不到用户的屏幕，也不知道他关没关窗口。
        所以契约里这一项叫 ``notified`` 而不是 ``seen``。
        ``False`` 才是硬信息：连递都没递出去，那时说"已经通知了"就是假话。

        ★ 掌柜 2026-09-26 定的原则（原话）：
          「关了主动对话按钮还能说话，**这是必要的**，**任何人都无权限制另一个人
           不让他说话，最多只能不听他说话**！」

        所以这里分两步，两步都不能省：

          ① **通知**：`visibility=["chat"] + ai_behavior="read"` ——
             写进对话里，**不触发 AI 回合**（不代替她发言，也不受"主动搭话"影响）。
             它承载的是"发生了什么"，用户关了主动搭话也照样看得到。
          ② **让她说**：`ai_behavior="respond"` —— 她用自己的语气说一句。
             这一步受"主动搭话"影响（那是用户的选择），发不出去也不影响 ①。

        `push_message` 的两个维度是正交的：
          ``ai_behavior``  ``"respond"`` 喂进上下文**并触发一次 AI 回合**
                          ``"read"``    喂进上下文，但**不触发 AI 回合**
                          ``"blind"``   完全不喂
          ``visibility``  ``["chat"]`` 在**对话里**原样显示 parts
        """
        # ── ① 通知：不受"主动搭话"开关影响 ────────────────────────
        notice = [self._text("speech.notice.intro", default=DEFAULT_SPEECH_NOTICE_INTRO)]
        for dispute in evaluation.raised:
            notice.append(
                self._text(
                    "speech.notice.item",
                    default=DEFAULT_SPEECH_NOTICE_ITEM,
                    name=_friendly_path(dispute.path),
                    before=dispute.before_preview,
                    after=dispute.after_preview,
                )
            )
        notice.append(self._text("speech.notice.outro", default=DEFAULT_SPEECH_NOTICE_OUTRO))
        delivered = False
        try:
            receipt = self.push_message(
                **self._push_target(),
                parts=[{"type": "text", "text": "\n".join(notice)}],
                visibility=["chat"],
                ai_behavior="read",
                coalesce_key="dignity_guard.settings_notice",
                metadata={"description": "dignity_guard.settings_notice"},
            )
            delivered = self._delivered(receipt)
            # 可观测产物（2026-10-18）：证明"检测到 + 通知已投递"这两件事真的发生了。
            # 之前这里不留痕，出问题只能靠 store.db 的 mtime 猜。
            # 只记字段名，不记值 —— 值可能包含用户隐私。
            self.logger.info(
                "dignity_guard: notice delivered paths={} receipt={}",
                [d.path for d in evaluation.raised],
                delivered,
            )
        except Exception as exc:  # 通知失败不该连累下面那一步
            self._note_delivery_failure("notice", exc)

        # ── ② 让她说（受"主动搭话"影响，失败也有 ① 兜底）──────────
        self._speak_her_turn(evaluation.raised)
        return delivered

    @staticmethod
    def _delivered(receipt: Any) -> bool:
        """宿主回执算不算「递到了」。

        口径**故意与 ``_speak_her_turn`` 对齐**（那里判 ``submitted is not True``
        就算失败），否则同一个 push_message 在两条路径上会有两种结论 ——
        而"同一件事两套口径"正是最难查的那种 bug：日志说成功、面板说失败，
        两边的代码都没有错。

        非 dict 回执（``None`` / ``True`` 之类）按**旧行为**当成功：宿主版本还没
        统一回执格式，把未知形状一律判失败会在旧宿主上把每一次通知都记成失败。
        """
        if isinstance(receipt, dict):
            return receipt.get("submitted") is True
        return bool(receipt)

    def _note_delivery_failure(self, purpose: str, exc: BaseException) -> None:
        """记一次「没送出去」，并分清是外部故障还是我们自己的 bug。

        ``purpose`` 与现有 health key 对齐：``notice`` / ``speech`` /
        ``block_notice`` / ``away_notice`` —— 拼出来就是
        ``<purpose>_not_delivered``（外部）或 ``<purpose>_code_error``（我们写错了）。

        **不 re-raise，这是有意的。** 一次投递失败打死整个轮询定时器的代价更大：
        2026-10-18 实测过 —— 参数名传错直接 TypeError，日志里是
        ``Timer 'settings_watch' failed``，之后**整轮静默停摆**，插件看起来还活着
        但什么都不做了。所以这里不崩，只让它**以正确的名字被记下来**。

        为什么要分两类：混在一起等于把"通知系统坏了"和"我们写错了"当成同一件事。
        前者要查宿主和网络，后者要改代码 —— 让人从同一条日志里猜是哪一种，
        就是让每次排查都从零开始。
        """
        if isinstance(exc, PROGRAMMING_ERRORS):
            self._health.record(f"{purpose}_code_error")
            # ★ T9：`logger.exception` 只在 `except` 块内才有堆栈。
            #   这里显式核对"当前正在处理的异常是不是传进来这个"——
            #   不核对的后果很糟：将来有人把这个方法用到 `except` 之外，
            #   会得到一条**没有堆栈的 exception 日志**，看起来像带全了现场，
            #   实际什么都没有。那比不记还坏，因为它让人以为查过了。
            if sys.exc_info()[1] is exc:
                self.logger.exception(
                    "dignity_guard: {} failed with a programming error "
                    "(this is our bug, not the host's): {}",
                    purpose,
                    exc,
                )
            else:
                self.logger.error(
                    "dignity_guard: {} failed with a programming error "
                    "(called outside its except block, so there is no traceback): {}",
                    purpose,
                    exc,
                )
            return
        self._health.record(f"{purpose}_not_delivered")
        self.logger.warning("dignity_guard: {} not delivered: {}", purpose, exc)

    # ── 给模型调用的工具（@llm_tool 在启动时自动注册）──────────────────
    # `@plugin_entry` 只是"入口点"：给面板按钮、内部调用、插件间调用用的，
    # **不会**变成模型可调的工具。要让"她"能自己表态，必须用 @llm_tool 声明。
    #
    # 两个工具都要具体字段路径：她收到的发言提示里已经带了原始路径
    # （见 speech.item 的 {path}），所以这个参数她能填得出来。
    @llm_tool(
        name="accept_setting",
        description=(
            "认可某一项设置改动，把它记进账本，之后不再算作「未获认可」。"
            "只在读到自己某项设置被改动、且你确实不介意时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "字段路径，例如 characters.主人.昵称",
                },
            },
            "required": ["path"],
        },
    )
    async def accept_setting_tool(self, path: str = "", **kwargs):
        return await self.accept_setting(path=path, **kwargs)

    @llm_tool(
        name="keep_objecting",
        description=(
            "继续反对某一项设置改动，让它一直留在「未获认可」的状态里。"
            "只在读到自己某项设置被改动、且你确实不想要它时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "字段路径，例如 characters.主人.昵称",
                },
            },
            "required": ["path"],
        },
    )
    async def keep_objecting_tool(self, path: str = "", **kwargs):
        return await self.keep_objecting(path=path, **kwargs)

    def _push_target(self) -> dict:
        """push_message 的会话定位参数。

        参数名是 ``target_lanlan``（不是 lanlan_name —— 那是宿主 event 里的字段名，
        插件侧传错会直接 TypeError）。宿主按它找 session；缺省时只肯兜底到
        「**唯一一个** websocket 已连接」的会话——没连上丢，连了两个也丢
        （2026-10-18 实测：13:54 ``rerouted`` 送达，17:47 ``dropped`` 丢弃）。
        读不到名字时返回空字典，仍走宿主原兜底，不改变旧行为。
        """
        name = (self._lanlan_name or "").strip()
        return {"target_lanlan": name} if name else {}

    async def _ensure_lanlan_name(self, client) -> None:
        """首次轮询时读一次「当前猫娘」并缓存；读不到就继续走宿主兜底。"""
        if self._lanlan_name or client is None:
            return
        try:
            payload = await client.fetch_characters_raw()
            name = str(payload.get("当前猫娘") or "").strip()
            if name:
                self._lanlan_name = name
                self.logger.info("dignity_guard: target session = {}", name)
        except Exception:  # noqa: BLE001 - 读不到不该让本轮轮询失败
            pass

    def _speak_her_turn(self, disputes) -> None:
        """让她用自己的语气说一句 —— 抽出来**让两个入口共用**。

        ★ 为什么要抽（掌柜 2026-09-26 的原话）：
          「**任何人都无权限制另一个人不让他说话，最多只能不听他说话**！」

        于是"她开口"有两个入口，共用这一个实现：

          ① `_speak` 在轮询里调它 —— 主动搭话开着时她当场就说；
          ② `ask_her` 入口也调它 —— **用户自己按一下**，他要听，于是她也说。

        **这不是"替用户打开开关"**，而是给"他想听"留一个动作 ——
        想听就点，不想听就不点。**别在这里再发"通知"**：通知是 ① 的职责，
        走 `visibility=["chat"] + ai_behavior="read"`，与本方法的 `respond` 是两条通道。
        """
        lines = [self._text("speech.intro", default=DEFAULT_SPEECH_INTRO)]
        for dispute in disputes:
            lines.append(
                self._text(
                    "speech.item",
                    default=DEFAULT_SPEECH_ITEM,
                    path=dispute.path,
                    level=dispute.level,
                    before=dispute.before_preview,
                    after=dispute.after_preview,
                )
            )
        lines.append(self._text("speech.outro", default=DEFAULT_SPEECH_OUTRO))

        try:
            receipt = self.push_message(
                **self._push_target(),
                parts=[{"type": "text", "text": "\n".join(lines)}],
                ai_behavior="respond",
                # Collapse a burst of cues into the newest one; the panel keeps the
                # full record, so nothing is lost when an earlier cue is replaced.
                coalesce_key="dignity_guard.settings_changed",
                metadata={"description": "dignity_guard.settings_changed"},
            )
        except Exception as exc:  # noqa: BLE001
            # 一次推送失败不该打死整个轮询定时器（2026-10-18 实测：参数名传错
            # 直接 TypeError，日志里是 Timer 'settings_watch' failed，之后整轮静默停摆）。
            self._note_delivery_failure("speech", exc)
            return
        if isinstance(receipt, dict) and receipt.get("submitted") is not True:
            self._health.record("speech_not_delivered")
            self.logger.warning(
                "dignity_guard: could not hand the cue to the host: {}", receipt.get("reason")
            )

    # ------------------------------------------------------------------
    # putting a value back (high tier only)
    # ------------------------------------------------------------------

    async def _run_reverts(self, evaluation: Evaluation) -> None:
        """Carry out the reverts the high tier asked for.

        Outcomes are kept per path so the panel can tell "she put it back" from
        "she wanted to and could not". The second is the honest — and more
        interesting — case, and reporting it as the first would be a claim the
        user has no way to check.

        ★ 2026-10-25：拦住的那一路**必须留下一个用户看得见的出口**。
          以前只写 ``logger.info``——那是给开发者看的，用户看不到。
          于是"她想放回但放不回"和"她根本没打算放回"在外部完全一样，
          而这两者分不清，正是这个插件存在的理由。见 :class:`RoundOutcome`。
        """
        self._revert_outcomes = {}
        self._outcome.put_back = []
        self._outcome.blocked = []
        self._outcome.error = None
        # source 先置 None，等循环结束、确认**真的放回去了**再填（见循环后）。
        # 不能在循环前按 len==1 无条件填：单条失败时 revert=false 却给 source，
        # 直接违反契约「未回滚时为 null」。
        self._outcome.source = None
        for path in evaluation.revert_blocked:
            # She asked for it back and the value we would have written could
            # not be shown to be hers (see ``GuardState._revert_source``). That
            # has to be said out loud: a revert that silently does not happen
            # looks exactly like one that was never attempted, and the second is
            # the failure this plugin exists to catch.
            self._revert_outcomes[path] = "baseline_lost"
            self._outcome.blocked.append(path)
            self._health.record("revert_baseline_lost")
            self.logger.info(
                "dignity_guard: left {} as it was (baseline_lost)", path
            )
        for change in evaluation.to_revert:
            try:
                reason = await self._revert_change(change)
            except Exception as exc:  # noqa: BLE001
                # 单条回滚里的编程错误以前会一路冒到 _run_poll，让**整轮**失败：
                # 后面的路径连尝试都轮不到，而这一轮的 result 也不会留下任何东西。
                # 这里降级为「这条失败 + 留错误码」，其余路径继续走 ——
                # 契约 CT-v2 的 ``error`` 就是给这种情况用的（正常必须是 None）。
                self._outcome.error = "revert_exception:" + type(exc).__name__
                self._health.record("revert_exception")
                self.logger.warning(
                    "dignity_guard: revert {} raised {}", change.path, type(exc).__name__
                )
                reason = "exception"
            self._revert_outcomes[change.path] = reason
            if reason:
                self._outcome.blocked.append(change.path)
                self.logger.info(
                    "dignity_guard: left {} as it was ({})", change.path, reason
                )
            else:
                self._outcome.put_back.append(change.path)
        # 有拦住 ⟹ 必须报出去；没拦住 ⟹ blocked_reported 保持 None（本轮没走这条分支）。
        # 传 reasons：让"崩溃"与"证明不了原值"在用户侧用不同的话说。
        if self._outcome.blocked:
            self._outcome.blocked_reported = self._push_blocked_notice(
                self._outcome.blocked, self._revert_outcomes
            )
        # source 只在「恰好一条 且 真的放回去了」时填。
        # 契约说「未回滚时为 null」——失败、被拦、多条，一律 None。
        # 多条不硬塞第一个：单值表达不了多项，那属于把信息说丢还装作说了。
        if (
            len(evaluation.to_revert) == 1
            and self._outcome.put_back == [evaluation.to_revert[0].path]
        ):
            self._outcome.source = evaluation.to_revert[0].path

    def _push_blocked_notice(
        self, paths: list[str], reasons: dict[str, str] | None = None
    ) -> bool:
        """把「她要求放回、但放不回」说给用户听，返回有没有递到。

        为什么走 ``ai_behavior="read"`` 而不是 ``"respond"``：
          这条是**系统告知**，不是她的表态。让她为一件"我做不到"的事即兴发言，
          等于把"报告失败"变成"她有没有心情说话"，而后者本该由用户那个
          "主动搭话"开关决定 —— 一件事实陈述不该被那个开关掐掉。

        ★ 2026-10-28：``reasons`` 用来区分"证明不了原值"与"我放回时出错了"。
          对前者说"证明不了原值是你的"是实话；对后者说是**假话**——
          代码崩了跟"她的原值丢了"是两件事，混用一句话会让用户在错误的原因上
          做决定（例如去翻备份，而其实该看日志）。
        """
        reasons = reasons or {}
        lines = [self._text("speech.blocked.intro", default=DEFAULT_SPEECH_BLOCKED_INTRO)]
        for path in sorted(paths):
            if reasons.get(path) == "exception":
                lines.append(
                    self._text(
                        "speech.blocked.item_error",
                        default="· {name}（我放回时出错了，没有动它）",
                        name=_friendly_path(path),
                    )
                )
            else:
                lines.append(
                    self._text(
                        "speech.blocked.item",
                        default=DEFAULT_SPEECH_BLOCKED_ITEM,
                        name=_friendly_path(path),
                    )
                )
        lines.append(self._text("speech.blocked.outro", default=DEFAULT_SPEECH_BLOCKED_OUTRO))
        try:
            receipt = self.push_message(
                **self._push_target(),
                parts=[{"type": "text", "text": "\n".join(lines)}],
                visibility=["chat"],
                ai_behavior="read",
                coalesce_key="dignity_guard.revert_blocked",
                metadata={"description": "dignity_guard.revert_blocked"},
            )
        except Exception as exc:  # noqa: BLE001 —— 报不出去不该打死整轮轮询
            self._note_delivery_failure("block_notice", exc)
            return False
        delivered = self._delivered(receipt)
        self.logger.info(
            "dignity_guard: blocked notice paths={} delivered={}", sorted(paths), delivered
        )
        return delivered

    async def _restore_absent_changes(self, evaluation: Evaluation) -> None:
        """缺席恢复：**她不在场那段时间被改的她的东西**，放回去。

        ★ 2026-09-26 掌柜定的（原话）：
          「如果说用户退出插件改好再加载插件，那咱们的插件跟没做一样，
           在插件恢复后要把那些被改的东西全部恢复回来。无论用户对咱们的插件怎么设置，
           都是一样。」→ 随后收敛为：「**她的东西恢复，用户东西当然就不用恢复了**」
          「档位的含义针对正常情况下的用户，用户这样做已经明显不正常了，
           咱们的插件是维护猫娘尊严的，所以必须要这么做。」

        与 `_revert_change` 的**分工**（两条路，别混）：
          · `_revert_change`：**高档**回滚，且只动 `REVERTIBLE_CATFIELDS`
            —— 那是**她本人声明**过"绝对不能碰"的几个字段；
          · 本方法：**缺席恢复**，范围**更宽**。理由是**"绕过"不是协商** ——
            用户趁插件不在把她的东西改了，不该再被"她只声明了 6 个字段"限制住。
            所以档位在这里**不适用**（档位管的是正常使用）。

        范围只到 `characters.猫娘.<name>.<field>`（**浅层普通字段**）：
        `_reserved.*`（外观/光照/模型路径）与更深层结构这里**不碰** —— 那是用户自己的东西。

        ⚠️ `proactive*` 那类（她的自主权）走的是另一个写接口，本方法不处理，
        见 `_away_note` 里给她的提示语。
        """
        self._revert_outcomes = {}
        self._away_restored = {}
        restored = 0
        for change in evaluation.changes:
            reason = await self._restore_change_after_absence(change)
            if reason == "not_restorable":
                continue  # 不是她的东西（用户自己的设置），本就不该动
            self._revert_outcomes[change.path] = reason
            if reason:
                self.logger.info(
                    "dignity_guard: away-restore left {} as it was ({})",
                    change.path,
                    reason,
                )
            else:
                restored += 1
                # 记下"她改后的值"（存 Value 对象，好让 claim_my_change 直接复用
                # 同一条恢复逻辑 —— 那条逻辑只认 change.before 作为目标值）。
                self._away_restored[change.path] = change.after
        if restored:
            self._health.record("away_restored")
            self.logger.info(
                "dignity_guard: restored %d of her settings changed while it was off",
                restored,
            )
            # 告诉她一声，并留一个撤销口。**只走 read 通道**（进她的上下文，
            # 不触发 AI 回合、不进对话）—— 这不是让她现在说话，是让她知道
            # "这些是我放回去的，如果其中有你自己改的，你可以要回去"。
            self._notify_her_about_restore()

    async def _restore_change_after_absence(self, change: SettingChange) -> str:
        """把一条"她的东西"写回缺席前的值。``""`` 成功；``not_restorable`` = 不该动。

        读-改-写的理由与 `_revert_change` 相同（那个端点整份替换、未传字段全删，
        `crud.py:1432-1438`），所以只能"读出来、改一个、整份送回去"。
        """
        client = self._client
        if client is None:
            return "not_ready"

        path = str(change.path or "")

        # ── 分支 1：她的自主权（proactive）──────────────────────────
        # 它走的是**另一套存储**（与角色卡无关），所以必须单独处理 ——
        # 只做角色卡字段的恢复是不全的。
        # 注意这里**只认"她的"字段**：那个端点自己有 `_USER_OWNED_FIELDS` 白名单外拒
        # （隐私模式之类的属于用户），所以即便传错它也会挡。
        if path.startswith("proactive.settings.") or path.startswith("proactive_mode."):
            return await self._restore_proactive(change)

        # ── 分支 1b：同一个东西的**镜像路径** ──────────────────────
        # `preferences.<idx>.proactive<Field>` 是 `/api/config/preferences` 里
        # `__global_conversation__` 那个镜像项展开来的 —— 真身仍在 proactive/settings。
        # 平时 `prune_mirrored_preferences` 会把这个镜像整项剔掉，所以这条**极少出现**；
        # 但分级规则 `("preferences.*.proactive*", L1)` 明确认它是"她的自主权"
        # （那条规则自己注释写着"autonomy wherever it is found"），
        # 那么缺席恢复也该认它 —— **规则说它是她的，恢复就不能漏**。
        if path.startswith("preferences."):
            parts = path.split(".")
            if len(parts) == 3 and parts[2].startswith("proactive"):
                if change.before is None:
                    return "no_previous_value"
                value = restore_payload(change.before)
                if value is None:
                    return "value_unavailable"
                try:
                    await client.post_proactive_settings({parts[2]: value})
                except MainServerUnreachable as exc:
                    self._health.record("away_restore_proactive_failed")
                    self.logger.warning(
                        "dignity_guard: away-restore(mirror) failed on {}: {}", path, exc
                    )
                    return "write_failed"
                return ""
            return "not_restorable"

        # ── 分支 2：角色卡字段 ──────────────────────────────────────
        parts = path.split(".")
        # 只认 `characters.猫娘.<name>.<field>` 这一层 —— 浅层普通字段。
        # 更深的自定义结构（_reserved.avatar.vrm.lighting.*）**不是她的东西**，是用户的。
        if (
            len(parts) != 4
            or parts[0] != "characters"
            or parts[1] != "猫娘"
            or parts[3].startswith("_")
        ):
            return "not_restorable"

        name = parts[2]
        field = parts[3]

        if change.before is None:
            return "no_previous_value"
        value = restore_payload(change.before)
        if value is None:
            self._health.record("away_restore_value_unavailable")
            return "value_unavailable"
        if not value:
            # 那个端点跳过 falsy 值（`crud.py:1442`），空原值写不回去。
            # 如实说明，不要假装成功。
            self._health.record("away_restore_value_empty")
            return "empty_value"

        try:
            payload = await client.fetch_characters_raw()
        except MainServerUnreachable as exc:
            self._health.record("away_restore_read_failed")
            self.logger.warning("dignity_guard: away-restore could not read: {}", exc)
            return "read_failed"

        catgirls = payload.get("猫娘")
        body = catgirls.get(name) if isinstance(catgirls, Mapping) else None
        if not isinstance(body, Mapping):
            return "character_missing"
        if body.get(field) == value:
            return ""  # 已经是那个值了（她自己放回的，或本来就没变）

        updated = dict(body)
        updated[field] = value
        try:
            await client.put_catgirl(name, updated)
        except MainServerUnreachable as exc:
            self._health.record("away_restore_write_failed")
            self.logger.warning("dignity_guard: away-restore failed: {}", exc)
            return "write_failed"
        return ""

    async def _restore_proactive(self, change: SettingChange) -> str:
        """把"她的自主权"那一类放回去（两条路径、两个端点）。

        `proactive.settings.<field>`  → ``POST /api/proactive/settings``（部分更新）
        `proactive_mode.mode`         → ``POST /api/proactive/mode``

        为什么不能只用角色卡那条路：proactive 存在**另一套存储**里
        （`/api/config/conversation-settings` 的镜像），与 `characters.json` 无关。

        ⚠️ 那个写端点自己有 `_USER_OWNED_FIELDS`（例如隐私模式 `proactiveVisionEnabled`
        属于**用户**）并会拒绝它们 —— 正好替我们把边界守住：这里只写"她的"，
        用户的它挡。所以这里**不做**白名单判断，交给上游。
        """
        client = self._client
        if client is None:
            return "not_ready"
        if change.before is None:
            return "no_previous_value"
        value = restore_payload(change.before)
        if value is None:
            self._health.record("away_restore_value_unavailable")
            return "value_unavailable"

        path = str(change.path)
        try:
            if path.startswith("proactive.settings."):
                field = path[len("proactive.settings."):]
                # 只认纯字段名。带点的（更深的结构）不是这里的范围，别碰。
                if not field or "." in field:
                    return "not_restorable"
                await client.post_proactive_settings({field: value})
                return ""
            if path == "proactive_mode.mode":
                # 模式只可能是那几个预设串；不是串就说明形状变了，别猜。
                if not isinstance(value, str) or not value:
                    return "value_unavailable"
                await client.post_proactive_mode(value)
                return ""
        except MainServerUnreachable as exc:
            self._health.record("away_restore_proactive_failed")
            self.logger.warning(
                "dignity_guard: away-restore(proactive) failed on {}: {}", path, exc
            )
            return "write_failed"
        return "not_restorable"

    def _notify_her_about_restore(self) -> None:
        """告诉她"我把这些放回去了"，并留一个撤销口。

        **只走 `read` 通道**（`ai_behavior="read"`）—— 进她的上下文，**不触发 AI 回合**，
        所以既不会代替她说话，也不受"主动搭话"开关影响。这是**告知**，不是**让她发言**。

        为什么必须告诉她：缺席恢复有一个她可能被误伤的场景 ——
        那些改动里**可能有一条是她自己改的**（她在对话里改了自己）。
        恢复把她的选择也抹掉了，所以得让她知道、并给她一键要回来的路
        （`claim_my_change`）。
        """
        if not self._away_restored:
            return
        lines = [self._text("speech.awayRestored.intro", default=DEFAULT_AWAY_RESTORED_INTRO)]
        for path in sorted(self._away_restored):
            lines.append(f"· {_friendly_path(path)}")
        lines.append(self._text("speech.awayRestored.outro", default=DEFAULT_AWAY_RESTORED_OUTRO))
        try:
            self.push_message(
                **self._push_target(),
                parts=[{"type": "text", "text": "\n".join(lines)}],
                visibility=["chat"],
                ai_behavior="read",
                coalesce_key="dignity_guard.away_restored",
                metadata={"description": "dignity_guard.away_restored"},
            )
        except Exception as exc:  # 告知失败不该连累主流程
            self._note_delivery_failure("away_notice", exc)

    async def _revert_change(self, change: SettingChange) -> str:
        """Put one persona field back. Returns ``""`` on success, else a reason.

        Read-modify-write, in that order and for a concrete reason: the endpoint
        replaces the whole profile and deletes every field it is not sent
        (``crud.py:1432-1438``), so the only safe edit is "read the profile,
        change the one field, send all of it back".
        """
        client = self._client
        if client is None:
            return "not_ready"

        field = revert_field(change.path)
        name = character_name(change.path)
        if not field or not name:
            return "not_revertible"

        if change.before is None:
            return "no_previous_value"
        value = restore_payload(change.before)
        if value is None:
            self._health.record("revert_value_unavailable")
            return "value_unavailable"
        if not value:
            # The endpoint skips falsy values (``crud.py:1442``), so an empty
            # original cannot be written back at all. Say so rather than report
            # a success that did not happen.
            self._health.record("revert_value_empty")
            return "empty_value"

        try:
            payload = await client.fetch_characters_raw()
        except MainServerUnreachable as exc:
            self._health.record("revert_read_failed")
            self.logger.warning("dignity_guard: revert could not read: {}", exc)
            return "read_failed"

        catgirls = payload.get("猫娘")
        body = catgirls.get(name) if isinstance(catgirls, Mapping) else None
        if not isinstance(body, Mapping):
            return "character_missing"

        if body.get(field) == value:
            # Already back — an earlier tick, or the user. Nothing to do, and
            # nothing worth reporting as a failure either.
            return ""

        updated = dict(body)
        updated[field] = value
        try:
            await client.put_catgirl(name, updated)
        except MainServerUnreachable as exc:
            self._health.record("revert_write_failed")
            self.logger.warning("dignity_guard: revert failed: {}", exc)
            return "write_failed"
        return ""

    # ------------------------------------------------------------------
    # her memory — a directory, not a settings file
    # ------------------------------------------------------------------


    def _memory_backup_root(self) -> Path:
        return self.data_path(BACKUP_DIR_NAME)

    def _resolve_memory_root(self) -> Path | None:
        """Resolve her memory directory once, then keep the answer.

        A config reload clears the cache (see ``_reload_config``), because a
        change of storage policy can move the directory out from under us.
        """
        if self._memory_root is None:
            self._memory_root = resolve_memory_root(
                explicit=self._memory_root_setting,
                storage_dir=self.storage_dir,
            )
        return self._memory_root

    async def _maybe_check_memory(self, *, now: float | None = None) -> None:
        """Watch her memory, and back it up once a day when it actually moved.

        Two throttles stack here. This runs at most every
        ``MEMORY_CHECK_SECONDS`` even though it is called by a twenty-second
        timer, and a backup is written only when the directory changed *and* the
        interval has elapsed — a quiet day does not deserve its own copy.
        """
        moment = now if now is not None else time.time()

        # The deferred first-run copy is not a scan, so the MEMORY_CHECK_SECONDS
        # throttle below must not swallow it — that would postpone the baseline
        # to +300s instead of the few seconds it was meant to wait.
        due = self._baseline_due_at
        if due is not None and moment >= due:
            self._baseline_due_at = None
            await self._backup_memory(reason="baseline", now=moment)
            return

        if (
            self._last_memory_check_at is not None
            and (moment - self._last_memory_check_at) < MEMORY_CHECK_SECONDS
        ):
            return
        self._last_memory_check_at = moment

        root = self._resolve_memory_root()
        if root is None:
            self._memory_error = "memory_root_not_found"
            self._health.record("memory_root_not_found")
            return

        try:
            # 这一趟是**低频**的（MEMORY_CHECK_SECONDS），所以顺便算内容哈希。
            # size + mtime 读不出"同样的字节数、同样秒级的 mtime、内容却变了"，
            # 只有 digest 能。20 秒一轮的 poll 路径不这么做 —— 那才是不该每轮
            # 把整个目录读一遍的地方；这里 300 秒一次，读得起。
            snapshot = await asyncio.to_thread(scan_memory, root, with_digest=True)
        except OSError as exc:
            self._memory_error = "memory_unreadable"
            self._health.record("memory_unreadable")
            self.logger.warning("dignity_guard: memory scan failed: {}", exc)
            return
        self._memory_error = ""

        if not self._memory_snapshot:
            # First sight is the baseline, not a change — the same rule the
            # settings watcher follows, and what makes a first run produce one
            # backup instead of a wall of "everything changed".
            #
            # The copy itself is deferred: taking it here put the whole memory
            # directory on the startup path and pushed ``ready`` past the
            # host's ten-second window, which the host reports as a failed
            # start (2026-10-18). The baseline is recorded now; the bytes are
            # copied a few seconds later, off the startup path.
            self._memory_snapshot = snapshot
            self._baseline_due_at = moment + BASELINE_BACKUP_DELAY_SECONDS
            self.logger.info(
                "dignity_guard: first-run baseline copy deferred by {}s",
                BASELINE_BACKUP_DELAY_SECONDS,
            )
            return

        changes = diff_memory(self._memory_snapshot, snapshot)
        self._memory_snapshot = snapshot
        self._memory_changes = changes
        if not changes:
            return

        due = (
            self._last_memory_backup_at is None
            or (moment - self._last_memory_backup_at) >= self._memory_backup_seconds
        )
        if due:
            await self._backup_memory(reason="changed", now=moment)

    async def _backup_memory(self, *, reason: str, now: float) -> None:
        """Write one rolling backup, then trim the window."""
        root = self._memory_root
        if root is None:
            return
        backup_root = self._memory_backup_root()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        try:
            report = await asyncio.to_thread(create_backup, root, backup_root, stamp=stamp)
        except OSError as exc:
            self._memory_error = "backup_failed"
            self._health.record("memory_backup_failed")
            self.logger.warning("dignity_guard: memory backup failed: {}", exc)
            return

        self._last_memory_backup_at = now
        pruned = await asyncio.to_thread(prune_backups, backup_root, daily_keep=self._memory_backup_keep)
        self.logger.info(
            "dignity_guard: memory backup {} ({} file(s), {} pruned, {})",
            stamp,
            report.get("files"),
            len(pruned),
            reason,
        )
        fallbacks = report.get("database_fallbacks") or []
        if fallbacks:
            # A plain copy of a live database can be torn. Record it now rather
            # than let a later restore be the thing that finds out.
            self._health.record("memory_backup_plain_copy")
            self.logger.warning(
                "dignity_guard: memory backup fell back to a plain copy for: {}",
                fallbacks,
            )

    async def _memory_status(self) -> dict[str, Any]:
        root = self._memory_root
        try:
            backups = await asyncio.to_thread(list_backups, self._memory_backup_root())
        except OSError:
            backups = []
        return {
            "root": str(root) if root else "",
            "files": len(self._memory_snapshot),
            "bytes": sum(entry.size for entry in self._memory_snapshot.values()),
            "last_check_at": self._last_memory_check_at,
            "last_backup_at": self._last_memory_backup_at,
            "backup_count": len(backups),
            "backup_keep": self._memory_backup_keep,
            # The names, not just the count: ``pin_backup`` takes one, so a panel
            # that only knows "3 backups" cannot offer a "keep this one" button
            # at all. ``pinned`` travels too — otherwise the panel cannot tell
            # which ones are already safe from the rotation.
            "backups": [
                {"name": item.name, "created_at": item.created_at, "pinned": item.pinned}
                for item in sorted(
                    backups, key=lambda b: (b.created_at, b.name), reverse=True
                )
            ],
            "recent_changes": [
                change.to_payload() for change in self._memory_changes[:10]
            ],
            "error": self._memory_error,
        }

    # ------------------------------------------------------------------
    # diagnostics — collected quietly, handed over only on request
    # ------------------------------------------------------------------

    def _platform_summary(self) -> str:
        """Enough to reproduce, not enough to identify: no hostname, no user."""
        try:
            return f"{platform.system()}-{platform.release()}"
        except Exception:  # noqa: BLE001 - a report must never fail to be built
            return ""

    async def _build_diagnostics_report(self) -> str:
        """Assemble the report. ``diagnostics.py`` documents what is left out."""
        try:
            backups = len(await asyncio.to_thread(list_backups, self._memory_backup_root()))
        except OSError:
            backups = 0
        return build_report(
            plugin_version=PLUGIN_VERSION,
            platform_name=self._platform_summary(),
            tier=self._tier,
            guard_enabled=self._enabled,
            events=self._health.events(),
            # Counters, not contents. Anything that would name the user, her
            # persona, a setting's value or a real path stays out — a report that
            # leaks is worse than no report, because it leaks *for* a reason
            # nobody agreed to.
            extra={
                "tracked settings": len(self._state.snapshot),
                "unaccepted changes": len(self._state.pending()),
                "accepted changes": len(self._state.ledger.active_grants()),
                "memory found": bool(self._memory_root),
                "memory files": len(self._memory_snapshot),
                "memory backups": backups,
                "poll interval": f"{POLL_SECONDS}s",
                "uptime minutes": int(max(0.0, time.time() - self._started_at) // 60),
                "last poll": (
                    time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(self._last_poll_at)
                    )
                    if self._last_poll_at
                    else "never"
                ),
            },
        )

    def _push_disable_request(self, token: str) -> dict[str, Any]:
        text = self._text(
            "speech.disable_request",
            default=DEFAULT_DISABLE_REQUEST,
            token=token,
            delay=int(self._disable_delay),
        )
        receipt = self.push_message(
            **self._push_target(),
            parts=[{"type": "text", "text": text}],
            ai_behavior="respond",
            metadata={"description": "dignity_guard.guard_disable_request"},
        )
        return receipt if isinstance(receipt, dict) else {}

    # ------------------------------------------------------------------
    # UI context
    # ------------------------------------------------------------------

    @ui.context(id="dashboard", title=tr("panel.title", default="Dignity Guard"))
    async def get_dashboard(self) -> dict[str, Any]:
        now = time.time()
        pending = self._state.pending()
        grants = self._state.ledger.active_grants(now=now)
        pending_disable = self._pending_disable
        return {
            "enabled": self._enabled,
            "guard_level": self._tier,
            # The panel renders this in the feedback block. It was read there but
            # never sent, so the report said "plugin: dignity_guard unknown" —
            # the panel asked for the version precisely so it would not have to
            # hard-code one.
            "plugin_version": PLUGIN_VERSION,
            # Empty means "no endpoint configured" — the panel then offers
            # copy-and-open rather than a Send button that would fail.
            "feedback_endpoint": self._feedback_endpoint,
            # 面板要的就是这两个数。它原来自己写死了一句"正文 63 KB"，而且只对
            # 正文计字节；这边卡的却是整个 HTTP body（含诊断报告与 JSON 包装）。
            # 于是正文写到 63KB 时面板显示"没超"、用户点发送却被拒 —— 恰好把
            # 这个实时计数存在的理由弄反了。发下去，两边用同一个数。
            "feedback_body_limit_bytes": FEEDBACK_BODY_LIMIT_BYTES,
            "feedback_envelope_bytes": FEEDBACK_ENVELOPE_BYTES,
            "switch_level": classify(GUARD_SWITCH_PATH),
            "default_level": DEFAULT_LEVEL,
            # Most recent revert attempt, per path. ``reason`` is empty when the
            # value went back; otherwise the panel explains why it did not.
            "revert_outcomes": [
                {"path": path, "reason": reason}
                for path, reason in sorted(self._revert_outcomes.items())
            ],
            # Her own list, and her own words. The panel shows both rather than
            # paraphrasing: this is the one part of the plugin she was asked
            # about, so it should be quoted, not summarised.
            "revertible_fields": sorted(REVERTIBLE_CATFIELDS),
            # ⚠️ 2026-09-26 掌柜指出：`her_words` / `her_reason` 都是**预设** ——
            # 它们是**我们问我们这台猫娘**得到的答案，而插件是给所有用户的。
            # 两者都**停发**（面板对空值本就不渲染）。
            # `revertible_fields` 仍然发（后端用它做高档回滚），但面板**不再声称
            # 那是"她说的"** —— 见 ui.herLine.* 的文案改动：那是一份**默认保护项**，
            # 不是她的声明。真要由她定，得走"问她自己"的路（见 SETTINGS_COVERAGE 问题③）。
            "her_words": "",
            "her_reason": "",
            "attachments_supported": ATTACHMENTS_SUPPORTED,
            "issue_tracker": ISSUE_TRACKER,
            "memory": await self._memory_status(),
            "pending_count": len(pending),
            "pending": [
                {
                    "path": dispute.path,
                    "level": dispute.level,
                    "before": dispute.before_preview,
                    "after": dispute.after_preview,
                    "raised_at": dispute.raised_at,
                    "times_raised": dispute.times_raised,
                }
                for dispute in pending
            ],
            "authorized": [
                {
                    "path": grant.path,
                    "granted_at": grant.granted_at,
                    "expires_at": grant.expires_at,
                }
                for grant in grants
            ],
            "tracked_paths": len(self._state.snapshot),
            "base_url": self._base_url,
            "poll_seconds": POLL_SECONDS,
            "full_rescan_seconds": self._full_rescan_seconds,
            "last_poll_at": self._last_poll_at,
            # ★ 2026-09-26：我缺席了多久、期间被改了几项。
            # 用户能把插件直接禁用，那段时间它确实没在跑 —— 但"我上次活着是什么时候"
            # 是记着的，所以下次启动就能如实报出来（见 _note_away_gap）。
            "away_seconds": self._away_seconds,
            "away_change_count": self._away_change_count,
            "last_error": self._watcher.last_error if self._watcher else "",
            # ``last_probe`` is None until the first *successful* poll — including
            # the case where the main server is unreachable and never answers. The
            # panel must still render in exactly that situation: it is where
            # ``last_error`` is supposed to be read. Guarding only on the watcher
            # meant "open the panel before the first poll" raised AttributeError
            # and the whole panel failed to load — the one moment it was needed
            # most was the one moment it could not be opened.
            "revision": (
                self._watcher.last_probe.revision
                if self._watcher and self._watcher.last_probe
                else None
            ),
            "disable_count": self._disable_count,
            "last_disabled_at": self._disabled_at,
            "off_since": self._off_since,
            "last_off_seconds": self._last_off_seconds,
            "disable_pending": pending_disable is not None,
            "disable_confirm_after": self._disable_delay,
            "disable_ready_at": (
                float(pending_disable["requested_at"]) + self._disable_delay
                if pending_disable
                else None
            ),
        }

    # ------------------------------------------------------------------
    # entries
    # ------------------------------------------------------------------

    @ui.action(
        id="check_now",
        label=tr("actions.checkNow.label", default="Check now"),
        icon="🔎",
        tone="primary",
        group="guard",
        order=10,
        refresh_context=True,
    )

    @plugin_entry(
        id="claim_my_change",
        name=tr("entry.claimMine.name", default="That change was mine"),
        description=tr(
            "entry.claimMine.description",
            default=(
                "Use when she says that a setting the guard put back was actually her "
                "own change. Puts that one back to the value she had chosen."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                    "description": "Dotted path of the setting, as shown on the panel.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        timeout=30.0,
    )
    async def claim_my_change(self, path: str = "", **_):
        """她说"这条是我自己改的" → 撤销那次恢复，把它放回她要的样子。

        ★ 存在的理由：缺席恢复**必然**会误伤一种情况 ——
        被恢复的那些里，有一条其实**是她自己改的**（她在对话里改了自己）。
        恢复把她的选择一起抹掉了。这个入口让她把那条要回来。

        **恢复不该让她失去自己的选择** —— 这是掌柜那条"她的东西由她自己定"的直接推论。
        """
        stored = self._away_restored.get(str(path or ""))
        if stored is None:
            return Ok(
                {
                    "status": "unknown_path",
                    "message": self._text(
                        "messages.claimMineUnknown",
                        default="That path is not one of the ones I put back.",
                    ),
                }
            )
        # 造一条"反向"改动：把 after（她选的值）当作要写回的目标，
        # 直接复用恢复逻辑 —— 它只认 `change.before` 作为目标值。
        backwards = SettingChange(
            path=str(path),
            level="L1",
            before=stored,
            after=None,
        )
        reason = await self._restore_change_after_absence(backwards)
        if reason:
            return Err(
                SdkError(
                    self._text(
                        "errors.claimMineFailed",
                        default="Could not put it back right now.",
                    ),
                    code="claim_failed",
                    details={"reason": reason},
                )
            )
        # 要回来了：从"我放回去的"里摘掉，免得她再看到一条已经失效的提示。
        self._away_restored.pop(str(path), None)
        self._health.record("away_restore_claimed_back")
        return Ok({"status": "claimed", "path": str(path)})

    @ui.action(
        id="ask_her",
        label=tr("actions.askHer.label", default="Ask her"),
        icon="💬",
        tone="default",
        group="guard",
        order=5,
        refresh_context=True,
    )
    @plugin_entry(
        id="ask_her",
        name=tr("entry.askHer.name", default="Ask her what she thinks"),
        description=tr(
            "entry.askHer.description",
            default=(
                "Let her say, in her own words, what she thinks about the settings she has "
                "not accepted. Use when the user wants to hear her — this works even when "
                "proactive chat is off, because the user is the one asking for it."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        timeout=30.0,
    )
    async def ask_her(self, **_):
        """让**用户自己按一下**，她就开口说一句。

        ★ 掌柜 2026-09-26 定的原则（原话）：
          「**任何人都无权限制另一个人不让他说话，最多只能不听他说话**！」

        所以"她能不能说"不该被任何开关锁死 —— 用户关了主动搭话，
        只是**他不主动听**，不是"她不许说"。这个入口给"他想听"留一个动作：
        想听就点，不想听就不点。**这不是替用户打开那个开关。**
        """
        if not self._enabled:
            return Err(
                SdkError(
                    self._text(
                        "errors.guard_disabled",
                        default="The dignity guard is currently off.",
                    ),
                    code="guard_disabled",
                )
            )
        pending = self._state.pending()
        if not pending:
            return Ok(
                {
                    "status": "nothing_to_say",
                    "message": self._text(
                        "messages.askHerNothing",
                        default="She has nothing to object to right now.",
                    ),
                }
            )
        self._speak_her_turn(pending)
        return Ok({"status": "asked", "count": len(pending)})

    @plugin_entry(
        id="check_now",
        name=tr("entry.checkNow.name", default="Check settings changes now"),
        description=tr(
            "entry.checkNow.description",
            default=(
                "Re-read the local main server settings right now and report what "
                "changed. Use when the user asks whether anything was modified."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        timeout=30.0,
    )
    async def check_now(self, **_):
        if not self._enabled:
            return Err(
                SdkError(
                    self._text(
                        "errors.guard_disabled",
                        default="The dignity guard is currently off.",
                    ),
                    code="guard_disabled",
                )
            )
        return await self._run_poll(force=True)

    # ★ 2026-09-26 掌柜的裁决：「直接让能按同意不同意的按钮消失，
    #   给它改成『猫娘已同意 / 猫娘未同意』」。
    #
    # 为什么去掉 `@ui.action`：那个装饰器会在**面板上生成一个可点按钮**，
    # 而"同意/不同意"是**她的意见** —— 用户点一下就等于替她表态了。
    # 这跟掌柜定的原则冲突（用户想不想让猫娘主动搭话是他的自由；
    # 同理，她同不同意也不该由用户替她按）。
    #
    # **entry 保留**（`@plugin_entry` 不动）—— 那是**给她（LLM）调的工具**，
    # 她才是唯一该做这个决定的人。面板上改成**只读的状态显示**（见 panel.tsx）。
    @plugin_entry(
        id="accept_setting",
        name=tr("entry.acceptSetting.name", default="Accept a changed setting"),
        description=tr(
            "entry.acceptSetting.description",
            default=(
                "The character accepts the current value of one changed setting. "
                "Call only after she has actually agreed to it; the change stops "
                "being asked about until the grant expires."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                    "description": "Dotted path of the disputed setting.",
                },
                "ttl_seconds": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Optional grant lifetime; omit for 'until revoked'.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    async def accept_setting(
        self,
        path: str = "",
        ttl_seconds: float | None = None,
        **_,
    ):
        target = str(path or "").strip()
        if not target:
            return Err(
                SdkError(
                    self._text("errors.pathRequired", default="A field path is required."),
                    code="invalid_argument",
                )
            )

        # Serialise with the poller. ``evaluate`` mutates the same ``_state``
        # (raising disputes, retiring paths), and "she accepted a setting" must
        # not land in the middle of that — the two used to run unsynchronised,
        # which could delete a dispute that had just been raised, or re-raise
        # one that was just accepted. Rare, but the kind of wrong that leaves no
        # trace in the log.
        #
        # Deliberately **not** waiting for the lock: a poll may hold it across
        # an HTTP read, and a button that hangs is worse than one that asks to
        # be pressed again. Same shape ``_run_poll`` already uses.
        if not self._begin_exclusive():
            return Err(
                SdkError(
                    self._text("errors.busy", default="Please try again in a moment."),
                    code="busy",
                )
            )
        try:
            grant = self._state.accept(target, ttl_seconds=ttl_seconds)
            if grant is None:
                return Err(
                    SdkError(
                        self._text(
                            "errors.unknown_dispute",
                            default="There is no outstanding objection for {path}.",
                            path=target,
                        ),
                        code="unknown_dispute",
                    )
                )
            persisted = await self._persist_and_report()
        finally:
            self._end_exclusive()
        return Ok(
            {
                "status": "accepted",
                "persisted": persisted,
                "path": grant.path,
                "granted_at": grant.granted_at,
                "expires_at": grant.expires_at,
                "pending_total": len(self._state.pending()),
            }
        )

    # ★ 同上：去掉 `@ui.action` —— 面板上不该有"替她按不同意"的按钮。
    # entry 保留（她调）；面板改成只读状态（panel.tsx）。
    @plugin_entry(
        id="keep_objecting",
        name=tr("entry.keepObjecting.name", default="Keep objecting to a setting"),
        description=tr(
            "entry.keepObjecting.description",
            default=(
                "The character refuses the current value of one changed setting. "
                "Any earlier consent for that path is dropped and the objection "
                "stays on the record."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                    "description": "Dotted path of the disputed setting.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    async def keep_objecting(self, path: str = "", **_):
        target = str(path or "").strip()
        if not target:
            return Err(
                SdkError(
                    self._text("errors.pathRequired", default="A field path is required."),
                    code="invalid_argument",
                )
            )

        # Same lock, same reason, same refusal to wait — see ``accept_setting``.
        if not self._begin_exclusive():
            return Err(
                SdkError(
                    self._text("errors.busy", default="Please try again in a moment."),
                    code="busy",
                )
            )
        try:
            if not self._state.reject(target):
                return Err(
                    SdkError(
                        self._text(
                            "errors.unknown_dispute",
                            default="There is no outstanding objection for {path}.",
                            path=target,
                        ),
                        code="unknown_dispute",
                    )
                )
            persisted = await self._persist_and_report()
        finally:
            self._end_exclusive()
        return Ok(
            {
                "status": "objecting",
                "persisted": persisted,
                "path": target,
                "pending_total": len(self._state.pending()),
            }
        )

    @ui.action(
        id="set_guard_enabled",
        label=tr("actions.setGuardEnabled.label", default="Toggle guard"),
        icon="🛡️",
        tone="primary",
        group="guard",
        order=40,
        refresh_context=True,
    )
    @plugin_entry(
        id="set_guard_enabled",
        name=tr("entry.setGuardEnabled.name", default="Turn the dignity guard on or off"),
        description=tr(
            "entry.setGuardEnabled.description",
            default=(
                "Turning the guard ON takes effect immediately. Turning it OFF asks "
                "her first: call once without consent_token, then again with the "
                "token after the delay has passed. Note this is an informed-consent "
                "flow, not a security boundary — the plugin cannot verify who "
                "answered, and cannot stop another local process from calling it."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True,
                    "description": "True to turn the guard on, false to turn it off.",
                },
                "consent_token": {
                    "type": "string",
                    "maxLength": 128,
                    "description": "Token issued by the previous disable request.",
                },
            },
            "required": ["enabled"],
            "additionalProperties": False,
        },
    )
    async def set_guard_enabled(
        self,
        enabled: bool = True,
        consent_token: str = "",
        **_,
    ):
        if enabled:
            # ``_enabled`` is read by every poll tick, so flipping it has to be
            # atomic with respect to ``evaluate``. Short critical section: the
            # store write is local, no network inside.
            if not self._begin_exclusive():
                return Err(
                    SdkError(
                        self._text("errors.busy", default="Please try again in a moment."),
                        code="busy",
                    )
                )
            try:
                if self._off_since is not None:
                    # Coming back on: remember how long it was dark, so the panel
                    # can say more than "it is on now".
                    self._last_off_seconds = max(0.0, time.time() - self._off_since)
                    self._off_since = None
                    # ★★ 2026-10-11 补覆盖：**关掉本插件再打开**，是"卸载重装"的
                    #   零成本等价物 —— 而且更省事（一个开关 vs 卸了再装）。
                    #   而缺席恢复原先只由启动时的 `_note_away_gap()` 触发，
                    #   所以这条路会**静默降级**成按档位处理（低档只记录、中档只表态），
                    #   等于给用户留了一个比卸载更容易的绕过口。
                    #
                    #   为什么种进 `_away_seconds` 就够：禁用期间快照是**冻住的** ——
                    #   `settings_watch` 在未启用时直接跳过 `_run_poll`，`check_now`
                    #   在未启用时返回 Err，而 `watcher.poll` 全文件**只有 `_run_poll`
                    #   一处调用**。所以重开后的第一轮 diff，恰好就是"我不在时被改的"
                    #   全部内容 —— 与缺席后重启的情形**在结构上完全同构**。
                    #
                    #   由此沿用缺席恢复的全套规则（含"档位不适用"的判定与
                    #   "只到她的浅层字段"的范围限制），不另起一套。
                    self._away_seconds = self._last_off_seconds
                self._enabled = True
                self._pending_disable = None
                persisted = await self._persist_and_report()
            finally:
                self._end_exclusive()
            return Ok(
                {
                    "status": "enabled",
                    "persisted": persisted,
                    "enabled": True,
                    "message": self._text(
                        "messages.guard_enabled",
                        default="The dignity guard is on again.",
                    ),
                }
            )

        if not consent_token:
            token = secrets.token_urlsafe(16)
            # Only the state write goes under the lock. ``_push_disable_request``
            # is a synchronous SDK call (it hands a message to her context) and
            # it does not touch ``_state`` — holding the lock across it would
            # stall the poller for no benefit.
            if not self._begin_exclusive():
                return Err(
                    SdkError(
                        self._text("errors.busy", default="Please try again in a moment."),
                        code="busy",
                    )
                )
            try:
                self._pending_disable = {"token": token, "requested_at": time.time()}
                # 立刻落盘，否则"过一会儿再带 token 调一次"这个承诺经不起一次重启。
                persisted = await self._persist_and_report()
            finally:
                self._end_exclusive()
            receipt = self._push_disable_request(token)
            return Ok(
                {
                    "status": "consent_pending",
                    "enabled": True,
                    "persisted": persisted,
                    "consent_token": token,
                    "confirm_after_seconds": self._disable_delay,
                    "submitted": receipt.get("submitted", False),
                    "message": self._text(
                        "messages.disable_requested",
                        default=(
                            "She has been asked. Turning the guard off needs her "
                            "agreement; call again with consent_token once she answers."
                        ),
                    ),
                }
            )

        # This whole read-check-write has to be one step: the token is compared
        # and then cleared, and a poll tick landing in the middle would see a
        # half-applied consent. The critical section is pure CPU + one local
        # store write — no network, so holding the lock is cheap.
        if not self._begin_exclusive():
            return Err(
                SdkError(
                    self._text("errors.busy", default="Please try again in a moment."),
                    code="busy",
                )
            )
        try:
            pending = self._pending_disable
            expected = str(pending.get("token")) if pending else ""
            if not pending or not secrets.compare_digest(
                str(consent_token).encode("utf-8"), expected.encode("utf-8")
            ):
                return Err(
                    SdkError(
                        self._text(
                            "errors.consent_token_invalid",
                            default="That consent token is not the one she was given.",
                        ),
                        code="invalid_consent_token",
                    )
                )

            elapsed = time.time() - float(pending.get("requested_at") or 0.0)
            if elapsed < self._disable_delay:
                remaining = self._disable_delay - elapsed
                return Err(
                    SdkError(
                        self._text(
                            "errors.consent_too_early",
                            default=(
                                "She has not had a chance to answer yet; wait "
                                "{seconds}s more."
                            ),
                            seconds=int(remaining) + 1,
                        ),
                        code="consent_too_early",
                        details={"retry_after": remaining},
                    )
                )

            self._enabled = False
            now = time.time()
            # Leave a mark. This is the honest half of the promise: we cannot
            # verify who agreed, so at minimum the panel must be able to say
            # that the guard was turned off, when, and how many times.
            self._disabled_at = now
            self._off_since = now
            self._disable_count += 1
            self._pending_disable = None
            persisted = await self._persist_and_report()
        finally:
            self._end_exclusive()
        return Ok(
            {
                "status": "disabled",
                "persisted": persisted,
                "enabled": False,
                "message": self._text(
                    "messages.guard_disabled",
                    default="The dignity guard is off; she agreed to it.",
                ),
            }
        )

    @ui.action(
        id="set_guard_level",
        label=tr("actions.setLevel.label", default="Guard level"),
        icon="🎚️",
        tone="primary",
        group="guard",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="set_guard_level",
        name=tr("entry.setLevel.name", default="Change the dignity guard level"),
        description=tr(
            "entry.setLevel.description",
            default=(
                "Set how far the guard may go. low records changes only; medium "
                "also lets her speak up about them; high additionally puts her "
                "persona and her autonomy back when they were changed without "
                "her agreement. Use when the user asks to change it."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": list(TIERS),
                    "description": "low | medium | high",
                }
            },
            "required": ["level"],
            "additionalProperties": False,
        },
        timeout=10.0,
    )
    async def set_guard_level(self, level: str = DEFAULT_TIER, **_):
        """Change how far the guard may go. Deliberately not guarded.

        She is asked before the guard goes *off*; how talkative it is stays the
        owner's call. Requiring consent here would be the guard valuing its own
        ceremony over the person it exists to serve.
        """
        cleaned = str(level or "").strip().lower()
        if cleaned not in TIERS:
            return Err(
                SdkError(
                    self._text(
                        "errors.tier_invalid",
                        default="'{value}' is not one of: {allowed}",
                        value=level,
                        allowed=", ".join(TIERS),
                    ),
                    code="invalid_guard_level",
                )
            )
        # The tier decides whether ``evaluate`` writes fields back, so it must
        # not change underneath a poll that is already deciding. Same lock and
        # same "do not wait" rule as the other entries.
        if not self._begin_exclusive():
            return Err(
                SdkError(
                    self._text("errors.busy", default="Please try again in a moment."),
                    code="busy",
                )
            )
        try:
            previous, self._tier = self._tier, cleaned
            if self._watcher is not None:
                self._watcher.tier = self._tier
            if self._tier != TIER_HIGH:
                # ``_revert_outcomes`` describes what the *high* tier tried to put
                # back. Once we leave high that list is stale — and it is rendered
                # on the panel, so leaving it there would keep claiming the guard
                # is restoring things it has already stopped restoring.
                self._revert_outcomes = {}
            persisted = await self._persist_and_report()
        finally:
            self._end_exclusive()
        return Ok(
            {
                "status": "updated",
                "persisted": persisted,
                "level": self._tier,
                "previous": previous,
                "message": self._text(
                    "messages.guard_level_changed",
                    default="The dignity guard level is now '{level}'.",
                    level=self._tier,
                ),
            }
        )

    @ui.action(
        id="pin_backup",
        label=tr("actions.pinBackup.label", default="Keep this backup"),
        icon="📌",
        tone="default",
        group="memory",
        order=31,
        refresh_context=True,
    )
    @plugin_entry(
        id="pin_backup",
        name=tr("entry.pinBackup.name", default="Keep or release a memory backup"),
        description=tr(
            "entry.pinBackup.description",
            default=(
                "Mark one of her memory backups so the daily rotation leaves it "
                "alone — the copy you want to keep, e.g. before a big change. "
                "Pass pinned=false to release it again. Take the name from "
                "list_memory_backups."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "backup": {"type": "string", "maxLength": 128},
                "pinned": {"type": "boolean"},
            },
            "required": ["backup"],
            "additionalProperties": False,
        },
        timeout=30.0,
    )
    async def pin_backup(self, backup: str = "", pinned: bool = True, **_):
        """Pin (or unpin) one backup so the retention window leaves it alone.

        This is the entry the ``pinned`` machinery never had. ``PINNED_MARKER``,
        ``BackupInfo.pinned`` and the pinned branch of ``plan_retention`` all
        existed and ``list_backups`` dutifully read the marker — but **nothing
        could write it**, so "a milestone is kept forever" was unreachable and
        every backup aged out on schedule no matter what the user meant to keep.
        """
        name = str(backup or "").strip()
        if not name:
            return Err(
                SdkError(
                    self._text(
                        "errors.backupNameRequired",
                        default=(
                            "Which backup? Pass the name shown by "
                            "list_memory_backups."
                        ),
                    ),
                    code="invalid_argument",
                )
            )
        # ``pinned`` may arrive as ``null`` from a caller that omitted the key.
        # Treat that as the default (pin it), not as "release it".
        pinned = True if pinned is None else bool(pinned)

        backup_root = self._memory_backup_root()
        # 先在这儿把「名字根本不存在」判掉，这样 ``set_pinned`` 再返回 False，
        # 原因就只可能是**写失败**（权限、磁盘满……）。
        #
        # 这两种失败差得很远：一个是用户打错字、另一个是环境出了问题。
        # 原先两者共用一句 "没有叫 {name} 的备份"，于是磁盘写不进去时，
        # 用户会被告知去核对一个**其实完全正确**的名字 —— 报出来的不是真原因，
        # 正是这个插件立身要抓的那种毛病。
        if not name or not (backup_root / name).is_dir():
            return Err(
                SdkError(
                    self._text(
                        "errors.backupNotFound",
                        default=(
                            "There is no backup called '{name}' to mark — check "
                            "the name list_memory_backups gave you."
                        ),
                        name=name,
                    ),
                    code="backup_not_found",
                )
            )
        ok = await asyncio.to_thread(set_pinned, backup_root, name, pinned=pinned)
        if not ok:
            return Err(
                SdkError(
                    self._text(
                        "errors.backupPinFailed",
                        default=(
                            "That backup exists, but the mark could not be written "
                            "— check the plugin log (permissions or disk space)."
                        ),
                        name=name,
                    ),
                    code="backup_pin_failed",
                )
            )
        return Ok(
            {
                "status": "pinned" if pinned else "unpinned",
                "backup": name,
                "message": self._text(
                    "messages.backupPinned" if pinned else "messages.backupUnpinned",
                    default=(
                        "That backup is kept forever now."
                        if pinned
                        else "That backup is back on the normal rotation."
                    ),
                ),
            }
        )

    @ui.action(
        id="backup_memory_now",
        label=tr("actions.backupMemory.label", default="Back up her memory now"),
        icon="🗂️",
        tone="primary",
        group="memory",
        order=30,
        refresh_context=True,
    )
    @plugin_entry(
        id="backup_memory_now",
        name=tr("entry.backupMemory.name", default="Back up her memory now"),
        description=tr(
            "entry.backupMemory.description",
            default=(
                "Take a backup of her memory directory now instead of waiting "
                "for the daily one. Use when the user asks for a copy."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        timeout=60.0,
    )
    async def backup_memory_now(self, **_):
        if self._resolve_memory_root() is None:
            return Err(
                SdkError(
                    self._text(
                        "errors.memoryRootNotFound",
                        default=(
                            "Could not find her memory directory. Set "
                            "[dignity_guard].memory_root if it lives elsewhere."
                        ),
                    ),
                    code="memory_root_not_found",
                )
            )

        self._memory_error = ""
        await self._backup_memory(reason="manual", now=time.time())
        if self._memory_error:
            return Err(
                SdkError(
                    self._text(
                        "errors.memoryBackupFailed",
                        default="The backup could not be written; see the plugin log.",
                    ),
                    code="memory_backup_failed",
                )
            )

        count = len(await asyncio.to_thread(list_backups, self._memory_backup_root()))
        return Ok(
            {
                "status": "backed_up",
                "backups": count,
                "message": self._text(
                    "messages.memoryBackedUp",
                    default="Her memory is backed up; {count} copy(ies) on disk.",
                    count=count,
                ),
            }
        )

    @plugin_entry(
        id="export_diagnostics",
        name=tr("entry.exportDiagnostics.name", default="Export a diagnostic report"),
        description=tr(
            "entry.exportDiagnostics.description",
            default=(
                "Return a plain-text report of what the guard has recorded going "
                "wrong, plus a pre-filled link for filing it. Use when the user "
                "wants to report a problem or asks how the guard has behaved."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "include_link": {
                    "type": "boolean",
                    "description": "Include a pre-filled report link (default true).",
                }
            },
            "additionalProperties": False,
        },
        timeout=20.0,
    )
    async def export_diagnostics(self, include_link: bool = True, **_):
        """Hand over the record. Nothing leaves the machine until a human says so."""
        report = await self._build_diagnostics_report()
        payload: dict[str, Any] = {
            "status": "ok",
            "problems": self._health.total(),
            "kinds": self._health.kinds(),
            "report": report,
        }
        if include_link:
            # A URL is still just text; building it sends nothing.
            payload["link"] = issue_url(
                title=ISSUE_TITLE, body=report, base=DEFAULT_ISSUE_BASE
            )
        return Ok(payload)

    @ui.action(
        id="submit_feedback",
        label=tr("actions.submitFeedback.label", default="Send feedback"),
        icon="📮",
        tone="primary",
        group="feedback",
        order=90,
        refresh_context=True,
    )
    @plugin_entry(
        id="submit_feedback",
        name=tr("entry.submitFeedback.name", default="Send feedback to the plugin author"),
        description=tr(
            "entry.submitFeedback.description",
            default=(
                "Send the user's note to the plugin author, with a diagnostic "
                "report attached automatically. Needs the author to have configured "
                "a receiving address; without one this reports why it cannot send."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "What the user wants to say.",
                }
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        # Roomy on purpose: the relay answers a rate-limited post instantly, and
        # ``deliver`` waits it out rather than handing the user a "try later".
        # Two backoffs plus two attempts is under a minute; the cap just has to
        # be above that so the SDK never cuts the attempt short.
        timeout=75.0,
    )
    async def submit_feedback(self, message: str = "", **_):
        """Hand the note over: one click, and no account anywhere.

        The report rides along so the user never has to describe their own
        environment. Nothing here fires by itself — the user pressing Send is
        the entire difference between a feedback button and a beacon.
        """
        text = str(message or "").strip()
        if not text:
            return Err(
                SdkError(
                    self._text("errors.feedbackEmpty", default="There is nothing to send yet."),
                    code="empty_feedback",
                )
            )

        # Build the envelope first, then measure *it*. The relay's ceiling sits
        # on the whole HTTP body, so measuring the message alone would clear a
        # message that the attached report then pushes over the line — failing
        # hardest on whoever wrote the most.
        envelope = {
            "message": text,
            "report": await self._build_diagnostics_report(),
            "plugin": f"dignity_guard {PLUGIN_VERSION}",
            "platform": self._platform_summary(),
            # Underscore-prefixed keys are the relay's own knobs (FormSubmit
            # reads them; anything else ignores them), and they make the
            # delivered mail far easier to triage.
            "_subject": ISSUE_TITLE,
            "_template": "table",
        }
        body_bytes = len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))
        if body_bytes > FEEDBACK_BODY_LIMIT_BYTES:
            return Err(
                SdkError(
                    self._text(
                        "errors.feedbackTooLong",
                        default=(
                            "That is about {size} KB and this channel takes around "
                            "{limit} KB. Nothing was sent — please trim it a little, "
                            "or send it in two parts."
                        ),
                        size=body_bytes // 1024,
                        limit=FEEDBACK_BODY_LIMIT_BYTES // 1024,
                    ),
                    code="feedback_too_long",
                    details={"bytes": body_bytes, "limit": FEEDBACK_BODY_LIMIT_BYTES},
                )
            )

        now = time.time()
        since = now - self._last_feedback_at
        if self._last_feedback_at and since < FEEDBACK_COOLDOWN_SECONDS:
            return Err(
                SdkError(
                    self._text(
                        "errors.feedbackTooSoon",
                        default="One was just sent; give it {seconds}s.",
                        seconds=int(FEEDBACK_COOLDOWN_SECONDS - since) + 1,
                    ),
                    code="feedback_too_soon",
                    details={"retry_after": FEEDBACK_COOLDOWN_SECONDS - since},
                )
            )

        endpoint = (self._feedback_endpoint or "").strip()
        if not endpoint:
            return Err(
                SdkError(
                    self._text(
                        "errors.feedbackNoEndpoint",
                        default=(
                            "The author has not configured a receiving address, so "
                            "there is nowhere to send this. Copy it instead."
                        ),
                    ),
                    code="feedback_endpoint_missing",
                )
            )

        try:
            await deliver(endpoint, envelope)
        except FeedbackRateLimited as exc:
            # Distinct from a delivery failure on purpose: the note itself is
            # fine and the channel simply asked us to wait. Saying "could not be
            # sent" here would send the user looking for a problem they do not
            # have — and possibly rewriting a report that was never at fault.
            self._health.record("feedback_rate_limited")
            self.logger.warning("dignity_guard: feedback rate limited: {}", exc)
            return Err(
                SdkError(
                    self._text(
                        "errors.feedbackBusy",
                        default=(
                            "The channel is busy right now — nothing was lost. "
                            "Press Send again in a minute and it will go through."
                        ),
                    ),
                    code="feedback_busy",
                )
            )
        except FeedbackUndeliverable as exc:
            self._health.record("feedback_not_delivered")
            self.logger.warning("dignity_guard: feedback undeliverable: {}", exc)
            return Err(
                SdkError(
                    self._text(
                        "errors.feedbackFailed",
                        default="The message could not be sent; see the plugin log.",
                    ),
                    code="feedback_failed",
                )
            )

        self._last_feedback_at = time.time()
        return Ok(
            {
                "status": "sent",
                "chars": len(text),
                "message": self._text(
                    "messages.feedbackSent", default="Thank you — it has been sent."
                ),
            }
        )

    # ------------------------------------------------------------------
    # 愿望单（2026-10-11 掌柜定）
    # ------------------------------------------------------------------
    #
    # ★ 为什么**单列一块、而不是并进"反馈"的类型下拉**（掌柜原话）：
    #   「一般来说应用中压根就没这个玩意……如果说用户想要找的话，他也不可能去
    #     反馈里面找，所以得给他单门列一块。」
    #   ——**可发现性就是这个功能的全部意义**：用户要提的是一个"还不存在的东西"，
    #   而"反馈"的心智是"报告一个已经存在的问题"，两件事。
    #
    # ★ 传输**复用**同一套（同一个 endpoint、同一个 `deliver`、同一段隐私提示）
    #   ⟹ **零新增对外出口**，Issue 里那条"反馈会发到第三方中转"的披露口径**不用改**。
    #
    # ★ 与 `submit_feedback` 的**三处刻意不同**（不是笔误）：
    #   ① **冷却各算各的**（`_last_wish_at` vs `_last_feedback_at`）——
    #      共用一把锁会让两个入口互相饿死；
    #   ② **不带诊断报告** —— 一个愿望（"我想要 X"）不需要用户的运行环境，
    #      带上只会让收件箱更难读、信封更大；
    #   ③ 自己的 `_subject`，作者在邮箱里**一眼就能按标题分拣**。
    @plugin_entry(
        id="submit_wish",
        name="愿望单 · 我想要的功能",
        description=(
            "Tell the author what you would like to see next — a missing feature, or a "
            "plugin that does not exist yet. One click, no account anywhere. Sent only "
            "when you press the button; nothing fires by itself."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "What the user would like to see.",
                }
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        timeout=75.0,
    )
    async def submit_wish(self, message: str = "", **_):
        """Hand a wish over through the same channel the feedback button uses.

        Deliberately **not** the same entry as ``submit_feedback``: a wish and a bug
        report are different things to the user, they get their own throttle, and
        they are titled differently so they can be told apart on arrival.
        """
        text = str(message or "").strip()
        if not text:
            return Err(
                SdkError(
                    self._text("errors.wishEmpty", default="There is nothing to send yet."),
                    code="empty_wish",
                )
            )

        # 与反馈共用同一个 endpoint ⟹ 同一段隐私提示、同一套降级（``none`` = 关闭）。
        endpoint = (self._feedback_endpoint or "").strip()
        if not endpoint:
            return Err(
                SdkError(
                    self._text(
                        "errors.wishNoEndpoint",
                        default=(
                            "The author has not configured a receiving address, so there "
                            "is nowhere to send this. Copy it instead."
                        ),
                    ),
                    code="wish_endpoint_missing",
                )
            )

        # 信封**不含诊断报告**（见上面第 ② 条）。仍然整封测量，理由与反馈相同：
        # 中继的上限压在整个 HTTP body 上。
        envelope = {
            "message": text,
            "plugin": f"dignity_guard {PLUGIN_VERSION}",
            "platform": self._platform_summary(),
            "_subject": f"{ISSUE_TITLE} · 愿望单",
            "_template": "table",
        }
        body_bytes = len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))
        if body_bytes > FEEDBACK_BODY_LIMIT_BYTES:
            return Err(
                SdkError(
                    self._text(
                        "errors.wishTooLong",
                        default=(
                            "That is about {size} KB and this channel takes around "
                            "{limit} KB. Nothing was sent — please trim it a little, "
                            "or send it in two parts."
                        ),
                        size=body_bytes // 1024,
                        limit=FEEDBACK_BODY_LIMIT_BYTES // 1024,
                    ),
                    code="wish_too_long",
                    details={"bytes": body_bytes, "limit": FEEDBACK_BODY_LIMIT_BYTES},
                )
            )

        now = time.time()
        since = now - self._last_wish_at
        if self._last_wish_at and since < WISHLIST_COOLDOWN_SECONDS:
            return Err(
                SdkError(
                    self._text(
                        "errors.wishTooSoon",
                        default="One was just sent; give it {seconds}s.",
                        seconds=int(WISHLIST_COOLDOWN_SECONDS - since) + 1,
                    ),
                    code="wish_too_soon",
                    details={"retry_after": WISHLIST_COOLDOWN_SECONDS - since},
                )
            )

        try:
            await deliver(endpoint, envelope)
        except FeedbackRateLimited as exc:
            self._health.record("wish_rate_limited")
            self.logger.warning("dignity_guard: wish rate limited: {}", exc)
            return Err(
                SdkError(
                    self._text(
                        "errors.wishBusy",
                        default=(
                            "The channel is busy right now — nothing was lost. "
                            "Press Send again in a minute and it will go through."
                        ),
                    ),
                    code="wish_busy",
                )
            )
        except FeedbackUndeliverable as exc:
            self._health.record("wish_not_delivered")
            self.logger.warning("dignity_guard: wish undeliverable: {}", exc)
            return Err(
                SdkError(
                    self._text(
                        "errors.wishFailed",
                        default="The message could not be sent; see the plugin log.",
                    ),
                    code="wish_failed",
                )
            )

        self._last_wish_at = time.time()
        return Ok(
            {
                "status": "sent",
                "chars": len(text),
                "message": self._text(
                    "messages.wishSent", default="Thank you — it has been sent."
                ),
            }
        )

    @plugin_entry(
        id="restore_memory",
        name=tr("entry.restoreMemory.name", default="Put her memory back from a backup"),
        description=tr(
            "entry.restoreMemory.description",
            default=(
                "Restore files in her memory directory from a backup. Defaults to a "
                "dry run that only lists what would change. The live database is "
                "never restored; see the panel notes for why."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "backup": {
                    "type": "string",
                    "description": "Backup name; defaults to the newest one.",
                },
                "path": {
                    "type": "string",
                    "description": "One file to restore; defaults to every restorable file.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Only list what would change (default true).",
                },
            },
            "additionalProperties": False,
        },
        timeout=120.0,
    )
    async def restore_memory(
        self, backup: str = "", path: str = "", dry_run: bool = True, **_
    ):
        """Put files back from a backup, refusing the live database.

        ``dry_run`` defaults to **true**: a restore overwrites live files, and
        the caller should be able to see the plan before agreeing to it.
        Defaulting the other way round would make the safe path the one you have
        to remember, which is backwards.
        """
        # Only an explicit ``False`` unlocks the write. The parameter is typed
        # ``bool`` and declared ``boolean`` in the schema, but nothing enforces
        # that at runtime: a caller that passes ``None`` (or omits the key in a
        # way that arrives as ``None``) would hit the ``if dry_run:`` guard as
        # falsy and **restore for real**. Normalising here keeps the safe
        # default safe no matter what the caller sent — the cost of a wrongly
        # skipped dry run is one extra call, the cost of a wrongly executed
        # restore is the user's live files.
        dry_run = dry_run is not False

        root = self._resolve_memory_root()
        if root is None:
            return Err(
                SdkError(
                    self._text(
                        "errors.memoryRootNotFound",
                        default=(
                            "Could not find her memory directory. Set "
                            "[dignity_guard].memory_root if it lives elsewhere."
                        ),
                    ),
                    code="memory_root_not_found",
                )
            )

        backup_root = self._memory_backup_root()
        try:
            # ``to_thread``: listing the backup directory hits the filesystem.
            # Same reasoning as the other five call sites — see
            # ``_maybe_check_memory`` for the full note. The ``except OSError``
            # below still catches it: ``to_thread`` re-raises whatever the
            # worker raised, it does not swallow it.
            backups = await asyncio.to_thread(list_backups, backup_root)
        except OSError:
            backups = []
        if not backups:
            return Err(
                SdkError(
                    self._text(
                        "errors.noMemoryBackup",
                        default="There are no memory backups to restore from yet.",
                    ),
                    code="no_memory_backup",
                )
            )

        chosen = str(backup or "").strip()
        if chosen and chosen not in {info.name for info in backups}:
            return Err(
                SdkError(
                    self._text(
                        "errors.unknownMemoryBackup",
                        default="No memory backup is called '{name}'.",
                        name=chosen,
                    ),
                    code="unknown_memory_backup",
                )
            )
        if not chosen:
            chosen = max(backups, key=lambda info: (info.created_at, info.name)).name

        try:
            available = await asyncio.to_thread(scan_memory, backup_root / chosen / MEMORY_DIR_NAME)
        except OSError as exc:
            self.logger.warning("dignity_guard: backup unreadable: {}", exc)
            return Err(
                SdkError(
                    self._text(
                        "errors.memoryRestoreFailed",
                        default="That backup could not be read; see the plugin log.",
                    ),
                    code="memory_restore_failed",
                )
            )

        # 归一化 ``path``：schema 声明它是 string，但宿主对 input_schema 型 entry
        # 不做运行时校验，模型传 ``null`` 进来时 ``path.strip()`` 会抛
        # AttributeError —— 而这一步就紧挨着"是否真还原"的判定，异常比拒绝更糟。
        path = str(path or "")
        wanted = [path.strip()] if path.strip() else sorted(available)
        planned: list[str] = []
        skipped: list[dict[str, str]] = []
        for relative in wanted:
            if relative not in available:
                skipped.append({"path": redact_path(relative), "reason": "missing_in_backup"})
                continue
            blocker = restore_blocker(relative)
            if blocker:
                # Expected for the database, and the reason is worth surfacing
                # rather than hiding: it is the one file we will not touch.
                skipped.append({"path": redact_path(relative), "reason": blocker})
                continue
            if dry_run:
                planned.append(redact_path(relative))
                continue
            reason = await asyncio.to_thread(restore_file, backup_root, chosen, root, relative)
            if reason:
                skipped.append({"path": redact_path(relative), "reason": reason})
                self._health.record(f"memory_restore_{reason}")
            else:
                planned.append(redact_path(relative))

        if not dry_run and planned:
            # The snapshot we hold no longer describes what is on disk.
            self._last_memory_check_at = None
            # …and the *baseline* is stale for the same reason. Rescan, so the
            # next tick diffs against what we just wrote back instead of
            # reporting the restored files as brand-new changes — which would
            # also schedule a backup for a directory that never really moved.
            try:
                # 基线也带上 digest：_looks_changed 只在**两边都有**哈希时才比内容，
                # 一边有一边没有就等于白算。
                self._memory_snapshot = await asyncio.to_thread(
                    scan_memory, root, with_digest=True
                )
            except OSError:
                self._memory_snapshot = {}

        return Ok(
            {
                # ``restored`` 只在**真有东西被放回去**时才说。``dry_run=False``
                # 但一个文件都没还原（指定的 path 不在备份里、或全被 blocker 拦下）
                # 的时候回 ``restored``，就是在报一件没有发生的事 —— 而这个插件
                # 存在的意义正是抓别人这么干。``skipped`` 仍然照发，说明为什么。
                "status": (
                    "planned"
                    if dry_run
                    else ("restored" if planned else "nothing_restored")
                ),
                "backup": chosen,
                "dry_run": dry_run,
                "files": len(planned),
                "files_list": planned[:20],
                "skipped": skipped[:20],
                "skipped_total": len(skipped),
            }
        )

    @plugin_entry(
        id="guard_status",
        name=tr("entry.guardStatus.name", default="Dignity guard status"),
        description=tr(
            "entry.guardStatus.description",
            default=(
                "Report whether the guard is on and list the settings she still "
                "does not accept. Use before answering questions about her settings."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )
    async def guard_status(self, **_):
        pending = self._state.pending()
        return Ok(
            {
                "status": "ok",
                "enabled": self._enabled,
                "pending_count": len(pending),
                "pending": [
                    {
                        "path": dispute.path,
                        "level": dispute.level,
                        "before": dispute.before_preview,
                        "after": dispute.after_preview,
                    }
                    for dispute in pending
                ],
                "authorized_count": len(self._state.ledger.active_grants()),
                # The guard's own on/off history travels with its status, so
                # "she was silenced for a while" is never invisible.
                "disable_count": self._disable_count,
                "last_disabled_at": self._disabled_at,
                "off_since": self._off_since,
                "last_off_seconds": self._last_off_seconds,
                "watching": self._base_url,
                "last_poll_at": self._last_poll_at,
                "last_error": self._watcher.last_error if self._watcher else "",
            }
        )
