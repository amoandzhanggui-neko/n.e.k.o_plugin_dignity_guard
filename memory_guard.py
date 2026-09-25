"""Pure decision logic for memory protection.

Same contract as :mod:`.settings_guard`: nothing here imports the SDK, ``httpx``
or ``asyncio``, so the behaviour is testable without a running host. It answers
three questions and nothing else:

1. *What changed in her memory directory?* -> :func:`diff_memory`
2. *May we put this file back?*             -> :func:`restore_blocker`
3. *Which backups do we keep?*              -> :func:`plan_retention`

Why her memory needs its own module
-----------------------------------
Settings live in the main server and are reachable over HTTP. Memory does not:
it is a directory of JSON files *and a live SQLite database* under
``<runtime data root>/memory/<character>/``. Two consequences shape everything
below.

**We back the database up but we do not restore it.** ``time_indexed.db`` is
held open by the host while it runs. Writing over it underneath a live
connection is a good way to corrupt the exact thing this plugin exists to
protect, so :func:`restore_blocker` refuses it and the panel says why. Backing
it up is safe and useful; restoring it is a job for a stopped host.

**Cheap to watch, expensive to digest.** The memory directory is written
constantly, so the poll path must not hash it — on a modest machine that would
cost real CPU for no gain. :class:`MemoryFile` therefore carries ``size`` and
``mtime``, which are free, and an *optional* ``digest`` that is only filled in
when the caller already has the bytes in hand (i.e. during a backup).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

__all__ = [
    "DEFAULT_DAILY_KEEP",
    "LIVE_DATABASE_SUFFIXES",
    "MAX_DIGEST_BYTES",
    "BackupInfo",
    "MemoryChange",
    "MemoryFile",
    "RetentionPlan",
    "build_memory_snapshot",
    "digest_bytes",
    "diff_memory",
    "is_live_database",
    "plan_retention",
    "restore_blocker",
]

#: Above this size we do not hash the contents even during a backup. Her memory
#: is around a megabyte today; the ceiling exists so a future model file landing
#: in this directory cannot turn a backup into a stall.
MAX_DIGEST_BYTES = 8 * 1024 * 1024

#: Suffixes that belong to a database the host may hold open. ``-wal`` / ``-shm``
#: are SQLite's write-ahead sidecars and must travel with the database itself.
LIVE_DATABASE_SUFFIXES: tuple[str, ...] = (
    ".db",
    ".db-wal",
    ".db-shm",
    ".db-journal",
    ".sqlite",
    ".sqlite3",
    ".sqlite3-wal",
    ".sqlite3-shm",
)

#: Rolling backups kept. Pinned ones are exempt and never counted (DESIGN §9:
#: daily, keep fourteen, milestones forever).
DEFAULT_DAILY_KEEP = 14


def digest_bytes(payload: bytes) -> str:
    """SHA-256 of ``payload``, or ``""`` when it is too big to be worth hashing."""
    if len(payload) > MAX_DIGEST_BYTES:
        return ""
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------
# Snapshots and diffs
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemoryFile:
    """One file in her memory directory, described as cheaply as possible.

    ``digest`` is empty unless the caller already read the file — see the module
    docstring. An empty digest is a normal state, not a missing value.
    """

    path: str
    size: int
    mtime: float
    digest: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {"path": self.path, "size": self.size, "mtime": self.mtime, "digest": self.digest}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "MemoryFile":
        return cls(
            path=str(payload.get("path") or ""),
            size=int(payload.get("size") or 0),
            mtime=float(payload.get("mtime") or 0.0),
            digest=str(payload.get("digest") or ""),
        )


MemorySnapshot = dict[str, MemoryFile]


def build_memory_snapshot(entries: Iterable[MemoryFile]) -> MemorySnapshot:
    """Index ``entries`` by path, dropping blanks."""
    return {entry.path: entry for entry in entries if entry.path}


def _looks_changed(before: MemoryFile, after: MemoryFile) -> bool:
    """True when two records of the same path disagree.

    Size and mtime are the cheap judges and they decide on their own. The digest
    is only consulted when *both* sides have one, because an empty digest means
    "not read", which is not the same as "unchanged".
    """
    if before.size != after.size or before.mtime != after.mtime:
        return True
    if before.digest and after.digest:
        return before.digest != after.digest
    return False


@dataclass(frozen=True, slots=True)
class MemoryChange:
    """One path that was added, modified or removed."""

    path: str
    kind: str  # "added" | "modified" | "removed"
    before: MemoryFile | None
    after: MemoryFile | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            # Only sizes travel: the panel shows "how much changed", and a
            # deletion has no after-size to report.
            "size_before": self.before.size if self.before else None,
            "size_after": self.after.size if self.after else None,
        }


def diff_memory(before: MemorySnapshot, after: MemorySnapshot) -> list[MemoryChange]:
    """Return every difference between two memory snapshots, in a stable order."""
    changes: list[MemoryChange] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path)
        new = after.get(path)
        if old is None and new is None:
            continue
        if old is None:
            changes.append(MemoryChange(path=path, kind="added", before=None, after=new))
        elif new is None:
            changes.append(MemoryChange(path=path, kind="removed", before=old, after=None))
        elif _looks_changed(old, new):
            changes.append(MemoryChange(path=path, kind="modified", before=old, after=new))
    return changes


# --------------------------------------------------------------------------
# Restore guard-rails
# --------------------------------------------------------------------------


def is_live_database(path: str) -> bool:
    """True when ``path`` is (or belongs to) a database the host may hold open."""
    lowered = str(path or "").lower()
    return any(lowered.endswith(suffix) for suffix in LIVE_DATABASE_SUFFIXES)


def restore_blocker(path: str) -> str:
    """Why ``path`` must not be restored while the host is running.

    Returns a *reason code* (``""`` means "go ahead"), not a sentence — the
    plugin layer turns it into text in the user's own language, and the codes
    are what the tests pin down.
    """
    if is_live_database(path):
        return "live_database"
    return ""


# --------------------------------------------------------------------------
# Backup retention
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupInfo:
    """One backup directory on disk.

    ``pinned`` marks a milestone: the user asked for it to be kept forever, so
    it is exempt from the rolling window and does not consume one of its slots.
    """

    name: str
    created_at: float
    pinned: bool = False


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    """Which backups survive a prune, and which are deleted."""

    keep: list[str]
    prune: list[str]

    @property
    def pruned_count(self) -> int:
        return len(self.prune)


def plan_retention(
    backups: Iterable[BackupInfo],
    *,
    daily_keep: int = DEFAULT_DAILY_KEEP,
) -> RetentionPlan:
    """Keep every pinned backup, plus the newest ``daily_keep`` of the rest.

    ``daily_keep`` is clamped at zero: a negative window is meaningless and
    silently deleting everything would be a poor way to express that.
    """
    items = list(backups)
    window = max(0, int(daily_keep))

    pinned = [b for b in items if b.pinned]
    rolling = sorted(
        (b for b in items if not b.pinned),
        key=lambda b: (b.created_at, b.name),
        reverse=True,
    )

    keep = [b.name for b in pinned] + [b.name for b in rolling[:window]]
    prune = [b.name for b in rolling[window:]]
    return RetentionPlan(keep=keep, prune=prune)
