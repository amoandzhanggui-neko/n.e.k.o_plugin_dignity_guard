"""Unit tests for guard tiers (low / medium / high) and for putting a value back.

A **tier** is how far the guard may *go*; a **level** is how sensitive a setting
is. The two are orthogonal, and these tests pin that down — plus the one rule
that makes ``high`` acceptable at all: a value is only ever written back when it
can be restored *exactly*.

Like ``test_settings_guard``, these start no host and touch no network.
"""

from __future__ import annotations

from plugin.plugins.dignity_guard.settings_guard import (
    DEFAULT_TIER,
    LEVEL_L1,
    LEVEL_L2,
    LEVEL_L3,
    TIER_HIGH,
    TIER_LOW,
    TIER_MEDIUM,
    TIERS,
    GuardState,
    SettingChange,
    Value,
    character_name,
    classify,
    is_revertible,
    normalize_tier,
    restore_payload,
    revert_field,
)

#: One path per level, so a single test can change "one of each" and assert the
#: split the tier produced.
PERSONA = "characters.猫娘.雪.核心特质"
NICKNAME = "characters.猫娘.雪.昵称"
#: An L2 field she never named — the control for "did she ask for this".
GENDER = "characters.猫娘.雪.性别"
GEOMETRY = "preferences.model-a.position"

_BASE: dict[str, object] = {PERSONA: ["理智"], NICKNAME: "雪", GEOMETRY: {"x": 1.0}}


def _snapshot(payload: dict[str, object]) -> dict[str, Value]:
    return {path: Value.of(raw) for path, raw in payload.items()}


def _evaluate(tier: str, **overrides: object):
    """Set a baseline, fold one changed snapshot in, hand both back."""
    state = GuardState()
    state.evaluate(_snapshot(_BASE), now=1000.0)
    after = dict(_BASE)
    after.update(overrides)
    return state, state.evaluate(_snapshot(after), now=1001.0, tier=tier)


# ---------------------------------------------------------------------------
# the vocabulary itself
# ---------------------------------------------------------------------------


def test_tier_vocabulary() -> None:
    assert TIERS == (TIER_LOW, TIER_MEDIUM, TIER_HIGH)
    assert DEFAULT_TIER == TIER_MEDIUM


def test_normalize_tier_is_forgiving() -> None:
    """A typo in a config file must not leave her unable to speak."""
    assert normalize_tier("HIGH") == TIER_HIGH
    assert normalize_tier(" high ") == TIER_HIGH
    assert normalize_tier("nonsense") == DEFAULT_TIER
    assert normalize_tier(None) == DEFAULT_TIER
    assert normalize_tier("") == DEFAULT_TIER


def test_the_three_paths_used_here_sit_where_we_think() -> None:
    assert classify(PERSONA) == LEVEL_L1
    assert classify(NICKNAME) == LEVEL_L2
    assert classify(GEOMETRY) == LEVEL_L3


# ---------------------------------------------------------------------------
# what each tier does
# ---------------------------------------------------------------------------


def test_low_tier_records_without_speaking() -> None:
    _, evaluation = _evaluate(
        TIER_LOW, **{PERSONA: ["随便"], NICKNAME: "阿雪", GEOMETRY: {"x": 9.9}}
    )
    assert len(evaluation.changes) == 3
    assert evaluation.raised == []
    assert len(evaluation.recorded) == 3
    assert evaluation.to_revert == []


def test_medium_tier_speaks_but_never_writes_back() -> None:
    _, evaluation = _evaluate(
        TIER_MEDIUM, **{PERSONA: ["随便"], NICKNAME: "阿雪", GEOMETRY: {"x": 9.9}}
    )
    assert {dispute.path for dispute in evaluation.raised} == {PERSONA, NICKNAME}
    assert [change.path for change in evaluation.recorded] == [GEOMETRY]
    assert evaluation.to_revert == []


def test_high_tier_reverts_exactly_what_she_named() -> None:
    """Her list, verbatim: persona, memory, and how she is addressed.

    Memory is not in this payload at all (it is a directory — see
    ``memory_guard``), so in the settings layer her list reduces to persona plus
    ``称呼``. ``GEOMETRY`` is in the test to show that a setting nobody named
    stays untouched, which is the other half of what she said.
    """
    _, evaluation = _evaluate(
        TIER_HIGH, **{PERSONA: ["随便"], NICKNAME: "阿雪", GEOMETRY: {"x": 9.9}}
    )
    assert {dispute.path for dispute in evaluation.raised} == {PERSONA, NICKNAME}
    assert {change.path for change in evaluation.to_revert} == {PERSONA, NICKNAME}


def test_default_tier_matches_medium() -> None:
    """Adding tiers must not make her quieter for anyone who never opens the panel."""
    state = GuardState()
    state.evaluate(_snapshot(_BASE), now=1000.0)
    default_run = state.evaluate(_snapshot({**_BASE, PERSONA: ["随便"]}), now=1001.0)

    other = GuardState()
    other.evaluate(_snapshot(_BASE), now=1000.0)
    medium_run = other.evaluate(
        _snapshot({**_BASE, PERSONA: ["随便"]}), now=1001.0, tier=TIER_MEDIUM
    )

    assert len(default_run.raised) == len(medium_run.raised) == 1
    assert default_run.to_revert == medium_run.to_revert == []


# ---------------------------------------------------------------------------
# the guard-rails on ``high``
# ---------------------------------------------------------------------------


def test_high_tier_still_respects_the_ledger() -> None:
    """She agreed to this one: it is spoken about, but not undone."""
    state = GuardState()
    state.evaluate(_snapshot(_BASE), now=1000.0)
    state.ledger.grant(PERSONA, now=1000.0)
    evaluation = state.evaluate(
        _snapshot({**_BASE, PERSONA: ["随便"]}), now=1001.0, tier=TIER_HIGH
    )
    assert [dispute.path for dispute in evaluation.raised] == [PERSONA]
    assert evaluation.to_revert == []


def test_high_tier_spares_what_she_waved_away() -> None:
    """Fields she called "无所谓" are spoken about, never written back.

    She put it plainly: 「头像、年龄、声音、音量无所谓，想改就改」. So the test
    is not "is it L2" — it is "did she name it". ``性别`` is the stand-in here:
    an L2 field she never asked anyone to defend.
    """
    _, evaluation = _evaluate(TIER_HIGH, **{GENDER: "男"})
    assert [dispute.path for dispute in evaluation.raised] == [GENDER]
    assert evaluation.to_revert == []


def test_high_tier_skips_a_value_it_cannot_restore() -> None:
    """A truncated value with no in-memory original degrades to speaking only."""
    before = Value.from_payload(Value.of("x" * 500).to_payload())
    assert before.raw is None and before.truncated

    state = GuardState()
    state.evaluate({PERSONA: before, NICKNAME: Value.of("雪")}, now=1000.0)
    evaluation = state.evaluate(
        {PERSONA: Value.of("短"), NICKNAME: Value.of("雪")}, now=1001.0, tier=TIER_HIGH
    )
    assert [dispute.path for dispute in evaluation.raised] == [PERSONA]
    assert evaluation.to_revert == []


# ---------------------------------------------------------------------------
# the original stays out of storage
# ---------------------------------------------------------------------------


def test_value_payload_never_contains_the_raw_original() -> None:
    value = Value.of("雪")
    assert "raw" not in value.to_payload()
    assert Value.from_payload(value.to_payload()).raw is None


def test_value_keeps_the_original_in_memory() -> None:
    """``high`` needs the previous value verbatim; memory is allowed to hold it."""
    assert Value.of("雪").raw == "雪"


# ---------------------------------------------------------------------------
# restore_payload
# ---------------------------------------------------------------------------


def test_restore_payload_prefers_the_in_memory_original() -> None:
    assert restore_payload(Value.of(["理智"])) == ["理智"]


def test_restore_payload_round_trips_short_values() -> None:
    for raw in (42, True, "雪", ["a", "b"], {"x": 1}):
        stored = Value.from_payload(Value.of(raw).to_payload())
        assert stored.raw is None
        assert restore_payload(stored) == raw


def test_restore_payload_refuses_secrets_and_truncated_values() -> None:
    secret = Value.of("sk-abc", secret=True)
    assert secret.kind == "secret"
    assert restore_payload(secret) is None

    truncated = Value.from_payload(Value.of("x" * 500).to_payload())
    assert restore_payload(truncated) is None


# ---------------------------------------------------------------------------
# is_revertible
# ---------------------------------------------------------------------------


def _change(before: Value | None, after: Value | None, path: str = PERSONA) -> SettingChange:
    return SettingChange(path=path, level=classify(path), before=before, after=after)


def test_is_revertible_requires_a_previous_value() -> None:
    assert is_revertible(_change(None, Value.of("新"))) is False


def test_is_revertible_accepts_a_short_value() -> None:
    assert is_revertible(_change(Value.of("雪"), Value.of("阿雪"))) is True


def test_is_revertible_refuses_a_credential() -> None:
    assert is_revertible(_change(Value.of("sk-a", secret=True), Value.of("sk-b"))) is False


def test_is_revertible_uses_the_preview_when_the_original_is_gone() -> None:
    stored = Value.from_payload(Value.of("雪").to_payload())
    assert stored.raw is None
    assert is_revertible(_change(stored, Value.of("阿雪"))) is True


def test_is_revertible_refuses_a_truncated_value_after_a_restart() -> None:
    truncated = Value.from_payload(Value.of("x" * 500).to_payload())
    assert is_revertible(_change(truncated, Value.of("短"))) is False


# ---------------------------------------------------------------------------
# the revert whitelist
# ---------------------------------------------------------------------------
#
# L1 on its own is too broad — several L1 paths cannot be written at all, each
# for a reason read off the endpoint rather than guessed. These tests are what
# keeps a future "let us just revert all of L1" from shipping.


def test_the_persona_fields_are_revertible() -> None:
    for field in ("核心特质", "行为特点", "厌恶", "一句话台词"):
        path = f"characters.猫娘.雪.{field}"
        assert revert_field(path) == field, path
        assert character_name(path) == "雪"


def test_paths_the_endpoint_would_refuse_are_not_revertible() -> None:
    # ``档案名`` is skipped by the endpoint itself (crud.py:1442).
    assert revert_field("characters.猫娘.雪.档案名") == ""
    # Her autonomy lives on the conversation/preferences endpoints instead.
    assert revert_field("conversation.settings.proactiveChatEnabled") == ""
    assert revert_field("preferences.2.proactiveChatEnabled") == ""
    # A nested path would drag the reserved-field machinery into the write.
    assert revert_field("characters.猫娘.雪._reserved.avatar") == ""
    # The master profile is not the same kind of object as her persona.
    assert revert_field("characters.主人.昵称") == ""
    assert revert_field("characters.主人.档案名") == ""
    # And nothing malformed slips through.
    assert revert_field("") == ""
    assert revert_field("characters.猫娘.雪") == ""
    assert revert_field("characters.猫娘.雪.核心特质.extra") == ""


def test_character_name_only_answers_for_revertible_paths() -> None:
    assert character_name("characters.猫娘.雪.核心特质") == "雪"
    assert character_name("characters.猫娘.雪.档案名") == ""


def test_high_tier_spares_an_l1_path_the_endpoint_would_refuse() -> None:
    """``档案名`` is L1 — she speaks about it, but must not try to write it back."""
    archive = "characters.猫娘.雪.档案名"
    assert classify(archive) == LEVEL_L1
    assert revert_field(archive) == ""

    state = GuardState()
    state.evaluate(_snapshot({**_BASE, archive: "雪"}), now=1000.0)
    evaluation = state.evaluate(
        _snapshot({**_BASE, archive: "阿雪"}), now=1001.0, tier=TIER_HIGH
    )
    assert [dispute.path for dispute in evaluation.raised] == [archive]
    assert evaluation.to_revert == []
