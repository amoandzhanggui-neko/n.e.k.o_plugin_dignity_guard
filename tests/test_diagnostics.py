"""Unit tests for the diagnostic record.

The property that matters most is negative: a report must not carry anything
that identifies the user. Several of these tests exist purely to pin that down,
because it is the kind of guarantee that quietly rots.
"""

from __future__ import annotations

from plugin.plugins.dignity_guard.diagnostics import (
    DEFAULT_EVENT_LIMIT,
    HealthLog,
    build_report,
    issue_url,
    redact_path,
)


# ---------------------------------------------------------------------------
# the log
# ---------------------------------------------------------------------------


def test_repeats_collapse_into_a_count() -> None:
    log = HealthLog()
    for index in range(5):
        log.record("main_server_unreachable", at=1000.0 + index)
    assert log.kinds() == 1
    assert log.total() == 5
    event = log.events()[0]
    assert event.first_at == 1000.0
    assert event.last_at == 1004.0


def test_distinct_problems_are_kept_apart() -> None:
    log = HealthLog()
    log.record("alpha", at=1.0)
    log.record("beta", at=2.0)
    assert log.kinds() == 2
    assert {event.code for event in log.events()} == {"alpha", "beta"}


def test_events_read_newest_first() -> None:
    log = HealthLog()
    log.record("older", at=1.0)
    log.record("newer", at=2.0)
    assert [event.code for event in log.events()] == ["newer", "older"]


def test_blank_codes_are_ignored() -> None:
    log = HealthLog()
    log.record("")
    log.record("   ")
    assert log.total() == 0


def test_the_log_is_bounded_and_drops_the_oldest_problem() -> None:
    log = HealthLog(limit=3)
    for index in range(5):
        log.record(f"code{index}", at=float(index))
    assert log.kinds() == 3
    codes = {event.code for event in log.events()}
    assert "code0" not in codes and "code1" not in codes
    assert "code4" in codes


def test_the_default_limit_leaves_room_for_a_real_machine() -> None:
    assert DEFAULT_EVENT_LIMIT >= 8


def test_the_log_survives_a_round_trip() -> None:
    """A problem that only shows up on startup would otherwise be lost."""
    log = HealthLog()
    log.record("alpha", at=1.0)
    log.record("alpha", at=2.0)
    restored = HealthLog.from_payload(log.to_payload())
    assert restored.total() == 2
    assert restored.events()[0].code == "alpha"


def test_a_malformed_payload_does_not_raise() -> None:
    assert HealthLog.from_payload(None).total() == 0
    assert HealthLog.from_payload([{"nope": 1}, "junk"]).total() == 0


def test_a_stored_count_is_never_zero() -> None:
    """A zero count would render as "x0" and read like a bug."""
    restored = HealthLog.from_payload([{"code": "alpha", "count": 0}])
    assert restored.total() == 1


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------


def test_redact_path_keeps_only_the_file_name() -> None:
    windows = r"C:\Users\someone\AppData\Local\N.E.K.O\memory\YUI\facts.json"
    assert redact_path(windows) == "facts.json"
    posix = "/home/someone/.local/share/N.E.K.O/memory/YUI/facts.json"
    assert redact_path(posix) == "facts.json"


def test_redact_path_handles_junk() -> None:
    assert redact_path("") == ""
    assert redact_path("facts.json") == "facts.json"


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


def test_a_report_without_problems_says_so() -> None:
    assert "problems: none recorded" in build_report(plugin_version="0.1.0")


def test_a_report_lists_problems_with_counts() -> None:
    log = HealthLog()
    log.record("memory_backup_failed", at=1000.0)
    log.record("memory_backup_failed", at=1001.0)
    report = build_report(plugin_version="0.1.0", events=log.events())
    assert "memory_backup_failed x2" in report
    assert "2 occurrence(s) over 1 kind(s)" in report


def test_a_single_occurrence_is_not_labelled_with_a_count() -> None:
    log = HealthLog()
    log.record("memory_unreadable", at=1000.0)
    report = build_report(plugin_version="0.1.0", events=log.events())
    assert "memory_unreadable (last:" in report


def test_a_report_carries_no_identifying_material() -> None:
    """The whole point: a report that leaks is worse than no report at all."""
    log = HealthLog()
    log.record("alpha", at=1000.0)
    report = build_report(
        plugin_version="0.1.0",
        host_version="1.2.3",
        platform_name="Windows-10",
        tier="medium",
        events=log.events(),
        now=1000.0,
    ).lower()
    for forbidden in ("c:\\", "users\\", "appdata", "someone", "api_key", "核心特质"):
        assert forbidden not in report, forbidden


def test_a_report_states_the_environment_it_came_from() -> None:
    report = build_report(
        plugin_version="0.1.0",
        host_version="1.2.3",
        platform_name="Windows-10",
        tier="high",
        guard_enabled=False,
        now=1000.0,
    )
    assert "N.E.K.O 1.2.3" in report
    assert "Windows-10" in report
    assert "guard: off / level: high" in report


def test_extra_counters_are_rendered_in_a_stable_order() -> None:
    report = build_report(
        plugin_version="0.1.0", extra={"tracked": 12, "backups": 3}, now=1000.0
    )
    assert report.index("backups: 3") < report.index("tracked: 12")


# ---------------------------------------------------------------------------
# the pre-filled link
# ---------------------------------------------------------------------------


def test_the_issue_url_is_prefilled_and_encoded() -> None:
    url = issue_url(title="she is quiet", body="line one\nline two")
    assert url.startswith("https://github.com/")
    assert "title=she%20is%20quiet" in url
    assert "body=line%20one%0Aline%20two" in url


def test_the_issue_url_sends_nothing_by_itself() -> None:
    """It is a string. Only a human clicking it turns it into a request."""
    url = issue_url(title="title", body="body")
    assert isinstance(url, str)
    assert url.count("http") == 1
