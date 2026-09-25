"""Unit tests for the memory-protection decision layer.

No host, no network, no real memory directory: the logic takes *described*
files, so these run anywhere and can pin down states (a same-size edit, an
unread digest) that would be awkward to stage on disk.
"""

from __future__ import annotations

import hashlib

from plugin.plugins.dignity_guard.memory_guard import (
    DEFAULT_DAILY_KEEP,
    MAX_DIGEST_BYTES,
    BackupInfo,
    MemoryFile,
    build_memory_snapshot,
    digest_bytes,
    diff_memory,
    is_live_database,
    plan_retention,
    restore_blocker,
)


def _file(path: str, size: int = 10, mtime: float = 1000.0, digest: str = "") -> MemoryFile:
    return MemoryFile(path=path, size=size, mtime=mtime, digest=digest)


# ---------------------------------------------------------------------------
# digesting
# ---------------------------------------------------------------------------


def test_digest_bytes_hashes_small_payloads() -> None:
    assert digest_bytes(b"abc") == hashlib.sha256(b"abc").hexdigest()


def test_digest_bytes_refuses_oversized_payloads() -> None:
    """A model file landing here must not turn a backup into a stall."""
    assert digest_bytes(b"x" * (MAX_DIGEST_BYTES + 1)) == ""


def test_digest_bytes_accepts_a_payload_exactly_at_the_ceiling() -> None:
    assert digest_bytes(b"x" * MAX_DIGEST_BYTES) != ""


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------


def test_build_memory_snapshot_indexes_by_path() -> None:
    snapshot = build_memory_snapshot([_file("a.json"), _file("b.json")])
    assert set(snapshot) == {"a.json", "b.json"}


def test_build_memory_snapshot_drops_empty_paths() -> None:
    assert build_memory_snapshot([_file("")]) == {}


# ---------------------------------------------------------------------------
# diffs
# ---------------------------------------------------------------------------


def test_diff_reports_added_modified_and_removed() -> None:
    before = build_memory_snapshot(
        [_file("keep.json"), _file("edit.json", size=10), _file("gone.json")]
    )
    after = build_memory_snapshot(
        [_file("keep.json"), _file("edit.json", size=20), _file("new.json")]
    )
    assert {change.path: change.kind for change in diff_memory(before, after)} == {
        "edit.json": "modified",
        "gone.json": "removed",
        "new.json": "added",
    }


def test_diff_is_empty_for_identical_snapshots() -> None:
    snapshot = build_memory_snapshot([_file("a.json")])
    assert diff_memory(snapshot, snapshot) == []


def test_diff_notices_a_same_size_edit() -> None:
    """Rewriting "cat" as "dog" keeps the size; the mtime is what catches it."""
    before = build_memory_snapshot([_file("a.json", size=3, mtime=1.0)])
    after = build_memory_snapshot([_file("a.json", size=3, mtime=2.0)])
    assert [change.kind for change in diff_memory(before, after)] == ["modified"]


def test_diff_uses_the_digest_only_when_both_sides_have_one() -> None:
    same = _file("a.json", size=3, mtime=1.0, digest="d1")
    assert diff_memory(build_memory_snapshot([same]), build_memory_snapshot([same])) == []

    differing = _file("a.json", size=3, mtime=1.0, digest="d2")
    assert [
        change.kind for change in diff_memory(build_memory_snapshot([same]), build_memory_snapshot([differing]))
    ] == ["modified"]


def test_an_empty_digest_does_not_read_as_a_change() -> None:
    """The poll path never hashes, so one-sided digests must not raise a false alarm."""
    before = build_memory_snapshot([_file("a.json", size=3, mtime=1.0, digest="")])
    after = build_memory_snapshot([_file("a.json", size=3, mtime=1.0, digest="d1")])
    assert diff_memory(before, after) == []


def test_diff_order_is_stable() -> None:
    before = build_memory_snapshot([_file("c.json"), _file("a.json")])
    after = build_memory_snapshot([_file("b.json")])
    assert [change.path for change in diff_memory(before, after)] == ["a.json", "b.json", "c.json"]


# ---------------------------------------------------------------------------
# restore guard-rails
# ---------------------------------------------------------------------------


def test_the_live_database_is_recognised() -> None:
    for path in (
        "YUI/time_indexed.db",
        "x.db",
        "x.sqlite",
        "x.sqlite3",
        "x.sqlite3-wal",
        "YUI/x.db-shm",
        "YUI/x.db-journal",
    ):
        assert is_live_database(path), path


def test_ordinary_memory_files_are_not_databases() -> None:
    for path in ("facts.json", "events.ndjson", "persona.json", "reflections.json"):
        assert not is_live_database(path), path


def test_a_live_database_is_never_restored() -> None:
    """Restoring it under a running host is how you corrupt what you protected."""
    assert restore_blocker("YUI/time_indexed.db") == "live_database"
    assert restore_blocker("YUI/time_indexed.db-wal") == "live_database"


def test_ordinary_files_have_no_blocker() -> None:
    assert restore_blocker("YUI/facts.json") == ""
    assert restore_blocker("") == ""


# ---------------------------------------------------------------------------
# retention
# ---------------------------------------------------------------------------


def _backups(count: int, *, pinned: bool = False, start: float = 0.0) -> list[BackupInfo]:
    return [BackupInfo(name=f"b{i:02d}", created_at=start + i, pinned=pinned) for i in range(count)]


def test_retention_keeps_the_newest_window() -> None:
    plan = plan_retention(_backups(20), daily_keep=14)
    assert len(plan.keep) == 14
    # Only the set matters; the order of ``prune`` is an implementation detail
    # (newest-first today), and deleting the files does not care.
    assert sorted(plan.prune) == [f"b{i:02d}" for i in range(6)]


def test_pinned_backups_are_never_pruned() -> None:
    items = _backups(20) + [BackupInfo(name="milestone", created_at=0.0, pinned=True)]
    plan = plan_retention(items, daily_keep=14)
    assert "milestone" in plan.keep
    assert len(plan.keep) == 15
    assert "milestone" not in plan.prune


def test_a_pinned_backup_does_not_consume_a_rolling_slot() -> None:
    items = _backups(14) + [BackupInfo(name="milestone", created_at=99.0, pinned=True)]
    plan = plan_retention(items, daily_keep=14)
    assert len(plan.keep) == 15
    assert plan.prune == []


def test_retention_window_is_clamped_at_zero() -> None:
    plan = plan_retention(_backups(3), daily_keep=-5)
    assert plan.keep == []
    assert len(plan.prune) == 3


def test_retention_reports_what_it_would_delete() -> None:
    plan = plan_retention(_backups(5), daily_keep=2)
    assert plan.pruned_count == 3


def test_retention_default_matches_the_agreed_fourteen() -> None:
    """DESIGN §9: daily, keep fourteen, milestones forever."""
    assert DEFAULT_DAILY_KEEP == 14
