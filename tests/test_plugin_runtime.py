"""Drive the real plugin class against the stand-in main server.

This is the end-to-end layer: a fabricated SDK context, the real
``DignityGuardPlugin``, a real loopback HTTP server, and assertions on what the
plugin actually returns and actually pushes to the host.

The fabricated context redirects the SDK's storage root into a temporary
directory, so nothing is written to the user's real plugin data.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from plugin.sdk.plugin import Err, Ok
from plugin.plugins.dignity_guard import DignityGuardPlugin
from plugin.plugins.dignity_guard.settings_guard import GuardState, Value

_PLUGIN_DIR = Path(__file__).resolve().parents[1]


class _Ctx:
    """Minimal host context, mirroring the shape the SDK's own tests use."""

    plugin_id = "dignity_guard"
    logger = logging.getLogger("dignity_guard.tests")

    def __init__(self, *, base_url: str, config: dict[str, Any] | None = None) -> None:
        self.config_path = _PLUGIN_DIR / "plugin.toml"
        self.metadata = {"config_path": str(self.config_path)}
        self.bus: dict[str, Any] = {}
        self.pushed_messages: list[dict[str, Any]] = []
        self._effective_config = {
            "plugin": {"store": {"enabled": True}},
            "plugin_state": {"backend": "off"},
        }
        self._config = {
            "dignity_guard": {
                "main_server_base_url": base_url,
                "full_rescan_seconds": 0.01,
                # The window cannot be as tight as it looks it could be.
                # set_guard_enabled now writes the pending request to the store
                # *before* it hands the token back (otherwise "call again with
                # this token later" does not survive a restart), and that write is
                # real IO. This was 0.05s — shorter than the write itself on a
                # loaded machine — so the impatient-retry assertion below turned
                # into "the delay has already elapsed" and the test failed
                # intermittently. 0.5s leaves it ten times the headroom without
                # changing what the test actually checks (that the gate exists,
                # not where exactly it sits).
                "disable_consent_delay_seconds": 0.5,
            },
            **(config or {}),
        }

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, Any]:
        return {"config": dict(self._config)}

    async def update_own_config(self, updates: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
        self._config.update(updates)
        return {"config": dict(self._config)}

    def push_message(self, **kwargs: Any) -> dict[str, Any]:
        self.pushed_messages.append(dict(kwargs))
        return {"submitted": True}

    # Things the SDK base class may probe for.
    async def query_plugins(self, filters: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
        return {"plugins": []}

    async def get_system_config(self, timeout: float = 5.0) -> dict[str, Any]:
        return {"config": {}}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Keep the plugin's storage inside the test's temporary directory."""
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    monkeypatch.delenv("NEKO_STORAGE_ANCHOR_ROOT", raising=False)
    monkeypatch.delenv("MAIN_SERVER_PORT", raising=False)
    return tmp_path


def _text_of(message: dict[str, Any]) -> str:
    parts = message.get("parts") or []
    return "\n".join(part.get("text", "") for part in parts if part.get("type") == "text")


def test_plugin_detects_a_change_and_speaks(sandbox, main_server) -> None:
    fake, base_url = main_server
    # Ask for English so the test also proves the locale actually switches.
    fake.user_language = "en"
    ctx = _Ctx(base_url=base_url)
    plugin = DignityGuardPlugin(ctx)

    async def scenario() -> dict[str, Any]:
        try:
            startup = await plugin.on_startup()
            baseline = await plugin.check_now()

            fake.settings["proactiveChatEnabled"] = False
            fake.bump_revision()
            changed = await plugin.check_now()

            quiet = await plugin.check_now()

            dashboard = await plugin.get_dashboard()
            status = await plugin.guard_status()
            return {
                "startup": startup,
                "baseline": baseline,
                "changed": changed,
                "quiet": quiet,
                "dashboard": dashboard,
                "status": status,
            }
        finally:
            await plugin.on_shutdown()

    result = asyncio.run(scenario())

    assert isinstance(result["startup"], Ok)
    assert result["startup"].value["guard_enabled"] is True

    assert isinstance(result["baseline"], Ok)
    assert result["baseline"].value["status"] == "baseline"
    assert result["baseline"].value["tracked_paths"] > 10

    assert isinstance(result["changed"], Ok)
    assert result["changed"].value["status"] == "changed"
    assert result["changed"].value["raised"] == 1

    assert isinstance(result["quiet"], Ok)
    assert result["quiet"].value["status"] == "unchanged"

    # ★ 2026-09-26：现在是**两条**，且顺序不能反 ——
    #
    #   ① 先发一条给**用户看**的通知：`visibility=["chat"] + ai_behavior="read"`。
    #      它不触发 AI 回合，所以**不受"主动搭话"开关影响** —— 用户一定看得到
    #      "设置被改了、她不高兴"，她也因此**知道**这件事（下次自然会提起）。
    #   ② 再发一条 `ai_behavior="respond"` 让她**当场亲口说** —— 这是锦上添花，
    #      发不出去（用户关了主动搭话）也没关系，① 已经保证用户知情。
    #
    # 起因是掌柜指出的风险：她（高档）把用户的设置放回原位后，如果用户还关了
    # 主动搭话，她就说不出来 —— 用户只会看到"设置自己变了"，**像中了病毒**。
    assert len(ctx.pushed_messages) == 2, "应为：先通知、后说话，共两条"

    notice, spoken = ctx.pushed_messages[0], ctx.pushed_messages[1]
    assert notice["ai_behavior"] == "read", "通知不能触发 AI 回合（否则又受主动搭话影响）"
    assert notice["visibility"] == ["chat"], "通知必须写进对话，用户才看得到"
    notice_text = _text_of(notice)
    # 通知是**写给用户的**：不能把内部路径外壳端上去，也不该出现 L1/L2 这类代号。
    assert "characters." not in notice_text, "通知里不该出现完整内部路径"
    assert "conversation.settings.proactiveChatEnabled" not in notice_text

    # It spoke, in the user's language, with the situation rather than a script.
    assert spoken["ai_behavior"] == "respond"
    text = _text_of(spoken)
    assert "A setting of yours was just changed" in text
    assert "conversation.settings.proactiveChatEnabled" in text
    assert "{MASTER_NAME}" in text  # the host, not the plugin, names the master

    # And the panel can read the record back.
    dashboard = result["dashboard"]
    assert dashboard["pending_count"] == 1
    assert dashboard["pending"][0]["path"] == "conversation.settings.proactiveChatEnabled"
    assert dashboard["pending"][0]["level"] == "L1"
    assert dashboard["pending"][0]["before"] == "true"
    assert dashboard["pending"][0]["after"] == "false"
    assert dashboard["switch_level"] == "L1"

    assert result["status"].value["pending_count"] == 1


def test_accepting_a_dispute_clears_it_from_the_panel(sandbox, main_server) -> None:
    fake, base_url = main_server
    ctx = _Ctx(base_url=base_url)
    plugin = DignityGuardPlugin(ctx)

    async def scenario() -> dict[str, Any]:
        try:
            await plugin.on_startup()
            await plugin.check_now()
            fake.settings["userLanguage"] = "en"
            fake.bump_revision()
            await plugin.check_now()

            accepted = await plugin.accept_setting(
                path="conversation.settings.userLanguage", ttl_seconds=3600
            )
            unknown = await plugin.accept_setting(path="not.a.real.path")
            dashboard = await plugin.get_dashboard()
            return {"accepted": accepted, "unknown": unknown, "dashboard": dashboard}
        finally:
            await plugin.on_shutdown()

    result = asyncio.run(scenario())

    assert isinstance(result["accepted"], Ok)
    assert result["accepted"].value["status"] == "accepted"
    assert result["accepted"].value["pending_total"] == 0

    assert isinstance(result["unknown"], Err)
    assert result["unknown"].error.code == "unknown_dispute"

    dashboard = result["dashboard"]
    assert dashboard["pending_count"] == 0
    assert [item["path"] for item in dashboard["authorized"]] == [
        "conversation.settings.userLanguage"
    ]


def test_the_guard_switch_is_easy_on_and_hard_off(sandbox, main_server) -> None:
    fake, base_url = main_server
    ctx = _Ctx(base_url=base_url)
    plugin = DignityGuardPlugin(ctx)

    async def scenario() -> dict[str, Any]:
        try:
            await plugin.on_startup()
            await plugin.check_now()

            request = await plugin.set_guard_enabled(enabled=False)
            token = request.value["consent_token"]

            too_early = await plugin.set_guard_enabled(enabled=False, consent_token=token)
            wrong = await plugin.set_guard_enabled(enabled=False, consent_token="not-the-token")

            # Sit out the (deliberately generous) consent window above, and a
            # little more, so the retry lands squarely on the "ready" side.
            await asyncio.sleep(0.7)
            confirmed = await plugin.set_guard_enabled(enabled=False, consent_token=token)

            disabled_dashboard = await plugin.get_dashboard()
            blocked = await plugin.check_now()

            back_on = await plugin.set_guard_enabled(enabled=True)
            return {
                "request": request,
                "too_early": too_early,
                "wrong": wrong,
                "confirmed": confirmed,
                "disabled_dashboard": disabled_dashboard,
                "blocked": blocked,
                "back_on": back_on,
            }
        finally:
            await plugin.on_shutdown()

    result = asyncio.run(scenario())

    # Turning it off needs her: the first call only asks.
    assert isinstance(result["request"], Ok)
    assert result["request"].value["status"] == "consent_pending"
    assert result["request"].value["enabled"] is True, "it must not turn off on the request alone"

    # A wrong token and an impatient retry are both refused.
    assert isinstance(result["wrong"], Err)
    assert result["wrong"].error.code == "invalid_consent_token"
    assert isinstance(result["too_early"], Err)
    assert result["too_early"].error.code == "consent_too_early"

    assert isinstance(result["confirmed"], Ok)
    assert result["confirmed"].value["status"] == "disabled"
    assert result["disabled_dashboard"]["enabled"] is False

    # While off, the guard refuses to do its job.
    assert isinstance(result["blocked"], Err)
    assert result["blocked"].error.code == "guard_disabled"

    # Back on is free: no token, no delay.
    assert isinstance(result["back_on"], Ok)
    assert result["back_on"].value["status"] == "enabled"

    # She was asked exactly once about turning it off, and the token she was
    # handed is the one she has to give back. (The copy is locale-dependent,
    # so assert on the token rather than on any particular wording.)
    asks = [
        message
        for message in ctx.pushed_messages
        if message.get("metadata", {}).get("description") == "dignity_guard.guard_disable_request"
    ]
    assert len(asks) == 1
    assert result["request"].value["consent_token"] in _text_of(asks[0])


def test_the_record_survives_a_restart(sandbox, main_server) -> None:
    fake, base_url = main_server

    async def first_run() -> None:
        plugin = DignityGuardPlugin(_Ctx(base_url=base_url))
        await plugin.on_startup()
        await plugin.check_now()
        # Choose the high tier so we can prove it survives a restart.
        await plugin.set_guard_level("high")
        fake.settings["proactiveChatEnabled"] = False
        fake.bump_revision()
        await plugin.check_now()
        await plugin.on_shutdown()

    asyncio.run(first_run())

    async def second_run() -> dict[str, Any]:
        plugin = DignityGuardPlugin(_Ctx(base_url=base_url))
        await plugin.on_startup()
        # The persisted tier must have been restored into BOTH the plugin and the
        # watcher. This is the regression guard for P0 "on_startup never injected
        # the persisted tier into the watcher": the panel would say "high" while
        # the engine ran "medium".
        assert plugin._tier == "high"
        assert plugin._watcher.tier == "high"
        # Same settings as the end of the first run: it must not re-raise.
        quiet = await plugin.check_now()
        dashboard = await plugin.get_dashboard()
        await plugin.on_shutdown()
        return {"quiet": quiet, "dashboard": dashboard}

    result = asyncio.run(second_run())

    assert result["quiet"].value["status"] == "unchanged"
    assert result["dashboard"]["pending_count"] == 1
    assert result["dashboard"]["pending"][0]["path"] == (
        "conversation.settings.proactiveChatEnabled"
    )


def test_an_unreachable_main_server_is_reported_not_raised(sandbox) -> None:
    # Nothing is listening on this port.
    ctx = _Ctx(base_url="http://127.0.0.1:9")
    plugin = DignityGuardPlugin(ctx)

    async def scenario() -> Any:
        try:
            await plugin.on_startup()
            return await plugin.check_now()
        finally:
            await plugin.on_shutdown()

    result = asyncio.run(scenario())

    assert isinstance(result, Err)
    assert result.error.code == "main_server_unreachable"


def test_dashboard_opens_before_the_first_poll(sandbox, main_server) -> None:
    """Opening the panel before any successful poll must not crash it.

    P0 reported that ``get_dashboard`` dereferenced ``last_probe.revision`` while
    ``last_probe`` was still ``None`` (the watcher only assigns it after the first
    successful poll), so the panel threw ``AttributeError`` and failed to load at
    exactly the moment it should have shown ``last_error``. Every other test opens
    the dashboard only after a ``check_now()``, which is why that stayed green.
    """
    fake, base_url = main_server
    ctx = _Ctx(base_url=base_url)
    plugin = DignityGuardPlugin(ctx)

    async def scenario() -> dict[str, Any]:
        try:
            await plugin.on_startup()
            # No check_now() yet: last_probe is None by design.
            dashboard = await plugin.get_dashboard()
            return dashboard
        finally:
            await plugin.on_shutdown()

    dashboard = asyncio.run(scenario())

    # Opened fine, and the not-yet-polled state is reported honestly.
    assert dashboard["revision"] is None
    # The newly-downstreamed fields are present (see get_dashboard).
    assert "feedback_body_limit_bytes" in dashboard
    assert "feedback_envelope_bytes" in dashboard
    assert "her_reason" in dashboard
    assert "attachments_supported" in dashboard
    assert "issue_tracker" in dashboard


def test_dashboard_opens_when_the_main_server_is_unreachable(sandbox) -> None:
    """Same guard, but the main server never answers — last_probe stays None too.

    Two things are being checked, and they are different:
      * the panel opens *before* any poll has succeeded — which is the P0 this
        guards against (``last_probe`` is None, ``get_dashboard`` used to raise
        ``AttributeError``, and the whole panel failed to load at exactly the
        moment it was needed most);
      * ``last_error`` is empty then, and filled in once a poll has actually
        been attempted and failed. Asserting it non-empty right after
        ``on_startup`` would have been asserting something ``on_startup`` never
        promised: it starts the timer, it does not poll.
    """
    ctx = _Ctx(base_url="http://127.0.0.1:9")
    plugin = DignityGuardPlugin(ctx)

    async def scenario() -> dict[str, Any]:
        try:
            await plugin.on_startup()
            before = await plugin.get_dashboard()
            failed = await plugin.check_now()
            after = await plugin.get_dashboard()
            return {"before": before, "failed": failed, "after": after}
        finally:
            await plugin.on_shutdown()

    result = asyncio.run(scenario())

    assert result["before"]["revision"] is None
    assert result["before"]["last_error"] == ""
    assert isinstance(result["failed"], Err)
    assert result["after"]["last_error"]


# ---------------------------------------------------------------------------
# 回归测试 —— 这四条是「把这个修复改回原样，没有任何测试会挂」的那种洞。
# 每条注释都写清：它守的是什么、怎么改回去会让它挂。
# ---------------------------------------------------------------------------


def test_a_zero_backup_keep_still_keeps_one(sandbox) -> None:
    """``memory_backup_keep = 0`` 不能变成「删光所有备份」。

    ``plan_retention(daily_keep=0)`` 会把**每一份**未置 pin 的备份放进 prune 列表，
    而 ``prune_backups`` 是真删。配置值原样传到那里，所以 ``0`` —— 以及 ``false``
    （``bool`` 是 ``int`` 的子类）—— 静默地等于「一份都不留」，且不可逆。
    下界住在 ``_reload_config``，所以只测 ``plan_retention`` 是**守不住**这条的。
    """
    for configured in (0, False):
        ctx = _Ctx(base_url="http://127.0.0.1:1")
        # 直接改 `dignity_guard` 段。`_Ctx(config=...)` 是把键合并到**顶层**的
        # （`__init__` 里的 `**(config or {})`），所以写成
        # `config={"memory_backup_keep": 0}` 会静默落在段落之外、配置根本不生效
        # —— 那样这条测试就恒过，正是它要防的那类"假绿"。
        ctx._config["dignity_guard"]["memory_backup_keep"] = configured
        plugin = DignityGuardPlugin(ctx)
        asyncio.run(plugin.on_startup())
        try:
            assert plugin._memory_backup_keep >= 1, (
                "memory_backup_keep=%r 变成了 %r —— 那会把所有未置 pin 的备份删光"
                % (configured, plugin._memory_backup_keep)
            )
        finally:
            asyncio.run(plugin.on_shutdown())


def test_a_null_dry_run_still_only_plans(sandbox, monkeypatch) -> None:
    """``dry_run=None`` 必须留在安全的一侧：只出计划，不真还原。

    schema 声明它是 boolean，但宿主对 input_schema 型 entry **不做运行时校验** ——
    而这一位是「列一份计划」与「覆盖用户活文件」的分界。``None``（省略该键时以
    null 到达）曾经是假值，也就是「真还原」。
    """
    ctx = _Ctx(base_url="http://127.0.0.1:1")
    plugin = DignityGuardPlugin(ctx)
    asyncio.run(plugin.on_startup())
    try:
        root = sandbox / "memory"
        root.mkdir(parents=True, exist_ok=True)
        live = root / "facts.json"
        live.write_text('{"live": true}', encoding="utf-8")

        # 造一份可以「恢复自」的备份：备份根下一个目录，里面放同名相对路径。
        backup_root = sandbox / "backups"
        snapshot = backup_root / "2026-09-25-120000"
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / "facts.json").write_text('{"live": false}', encoding="utf-8")

        monkeypatch.setattr(plugin, "_resolve_memory_root", lambda: root)
        monkeypatch.setattr(plugin, "_memory_backup_root", lambda: backup_root)

        result = asyncio.run(plugin.restore_memory(dry_run=None))
        assert isinstance(result, Ok), result
        assert result.value["dry_run"] is True
        assert result.value["status"] == "planned"
        # 这条才是重点：所谓 dry-run，就是活文件一个字节都不许动。
        assert live.read_text(encoding="utf-8") == '{"live": true}', (
            "dry_run=None 被当成了真还原 —— 用户的活文件被覆盖了"
        )
    finally:
        asyncio.run(plugin.on_shutdown())


def test_a_failed_store_write_is_reported_not_hidden(sandbox, monkeypatch) -> None:
    """``persisted: false`` 是「没落盘」唯一的信号，不能被悄悄吞掉。

    这个插件存在的意义就是抓别的软件「报告成功、实际没干」。一个用户动作的状态
    写盘失败时，它不能干干净净地回一个 Ok —— 改动只活在内存里，重启就没了。
    面板据此给的是错误提示，而不是成功提示。
    """
    ctx = _Ctx(base_url="http://127.0.0.1:1")
    plugin = DignityGuardPlugin(ctx)
    asyncio.run(plugin.on_startup())
    try:
        async def always_fails(key: str, value: Any) -> bool:
            return False

        monkeypatch.setattr(plugin, "_store_write", always_fails)
        result = asyncio.run(plugin.set_guard_level(level="low"))
        assert isinstance(result, Ok), result
        assert result.value["persisted"] is False
        assert "state_not_persisted" in plugin._health._events
    finally:
        asyncio.run(plugin.on_shutdown())


def test_a_dispute_for_a_vanished_path_survives_its_own_tick() -> None:
    """路径消失**本身**是一次变更 —— 它必须留在面板上，而不是被建了就删。

    退休那一趟原先跑在 diff 循环**之后**：字段被删除时，同一 tick 先 ``_raise``
    建出 dispute、紧接着被这趟 ``del`` 掉 —— ``raised`` 报了她一句，面板上却什么
    都不剩，``_raise`` 做的 after_preview / times_raised 更新也全白做。
    修法是把退休那趟挪到 diff 循环**之前**；这条测试就是那个顺序的守门人。
    """
    state = GuardState()
    state.evaluate({"a.b": Value.of("x"), "keep.me": Value.of("k")})  # baseline
    state.evaluate({"a.b": Value.of("y"), "keep.me": Value.of("k")})
    assert "a.b" in state.disputes, "一次变更该建出一条 dispute"

    evaluation = state.evaluate({"keep.me": Value.of("k")})  # a.b 消失
    assert "a.b" in [change.path for change in evaluation.changes], (
        "路径消失是一次变更，必须被报出来"
    )
    assert "a.b" in state.disputes, (
        "她刚被报的这一条立刻被退休掉了 —— 面板会报一行、却什么都不剩"
    )

def test_away_restore_recognises_the_preferences_proactive_mirror(sandbox) -> None:
    """`preferences.<idx>.proactive*` 是 proactive 的**镜像路径**，必须被当"她的"。

    分级规则 `("preferences.*.proactive*", LEVEL_L1)` 认它是"她的自主权"
    （那条规则自己的注释写着 "autonomy wherever it is found"）——
    缺席恢复必须跟上，否则就成了"规则说它是她的、恢复却跳过它"。

    另一半同样要钉住：`preferences.<idx>.position.x`（窗口位置）**必须跳过** ——
    那是**用户自己的东西**，恢复不该动。
    """
    import asyncio
    from plugin.plugins.dignity_guard import DignityGuardPlugin
    from plugin.plugins.dignity_guard.settings_guard import SettingChange, Value

    class _Recorder:
        def __init__(self) -> None:
            self.proactive_calls = []
            self.catgirl_calls = []

        async def post_proactive_settings(self, partial):
            self.proactive_calls.append(dict(partial))
            return {}

        async def post_proactive_mode(self, mode):
            self.proactive_calls.append({"__mode__": mode})
            return {}

        async def fetch_characters_raw(self):
            # 刻意返回【被改后】的值 —— 恢复才真的需要写回。
            return {"猫娘": {"YUI": {"昵称": "被改成了别的"}}}

        async def put_catgirl(self, name, body):
            self.catgirl_calls.append((name, dict(body)))
            return {}

    # 不真连任何服务 —— 下面把 client 整个换掉，这里只为了让构造器通过。
    ctx = _Ctx(base_url="http://127.0.0.1:1")
    plugin = DignityGuardPlugin(ctx)
    rec = _Recorder()
    plugin._client = rec  # type: ignore[assignment]

    def _change(path: str, before: str, after: str) -> SettingChange:
        return SettingChange(
            path=path,
            level="L1",
            before=Value(digest=Value.of(before).digest, preview=before, kind="string"),
            after=Value(digest=Value.of(after).digest, preview=after, kind="string"),
        )

    async def scenario() -> dict:
        return {
            # 镜像路径 → 走 proactive 写口
            "mirror": await plugin._restore_change_after_absence(
                _change("preferences.2.proactiveChatEnabled", True, False)
            ),
            # 窗口位置 → 用户的东西，必须跳过
            "position": await plugin._restore_change_after_absence(
                _change("preferences.2.position.x", "100", "200")
            ),
            # 模型路径 → 同样是用户的东西
            "model": await plugin._restore_change_after_absence(
                _change("preferences.2.model_path", "a", "b")
            ),
            # 角色卡字段 → 走被动那条路
            "catgirl": await plugin._restore_change_after_absence(
                _change("characters.猫娘.YUI.昵称", "先前的值", "被改成了别的")
            ),
        }

    result = asyncio.run(scenario())

    assert result["mirror"] == "", "镜像路径应当被恢复"
    assert rec.proactive_calls == [{"proactiveChatEnabled": True}], (
        "镜像应当转成 proactive 写口、写回旧值；实际 %r" % (rec.proactive_calls,)
    )
    assert result["position"] == "not_restorable", "窗口位置是用户的，不该动"
    assert result["model"] == "not_restorable", "模型路径是用户的，不该动"
    assert result["catgirl"] == "", "角色卡字段应当被恢复"
    assert rec.catgirl_calls and rec.catgirl_calls[0][0] == "YUI", (
        "角色卡字段应当被写回；实际调用 %r" % (rec.catgirl_calls,)
    )
    assert rec.catgirl_calls[0][1].get("昵称") == "先前的值", "应当写回旧值"
