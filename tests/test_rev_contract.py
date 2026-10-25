"""C-REV-1 / C-REV-2 契约测试（2026-10-25 首次真写）

═══════════════════════════════════════════════════════════════════════
⚠ 写之前必须交代的一件事：**契约声明的 7 个 out 字段，实现里只有 3 个。**

| CT-v2 声明的字段 | 实现里 | 说明 |
|---|---|---|
| `error`         | **无** | 靠返回值/异常表达，没有这个字段 |
| `revert`        | ✅ `Evaluation.reverted` / `to_revert` | 名字不同，语义对应 |
| `source`        | **无独立字段** | 只有内部方法 `GuardState._revert_source` |
| `blocked`       | ⚠️ `Evaluation.revert_blocked` | 名字不同，且是**内部状态**不是对外 payload |
| `block_reported`| **无** | 契约要求"必须有对外出口"，这个出口没实现 |
| `notified`      | **无** | 同上 |
| `status_after`  | **无** | 同上 |

⟹ 所以本文件**不能在"契约字面"上写**（那会断言一堆不存在的字段，必然全红）。
   本文件的做法是：**按实现里真实存在的字段断言行为**，并额外加一条测试把上面这个缺口钉死。
   缺口一变（实现了新字段），那条测试就会失败，逼人回来更新 CT-v2 和这张映射表。

这条差异让 C-REV-1/C-REV-2 处于「**部分实现**」状态，不是「已实现」。
按 skill §5 口径，交付三分类里仍应记为「未实现」，直到对外 payload 补齐或契约改写。
═══════════════════════════════════════════════════════════════════════

跑法（必须从宿主仓库跑，否则插件根 __init__.py 会被当测试收集）：
    cd N.E.K.O && python -m pytest plugin/plugins/dignity_guard/tests/ -q -k "rev"
"""

from __future__ import annotations

from plugin.plugins.dignity_guard.settings_guard import (
    MAX_PERSISTED_RAW_BYTES,
    TIER_HIGH,
    TIER_MEDIUM,
    Evaluation,
    GuardState,
    Value,
)

# 与 test_guard_tiers 保持同一组路径（L1 层，可回滚）
PERSONA = "characters.猫娘.雪.核心特质"
NICKNAME = "characters.猫娘.雪.昵称"

_BASE: dict[str, object] = {PERSONA: ["理智"], NICKNAME: "雪", "preferences.model-a.position": {"x": 1.0}}


def _snap(payload: dict[str, object]) -> dict[str, Value]:
    return {path: Value.of(raw) for path, raw in payload.items()}


def _run(tier: str, **overrides: object):
    """设基线 → 改一版 → 返回 (state, evaluation)。"""
    state = GuardState()
    state.evaluate(_snap(_BASE), now=1000.0)
    after = dict(_BASE)
    after.update(overrides)
    return state, state.evaluate(_snap(after), now=1001.0, tier=tier)


# ───────────────────────── C-REV-1 单次改动 ─────────────────────────


def test_rev1_high_tier_marks_exactly_what_she_named() -> None:
    """C-REV-1 正例：high 档只把她点名的字段标记为"要放回去"。

    对应契约 out 里的 `revert`（→ `Evaluation.to_revert`）与 `source`（→ 变更项自带 path）。
    未点名的 L3 字段（position）必须原样不动 —— 这是"只改她说的"那一半。
    """
    state, evaluation = _run(
        TIER_HIGH, **{PERSONA: ["随便"], NICKNAME: "阿雪", "preferences.model-a.position": {"x": 9.9}}
    )
    assert {c.path for c in evaluation.to_revert} == {PERSONA, NICKNAME}
    # 未点名的 L3 不进待回滚
    assert "preferences.model-a.position" not in {c.path for c in evaluation.to_revert}
    # 已决定回滚的，不能同时出现在"被拦"里
    assert evaluation.revert_blocked == []


def test_rev1_cannot_restore_degrades_to_speaking_only() -> None:
    """C-REV-1 反例：想回滚但拿不到可信原值 ⟹ 不许写回，只能报告。

    契约里把它叫 `blocked` + `block_reported`；实现里只有内部状态 `revert_blocked`。
    **这就是契约要求"必须有对外出口"而实现没做到的地方** —— 所以这里只能断言内部状态。
    """
    oversized = "x" * (MAX_PERSISTED_RAW_BYTES + 1)
    truncated = Value.from_payload(Value.of(oversized).to_payload())
    assert truncated.raw is None and truncated.truncated, "前提：这个值必须已经不可还原"

    state = GuardState()
    state.evaluate({PERSONA: truncated, NICKNAME: Value.of("雪")}, now=1000.0)
    evaluation = state.evaluate(
        {PERSONA: Value.of("短"), NICKNAME: Value.of("雪")}, now=1001.0, tier=TIER_HIGH
    )
    # 她确实改了 → 要报告
    assert [d.path for d in evaluation.raised] == [PERSONA]
    # 但不可写回 → to_revert 空
    assert evaluation.to_revert == []


def test_rev1_medium_tier_never_reverts() -> None:
    """C-REV-1 的边界：medium 档只提问，不动手。"""
    _, evaluation = _run(TIER_MEDIUM, **{PERSONA: ["随便"]})
    assert evaluation.to_revert == []


# ───────────────────────── C-REV-2 同 path 第二次 ─────────────────────────


def test_rev2_second_change_inside_ttl_stays_authorized() -> None:
    """C-REV-2 正例：TTL 内、她此前同意过 ⟹ 不再重复提问。

    对应契约 out 的 `status_after`（→ 实现里没有这个字段，改用"是否再次 raise"表达）。
    """
    state = GuardState()
    state.evaluate(_snap(_BASE), now=1000.0)
    state.evaluate(_snap({**_BASE, NICKNAME: "阿雪"}), now=1001.0)
    # 她同意，并给一个 TTL
    state.accept(NICKNAME, ttl_seconds=60.0, now=1002.0)

    evaluation = state.evaluate(_snap({**_BASE, NICKNAME: "小雪"}), now=1003.0)
    assert [d.path for d in evaluation.raised] == [], "TTL 内不该再问一次"


def test_rev2_change_after_ttl_raises_again() -> None:
    """C-REV-2 反例：TTL 已过 ⟹ 必须重新问，不能把她此前的同意无限延用。"""
    state = GuardState()
    state.evaluate(_snap(_BASE), now=1000.0)
    state.evaluate(_snap({**_BASE, NICKNAME: "阿雪"}), now=1001.0)
    state.accept(NICKNAME, ttl_seconds=10.0, now=1002.0)

    evaluation = state.evaluate(_snap({**_BASE, NICKNAME: "小雪"}), now=1100.0)  # 远超 TTL
    assert [d.path for d in evaluation.raised] == [NICKNAME], "TTL 过了必须重新问"


def test_rev2_rejecting_revokes_the_grant() -> None:
    """C-REV-2 的另一半：拒绝必须撤销同意，否则"她说不"就是一次性的。"""
    state = GuardState()
    state.evaluate(_snap(_BASE), now=1000.0)
    state.evaluate(_snap({**_BASE, NICKNAME: "阿雪"}), now=1001.0)
    state.accept(NICKNAME, ttl_seconds=None, now=1002.0)

    assert state.reject(NICKNAME, now=1003.0) is True
    evaluation = state.evaluate(_snap({**_BASE, NICKNAME: "小雪"}), now=1004.0)
    assert [d.path for d in evaluation.raised] == [NICKNAME]


# ─────────────────── 把"契约 ≠ 实现"这个缺口钉死 ───────────────────


def test_rev_contract_missing_out_fields_are_still_missing() -> None:
    """CT-v2 声明了 7 个 out 字段，其中 3 个在实现里根本不存在。

    这条测试的用意**不是**容忍缺口，而是让缺口显式化：
    一旦有人真的实现了 `block_reported` / `notified` / `status_after`，
    这条就会失败，逼他回来更新 CT-v2 的映射表、把 C-REV-1/2 的分类从"未实现"改掉。

    没有这条，缺口会被永远忘记 —— 那就是"看起来在跑、其实没跑"。
    """
    fields = set(Evaluation.__dataclass_fields__)
    still_missing = [n for n in ("block_reported", "notified", "status_after") if n not in fields]
    assert still_missing == ["block_reported", "notified", "status_after"], (
        "缺口变了！实现里已经出现了上面列不出的字段之一。"
        "请更新 CT-v2 的「契约字段 → 实际字段」映射表，并重新判定 C-REV-1/C-REV-2 的三分类。"
    )
