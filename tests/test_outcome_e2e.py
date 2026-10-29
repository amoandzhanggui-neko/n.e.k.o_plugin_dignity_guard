"""端到端：三个新字段**真的会被填上值**，不是"字段存在"。

## 这个文件补的是什么

契约 CT-v2 的未证实项 **U3** 原话：

> 三个新字段 `block_reported` / `notified` / `status_after` 只有单元级验证，
> 没有端到端验证。现在做的只是把 `RoundOutcome` / `_state_after` / `_delivered`
> 三块单独测通；**"它们真的被填上值"这件事，还没有任何一条测试证明过**。

`test_rev_contract.py` 里那几条测的是"零件对不对"（`as_report()` 的键名、六种取值、
回执口径）。这个文件测的是**装配起来之后**对不对：

    真实方法 `_run_reverts` → `_push_blocked_notice` → `_summary`

一路走下来，`summary["block_reported"]` 到底是不是 `True`。

## 它不是"真启动插件"，边界要说清

真启动要宿主 ctx（`NekoPluginBase.__init__(self, ctx)`），这里没有。
所以做法是：**方法体全用真的，外部依赖全打桩**（push_message / _revert_change / logger / health）。

- ✅ 它证明：真实代码路径会把字段填上正确的值，包括"通知失败时是 False 不是 None"。
- ❌ 它不证明：插件在宿主进程里跑得起来（那是完工定义第 4 条，看日志）。

把这两件事混起来，就是"测试通过 ≠ 插件能跑"的老毛病 —— 已经在 skill 里写死要分开看。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.dignity_guard import DignityGuardPlugin, RoundOutcome
from plugin.plugins.dignity_guard.settings_guard import (
    LEVEL_L1,
    TIER_HIGH,
    Dispute,
    Evaluation,
    SettingChange,
    Value,
)

PERSONA = "characters.猫娘.雪.核心特质"


def _change(path: str = PERSONA) -> SettingChange:
    return SettingChange(
        path=path,
        level=LEVEL_L1,
        before=Value.of(["理智"]),
        after=Value.of(["暴躁"]),
    )


def _plugin(*, submit: bool = True, revert_reason: str = "baseline_lost"):
    """造一个"外形是假的、方法是真的"插件。

    返回 ``(fake_self, pushed)``：``pushed`` 收所有 push_message 的调用参数，
    用来断言"到底发没发、走的哪条通道"。
    """
    pushed: list[dict] = []

    async def fake_revert_change(change):  # noqa: ANN001
        return revert_reason

    def fake_push(**kwargs):
        pushed.append(kwargs)
        return {"submitted": submit}

    fake = SimpleNamespace(
        _outcome=RoundOutcome(),
        _revert_outcomes={},
        _watcher=None,
        _health=SimpleNamespace(record=lambda *_: None),
        logger=SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
        ),
        _revert_change=fake_revert_change,
        push_message=fake_push,
        _push_target=lambda: {"target_lanlan": "YUI"},
        _text=lambda key, default="", **params: default,
        _speak_her_turn=lambda disputes: None,
        _state=SimpleNamespace(pending=lambda: []),
    )
    # `_summary` 会调 `self._state_after(...)`，`_run_reverts` 会调 `self._push_blocked_notice(...)`，
    # `_speak` 会调 `self._delivered(...)`。不挂上就会 AttributeError。
    # 这几条依赖是真跑测试才发现的 —— 也说明链路是真的被走通了，不是把整段 mock 掉。
    #
    # ⚠ 顺带记一个真问题：`_speak` 里的 `except Exception` 会把 AttributeError 这种
    #   **编程错误**当成"通知发送失败"吞掉，只记一条 warning。第一次跑这个测试时就是
    #   因此拿到 `delivered=False` 而不是崩。生产里的后果：一处笔误 ⟹ 通知永远发不出去，
    #   而日志只说"没送到"，看不出是代码 bug。已列入第 5 轮待办，本轮不改（属行为变更）。
    fake._state_after = lambda ev: DignityGuardPlugin._state_after(fake, ev)
    fake._push_blocked_notice = lambda paths, reasons=None: DignityGuardPlugin._push_blocked_notice(
        fake, paths, reasons
    )
    fake._delivered = DignityGuardPlugin._delivered
    fake._note_delivery_failure = lambda p, e: DignityGuardPlugin._note_delivery_failure(fake, p, e)
    return fake, pushed


# ────────────── blocked 路径：三个字段一起被填上 ──────────────


def test_blocked_path_fills_all_three_fields() -> None:
    """想放回但放不回 ⟹ blocked / block_reported / status_after 全部有真值。

    这就是 U3 缺的那条实验。断言的是**真实调用链**的结果，不是零件单独的行为。
    """
    fake, pushed = _plugin(revert_reason="baseline_lost")
    evaluation = Evaluation()
    evaluation.revert_blocked = [PERSONA]

    asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))
    summary = DignityGuardPlugin._summary(fake, evaluation)

    assert summary["blocked"] is True, "有放不回的，blocked 必须是 True"
    assert summary["revert"] is False, "这一项没放回去，revert 必须是 False"
    assert summary["block_reported"] is True, "拦住时必须真的报出去（U3 要的这一条）"
    assert summary["status_after"] == "blocked"

    # 这一轮没走 _speak，所以 notified 必须是 None —— **不是 False**。
    # 写成 False 就是凭空造出一次"通知失败"，日志里会出现根本没发生的故障。
    assert summary["notified"] is None

    # 而且真的发了一条消息，走的是只读通道（系统告知，不是她表态）
    assert len(pushed) == 1
    assert pushed[0]["ai_behavior"] == "read"
    assert pushed[0]["visibility"] == ["chat"]


def test_blocked_notice_failure_reports_false_not_none() -> None:
    """通知发不出去时，block_reported 是 **False**（不是 None、也不是 True）。

    None 的意思是"没走到这条分支"，False 的意思是"走到了、失败了"。
    把 False 写成 None，就是一次真实失败被藏起来 —— 而"沉默地没做"正是这个插件要抓的东西。
    """
    fake, pushed = _plugin(submit=False, revert_reason="baseline_lost")
    evaluation = Evaluation()
    evaluation.revert_blocked = [PERSONA]

    asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))
    summary = DignityGuardPlugin._summary(fake, evaluation)

    assert len(pushed) == 1, "尝试过发送"
    assert summary["block_reported"] is False, "发了但没递到，必须是 False"
    assert summary["blocked"] is True, "拦住了这件事本身没变"


def test_successful_revert_does_not_report_blocked() -> None:
    """放回去了 ⟹ revert=True、blocked=False、block_reported=None（没拦，不写 false）。"""
    fake, pushed = _plugin(revert_reason="")  # 空字符串 = 成功
    evaluation = Evaluation()
    evaluation.to_revert = [_change()]

    asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))
    summary = DignityGuardPlugin._summary(fake, evaluation)

    assert summary["revert"] is True
    assert summary["blocked"] is False
    assert summary["block_reported"] is None, "没拦就没这条通知，不能写 False"
    assert summary["status_after"] == "reverted"
    assert pushed == [], "成功回滚不该发 blocked 通知"


# ────────────── notified：走真实的 _speak ──────────────


def test_notified_true_when_notice_delivered() -> None:
    """``_speak`` 的返回值就是 notified 的来源，且它会被写进 _outcome。"""
    fake, pushed = _plugin(submit=True)
    evaluation = Evaluation()
    evaluation.raised = [_dispute()]

    delivered = DignityGuardPlugin._speak(fake, evaluation)

    assert delivered is True
    assert pushed, "通知必须真的发出去"
    assert pushed[0]["ai_behavior"] == "read", "通知走只读通道，不触发 AI 回合"


def test_notified_false_when_host_rejects() -> None:
    """宿主回执 submitted=False ⟹ notified=False。不是"调用了就算成功"。"""
    fake, _ = _plugin(submit=False)
    evaluation = Evaluation()
    evaluation.raised = [_dispute()]

    assert DignityGuardPlugin._speak(fake, evaluation) is False


def test_notified_flows_into_summary() -> None:
    """端到端：``_speak`` 的结果经 ``_outcome`` 流到 ``_summary['notified']``。"""
    fake, _ = _plugin(submit=True)
    evaluation = Evaluation()
    evaluation.raised = [_dispute()]

    fake._outcome.notice_delivered = DignityGuardPlugin._speak(fake, evaluation)
    summary = DignityGuardPlugin._summary(fake, evaluation)

    assert summary["notified"] is True


# ────── T4：投递失败要分清「外部故障」和「我们写错了」 ──────
#
# 为什么单独立这一节：这四处都是 `except Exception`，会把 AttributeError 一起吞掉。
# 2026-10-25 实测踩到过 —— 假对象少挂一个方法，本该崩，结果拿到 delivered=False，
# 日志只说"没送到"。于是排查的人会去查宿主和网络，而真正的问题是自己少写了一个方法。


def test_programming_error_recorded_as_code_error() -> None:
    """AttributeError / TypeError / NameError / UnboundLocalError ⟹ 记 code_error。

    这四个的共同点：几乎只可能来自代码写错。宿主不会让你的属性消失。
    """
    records: list[str] = []
    fake, _ = _plugin()
    fake._health = SimpleNamespace(record=lambda k: records.append(k))
    fake.logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        exception=lambda *a, **k: None,
    )

    for exc in (
        AttributeError("no such attr"),
        TypeError("wrong arg"),
        NameError("undefined"),
        UnboundLocalError("local"),
    ):
        records.clear()
        DignityGuardPlugin._note_delivery_failure(fake, "notice", exc)
        assert records == ["notice_code_error"], f"{type(exc).__name__} 应记 code_error"


def test_external_failure_recorded_as_not_delivered() -> None:
    """网络/宿主类异常 ⟹ 记 not_delivered（沿用原来的名字，不破坏看板口径）。"""
    records: list[str] = []
    fake, _ = _plugin()
    fake._health = SimpleNamespace(record=lambda k: records.append(k))
    fake.logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        exception=lambda *a, **k: None,
    )

    for exc in (TimeoutError("host slow"), OSError("socket closed"), RuntimeError("busy")):
        records.clear()
        DignityGuardPlugin._note_delivery_failure(fake, "notice", exc)
        assert records == ["notice_not_delivered"], f"{type(exc).__name__} 应记 not_delivered"


def test_speak_programming_error_is_not_mistaken_for_delivery_failure() -> None:
    """端到端：``_speak`` 内部抛出编程错误时，**不崩**，但记的是 code_error。

    这是 T4 的核心断言。两条同时成立才算对：
      · 仍然不 re-raise（一次失败打死轮询定时器是更严重的毛病）
      · 但名字必须是"我们写错了"，不是"没送到"
    """
    records: list[str] = []
    fake, _ = _plugin()
    fake._health = SimpleNamespace(record=lambda k: records.append(k))
    fake.logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        exception=lambda *a, **k: None,
    )
    # 拆掉 _delivered，让 _speak 内部抛 AttributeError
    del fake._delivered

    evaluation = Evaluation()
    evaluation.raised = [_dispute()]

    delivered = DignityGuardPlugin._speak(fake, evaluation)

    assert delivered is False, "仍然不抛出去 —— 不能打死整轮轮询"
    assert records == ["notice_code_error"], "但必须记成 code_error，不能混成 not_delivered"


# ────── T9：`logger.exception` 不能在没有 except 的地方假装带堆栈 ──────


def _logger_probe():
    """返回 (fake, errors, exceptions)：两个列表分别收 logger.error / logger.exception 的调用。"""
    errors: list[tuple] = []
    exceptions: list[tuple] = []
    fake, _ = _plugin()
    fake._health = SimpleNamespace(record=lambda k: None)
    fake.logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: errors.append(a),
        exception=lambda *a, **k: exceptions.append(a),
    )
    return fake, errors, exceptions


def test_inside_except_block_uses_exception_logger() -> None:
    """在 `except` 块内调用 ⟹ 用 `logger.exception`（有堆栈，能看出是哪一行）。"""
    fake, errors, exceptions = _logger_probe()

    try:
        raise AttributeError("boom")
    except AttributeError as exc:
        DignityGuardPlugin._note_delivery_failure(fake, "notice", exc)

    assert len(exceptions) == 1, "except 块内应该用 logger.exception"
    assert errors == []


def test_outside_except_block_degrades_to_error() -> None:
    """在 `except` 块外调用 ⟹ 降级成 `logger.error` 并写明没有堆栈。

    `logger.exception` 在 except 外只会打出 `NoneType: None` ——
    看起来像带全了现场，实际什么都带不到。**那比不记更糟**，因为它让人以为查过了。
    """
    fake, errors, exceptions = _logger_probe()

    DignityGuardPlugin._note_delivery_failure(fake, "notice", AttributeError("boom"))

    assert exceptions == [], "没有活着的异常时不该调 logger.exception"
    assert len(errors) == 1, "应降级成 logger.error"
    assert "outside its except block" in errors[0][0], "且必须在文案里说明，别让人以为漏了堆栈"


def _dispute() -> Dispute:
    return Dispute(
        path=PERSONA,
        level=LEVEL_L1,
        base_digest=None,
        raised_at=1000.0,
        seen_at=1000.0,
        before_preview="理智",
        after_preview="暴躁",
    )


# ── 2026-10-28：契约 CT-v2 的 error / source 两个字段（A 方案补实现后补样例）──

def test_source_names_path_when_exactly_one_revert() -> None:
    """契约 source：恰好一条回滚时，必须指名道姓（不许用 None 糊过去）。"""
    fake, _ = _plugin(revert_reason="")
    evaluation = Evaluation()
    evaluation.to_revert = [_change()]
    asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))
    report = DignityGuardPlugin._summary(fake, evaluation)
    assert report["source"] == [_change().path], "一条就报一条，用列表"


def test_source_lists_every_reverted_path() -> None:
    """契约 source：几条就报几条；0 条 ⟹ 空列表（不用 None 表示"没有"）。"""
    fake, _ = _plugin(revert_reason="")
    for items, expected in (([], []), ([_change("a.b"), _change("c.d")], ["a.b", "c.d"])):
        evaluation = Evaluation()
        evaluation.to_revert = list(items)
        asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))
        assert DignityGuardPlugin._summary(fake, evaluation)["source"] == expected


def test_error_is_none_on_normal_path() -> None:
    """契约 error：正常路径必须是 None（不能拿 blocked 的 reason 冒充）。"""
    fake, _ = _plugin(revert_reason="baseline_lost")
    evaluation = Evaluation()
    evaluation.to_revert = [_change()]
    asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))
    assert DignityGuardPlugin._summary(fake, evaluation)["error"] is None


def test_error_records_exception_without_aborting_the_round() -> None:
    """契约 error：单条回滚抛异常 ⟹ 记错误码，且这条算拦住（不算放回去了）。"""
    fake, _ = _plugin(revert_reason="")

    async def boom(change):  # noqa: ANN001
        raise RuntimeError("kaboom")

    fake._revert_change = boom
    evaluation = Evaluation()
    evaluation.to_revert = [_change()]
    asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))
    report = DignityGuardPlugin._summary(fake, evaluation)
    assert report["error"] == "revert_exception:RuntimeError"
    assert report["blocked"] is True, "抛异常的那条要算拦住，不能算放回去了"
    assert report["revert"] is False


def test_source_is_empty_when_the_single_revert_fails() -> None:
    """契约「未回滚 ⟹ 空列表」：单条失败时 source 必须是 []，不许照样填路径。"""
    fake, _ = _plugin(revert_reason="baseline_lost")
    evaluation = Evaluation()
    evaluation.to_revert = [_change()]
    asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))
    report = DignityGuardPlugin._summary(fake, evaluation)
    assert report["revert"] is False
    assert report["source"] == [], "没放回去就没有可指的路径"


def test_exception_on_one_path_keeps_the_others_going() -> None:
    """混合场景：一条崩溃、另一条成功 ⟹ error 记下，成功的那条仍算 revert。"""
    fake, pushed = _plugin(revert_reason="")

    async def mixed(change):  # noqa: ANN001
        if change.path == "chars.one":
            raise RuntimeError("kaboom")
        return ""

    fake._revert_change = mixed
    evaluation = Evaluation()
    evaluation.to_revert = [_change("chars.one"), _change("chars.two")]
    asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))
    report = DignityGuardPlugin._summary(fake, evaluation)
    assert report["error"] == "revert_exception:RuntimeError"
    assert report["blocked"] is True, "崩的那条要算拦住"
    assert report["revert"] is True, "另一条成功了，revert 必须是 True"
    assert report["source"] == ["chars.two"], "只有真的回去的那条进列表"
    assert pushed, "拦住的那条必须留下用户可见出口"


def test_exception_uses_its_own_wording() -> None:
    """崩溃不能对用户说「证明不了原值是你的」—— 那是假话。"""
    import json as _json

    fake, pushed = _plugin(revert_reason="")

    async def boom(change):  # noqa: ANN001
        raise RuntimeError("kaboom")

    fake._revert_change = boom
    evaluation = Evaluation()
    evaluation.to_revert = [_change()]
    asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))
    blob = _json.dumps(pushed, ensure_ascii=False)
    assert "出错了" in blob, "崩溃要用它自己的说法"
    assert "证明" not in blob, "不许把崩溃说成「证明不了原值」"


def test_source_and_blocked_partition_the_targets() -> None:
    """分区不变式：成功的进 source、失败的进 blocked，并集 = 本轮全部目标。

    为什么单立一条：``source`` 是 ``{present}``（空列表也合法），
    "该有却空"和"本来没有"形状一样，单看允许态永远判不了红。
    唯一能抓的是这条等式 —— 两个集合漏掉任何一条路径都会当场不成立。
    """
    fake, _ = _plugin(revert_reason="")

    async def mixed(change):  # noqa: ANN001
        if change.path == "chars.bad":
            raise RuntimeError("kaboom")
        return ""

    fake._revert_change = mixed
    evaluation = Evaluation()
    evaluation.to_revert = [_change("chars.ok"), _change("chars.bad")]
    evaluation.revert_blocked = ["chars.nobase"]
    asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))

    report = DignityGuardPlugin._summary(fake, evaluation)
    assert set(report["source"]) == {"chars.ok"}, "只有真的回去的才算 source"
    assert set(fake._outcome.blocked) == {"chars.bad", "chars.nobase"}
    assert set(report["source"]) | set(fake._outcome.blocked) == {
        "chars.ok", "chars.bad", "chars.nobase"
    }, "并集必须等于本轮全部目标，少一条就是丢路径"


# ── 2026-10-29：补 M2-6 / M2-7 两条反例（完工定义第 1 条要「正例+反例」）──

def test_notified_false_reaches_summary_when_host_rejects() -> None:
    """反例：推送失败时 ``summary['notified']`` 必须是 False。

    正例（``test_notified_flows_into_summary``）只证明 True 能流下去；
    不补这一条，"调用了就算送达"这种写法照样能全绿 —— 而那正是本插件要抓的假成功。
    """
    fake, _ = _plugin(submit=False)
    evaluation = Evaluation()
    evaluation.raised = [_dispute()]

    fake._outcome.notice_delivered = DignityGuardPlugin._speak(fake, evaluation)
    summary = DignityGuardPlugin._summary(fake, evaluation)

    assert summary["notified"] is False, "宿主明确回执失败，不许写成 True"


def test_status_after_prefers_blocked_over_reverted() -> None:
    """反例：同一轮里既有成功放回、也有放不回的 ⟹ 必须是 ``blocked``。

    优先级写反（先看 put_back）时，这条会输出 ``reverted`` —— 用户看到"已修复"，
    实际上还有一条没回去。这是六值里最容易写错的一处排序。
    """
    fake, _ = _plugin(revert_reason="")

    async def mixed(change):  # noqa: ANN001
        return "baseline_lost" if change.path == "chars.bad" else ""

    fake._revert_change = mixed
    evaluation = Evaluation()
    evaluation.to_revert = [_change("chars.ok"), _change("chars.bad")]
    asyncio.run(DignityGuardPlugin._run_reverts(fake, evaluation))

    summary = DignityGuardPlugin._summary(fake, evaluation)
    assert summary["revert"] is True, "确实有一条放回去了"
    assert summary["status_after"] == "blocked", "有放不回的，就必须先说坏消息"
