"""Filesystem side of memory protection.

The decisions live in :mod:`.memory_guard`; this module only touches disk, and
it is deliberately thin so the interesting behaviour stays testable without a
host.

What lives here:

* :func:`resolve_memory_root` — find her memory directory, or admit we cannot.
* :func:`scan_memory` — describe every file cheaply (size + mtime, no hashing).
* :func:`create_backup` / :func:`list_backups` / :func:`prune_backups`.
* :func:`restore_file` — put one file back, refusing the live database.

The database rule
-----------------
``time_indexed.db`` cannot be handled like the JSON beside it. Two things are
true of it and neither is true of the rest:

* a plain file copy of a database being written to can capture a torn state,
  where the file body and its write-ahead log disagree. :func:`_copy_database`
  therefore uses SQLite's own online-backup API, which is the supported way to
  snapshot a live database, and only falls back to copying if that fails;
* the ``-wal`` / ``-shm`` sidecars are **skipped**, not copied. Once the backup
  API has produced a self-contained database, dragging the sidecars along would
  let them overwrite the very state we just captured.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .memory_guard import (
    LIVE_DATABASE_SUFFIXES,
    BackupInfo,
    MemoryFile,
    MemorySnapshot,
    RetentionPlan,
    build_memory_snapshot,
    digest_bytes,
    is_live_database,
    plan_retention,
    restore_blocker,
)

__all__ = [
    "BACKUP_DIR_NAME",
    "MEMORY_DIR_NAME",
    "PINNED_MARKER",
    "create_backup",
    "list_backups",
    "prune_backups",
    "resolve_memory_root",
    "restore_file",
    "scan_memory",
    "set_pinned",
]

#: Directory name inside the runtime data root that holds her memory.
MEMORY_DIR_NAME = "memory"

#: Where backups go, relative to the plugin's own storage directory.
BACKUP_DIR_NAME = "memory_backups"

#: Marker file inside a backup directory meaning "never prune this one".
PINNED_MARKER = ".pinned"

#: Data-root environment variables the host uses to tell child processes where
#: its data lives. Mirrors ``plugin/sdk/runtime/base_runtime.py:69-80``.
_ROOT_ENV_VARS: tuple[str, ...] = ("NEKO_STORAGE_SELECTED_ROOT", "ANCHOR_ROOT", "NEKO_ANCHOR_ROOT")

#: SQLite's write-ahead sidecars. Handled by the database, never copied alone.
_SIDECAR_SUFFIXES: tuple[str, ...] = ("-wal", "-shm", "-journal")


def resolve_memory_root(
    *,
    explicit: str = "",
    storage_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """Return the memory directory that actually exists, or ``None``.

    Candidates are tried in order of specificity: an explicit path from the
    plugin's configuration, the data root the host handed down through the
    environment, then the platform default (``%LOCALAPPDATA%/N.E.K.O`` on
    Windows, ``~/.local/share/N.E.K.O`` elsewhere).

    Returning ``None`` matters more than it looks: a wrong guess would have us
    silently backing up an empty directory and reporting success, which is
    worse than admitting we could not find her memory.
    """
    environment = env if env is not None else os.environ
    candidates: list[Path] = []

    if explicit.strip():
        candidates.append(Path(explicit.strip()))

    for name in _ROOT_ENV_VARS:
        value = environment.get(name, "").strip()
        if value:
            candidates.append(Path(value) / MEMORY_DIR_NAME)
            # ``ANCHOR_ROOT`` has been seen pointing at the app folder itself,
            # one level above the data root.
            candidates.append(Path(value) / "data" / MEMORY_DIR_NAME)

    if storage_dir is not None:
        # The plugin's own directory sits under the same data root, so its
        # grandparent is the root the host uses.
        root = Path(storage_dir)
        for parent in (root, root.parent, root.parent.parent):
            candidates.append(parent / MEMORY_DIR_NAME)

    local_app_data = environment.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(Path(local_app_data) / "N.E.K.O" / MEMORY_DIR_NAME)
    candidates.append(Path.home() / ".local" / "share" / "N.E.K.O" / MEMORY_DIR_NAME)

    for candidate in candidates:
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def _is_sidecar(path: Path) -> bool:
    return path.name.lower().endswith(_SIDECAR_SUFFIXES)


def scan_memory(root: Path, *, with_digest: bool = False) -> MemorySnapshot:
    """Describe every file under ``root``.

    ``with_digest`` is off by default on purpose: this runs on the poll path,
    and hashing a megabyte every tick would cost real CPU on a modest machine
    for no benefit. Size and mtime are enough to notice a change; the digest is
    only worth computing when we are already reading the bytes for a backup.
    """
    entries: list[MemoryFile] = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            # A file that vanished mid-scan is not an error worth raising; the
            # next scan will simply not mention it.
            continue
        digest = ""
        if with_digest:
            try:
                digest = digest_bytes(path.read_bytes())
            except OSError:
                digest = ""
        entries.append(
            MemoryFile(
                path=path.relative_to(root).as_posix(),
                size=stat.st_size,
                mtime=stat.st_mtime,
                digest=digest,
            )
        )
    return build_memory_snapshot(entries)


def _copy_database(source: Path, target: Path) -> str:
    """Copy one live SQLite database. Returns ``""`` or a fallback note.

    A plain copy of a database under active write can be torn, so SQLite's
    online-backup API is tried first — reading through a read-only connection,
    which cannot disturb the host's own writer.
    """
    try:
        with sqlite3.connect(
            f"file:{source.as_posix()}?mode=ro", uri=True, timeout=3.0
        ) as origin, sqlite3.connect(str(target)) as copy:
            origin.backup(copy)
        return ""
    except Exception:  # noqa: BLE001 - any failure means "fall back and say so"
        shutil.copy2(source, target)
        return "plain_copy"


def create_backup(root: Path, backup_root: Path, *, stamp: str) -> dict[str, Any]:
    """Copy her memory into ``backup_root/<stamp>/memory``.

    Returns a small report rather than raising, because a backup that half
    succeeded is still worth keeping — the caller logs it and the panel shows it.
    """
    source = Path(root)
    destination = Path(backup_root) / stamp / MEMORY_DIR_NAME
    destination.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    fallbacks: list[str] = []

    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if _is_sidecar(path):
            # Carried inside the database copy, not beside it.
            skipped += 1
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_live_database(relative.as_posix()):
            note = _copy_database(path, target)
            if note:
                fallbacks.append(f"{relative.as_posix()}:{note}")
        else:
            shutil.copy2(path, target)
        copied += 1

    return {
        "stamp": stamp,
        "files": copied,
        "sidecars_skipped": skipped,
        "database_fallbacks": fallbacks,
        "path": str(destination),
    }


def list_backups(backup_root: Path) -> list[BackupInfo]:
    """List backup directories, newest last. Unreadable entries are ignored."""
    root = Path(backup_root)
    if not root.is_dir():
        return []
    found: list[BackupInfo] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            created = entry.stat().st_mtime
        except OSError:
            continue
        found.append(
            BackupInfo(
                name=entry.name,
                created_at=created,
                pinned=(entry / PINNED_MARKER).is_file(),
            )
        )
    return found


def set_pinned(backup_root: Path, name: str, *, pinned: bool = True) -> bool:
    """Mark a backup as a milestone (kept forever), or clear the mark."""
    marker = Path(backup_root) / name / PINNED_MARKER
    try:
        if pinned:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(time.time()), encoding="utf-8")
        elif marker.exists():
            marker.unlink()
        return True
    except OSError:
        return False


def prune_backups(backup_root: Path, *, daily_keep: int) -> list[str]:
    """Delete the backups outside the retention window. Returns what was deleted.

    A directory that cannot be removed is left alone and reported by omission
    rather than being retried in a loop.
    """
    plan: RetentionPlan = plan_retention(list_backups(backup_root), daily_keep=daily_keep)
    removed: list[str] = []
    for name in plan.prune:
        try:
            shutil.rmtree(Path(backup_root) / name)
            removed.append(name)
        except OSError:
            continue
    return removed


def restore_file(backup_root: Path, name: str, root: Path, relative: str) -> str:
    """Put one file back from a backup. Returns ``""`` or a reason code.

    Refuses the live database outright — see the module docstring. The caller
    turns the code into the user's language.
    """
    blocker = restore_blocker(relative)
    if blocker:
        return blocker
    source = Path(backup_root) / name / MEMORY_DIR_NAME / relative
    if not source.is_file():
        return "missing_in_backup"
    destination = Path(root) / relative
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    except OSError:
        return "write_failed"
    return ""
