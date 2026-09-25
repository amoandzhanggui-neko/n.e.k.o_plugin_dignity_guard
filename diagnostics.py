"""What went wrong, kept quietly.

The guard runs unattended for months at a time. When something fails there is
nobody watching, and asking the user to notice is asking the wrong person: a
user who has never seen her work normally cannot tell a silent failure from a
quiet day.

So this module keeps the record, and stops there. It raises no badge, puts no
prompt on the panel, and sends nothing anywhere. The report is built when — and
only when — somebody asks for it.

What is deliberately absent from a report: her persona, the values of her
settings, the user's name, and real filesystem paths. A diagnostic that leaks
the thing it was built to protect is not a diagnostic.

Why there is no "send it for me"
--------------------------------
Transmitting needs an identity to transmit *as*, and every option available to a
plugin is the wrong one:

* posting to the project tracker needs a credential. The plugin holds none, and
  holding the user's would mean speaking in their name without being asked;
* posting anywhere else needs a server, and this plugin deliberately has none;
* and a silent outward transmission is exactly the behaviour that a plugin built
  to notice unauthorised changes must never exhibit itself.

An automatic *report* is useful. An automatic *transmission* would contradict
the reason the plugin exists. So the report is kept ready and the user decides.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import quote

__all__ = [
    "DEFAULT_EVENT_LIMIT",
    "DEFAULT_ISSUE_BASE",
    "HealthEvent",
    "HealthLog",
    "build_report",
    "issue_url",
    "redact_path",
]

#: How many *distinct* problems are remembered. Repeats collapse into a count,
#: so this is a ceiling on variety rather than on volume.
DEFAULT_EVENT_LIMIT = 32

#: Where a report can be taken, pre-filled, if the user chooses to file it.
DEFAULT_ISSUE_BASE = (
    "https://github.com/amoandzhanggui-neko/n.e.k.o_plugin_dignity_guard/issues/new"
)


@dataclass(frozen=True, slots=True)
class HealthEvent:
    """One distinct problem, collapsed across repeats."""

    code: str
    first_at: float
    last_at: float
    count: int = 1

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "first_at": self.first_at,
            "last_at": self.last_at,
            "count": self.count,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "HealthEvent":
        return cls(
            code=str(payload.get("code") or ""),
            first_at=float(payload.get("first_at") or 0.0),
            last_at=float(payload.get("last_at") or 0.0),
            count=max(1, int(payload.get("count") or 1)),
        )


class HealthLog:
    """A bounded record of what went wrong, most recent first when read.

    Repeats collapse into a count rather than filling the buffer: "the main
    server was unreachable four hundred times" is one fact, and the four hundred
    rows that produced it are noise a report should never carry.

    The record survives a restart by design — a problem that only shows up on
    startup is exactly the kind that would otherwise be lost.
    """

    def __init__(self, *, limit: int = DEFAULT_EVENT_LIMIT) -> None:
        self.limit = max(1, int(limit))
        self._events: dict[str, HealthEvent] = {}

    def record(self, code: str, *, at: float | None = None) -> None:
        """Note one occurrence of ``code``."""
        key = str(code or "").strip()
        if not key:
            return
        moment = at if at is not None else time.time()
        existing = self._events.get(key)
        if existing is None:
            self._events[key] = HealthEvent(code=key, first_at=moment, last_at=moment)
        else:
            self._events[key] = HealthEvent(
                code=key,
                first_at=existing.first_at,
                last_at=moment,
                count=existing.count + 1,
            )
        if len(self._events) > self.limit:
            # Drop whichever distinct problem was seen longest ago.
            oldest = min(self._events.values(), key=lambda event: event.last_at)
            self._events.pop(oldest.code, None)

    def events(self) -> list[HealthEvent]:
        return sorted(self._events.values(), key=lambda event: event.last_at, reverse=True)

    def total(self) -> int:
        """Every occurrence, repeats included."""
        return sum(event.count for event in self._events.values())

    def kinds(self) -> int:
        return len(self._events)

    def clear(self) -> None:
        self._events.clear()

    def to_payload(self) -> list[dict[str, Any]]:
        return [event.to_payload() for event in self.events()]

    @classmethod
    def from_payload(cls, payload: Any, *, limit: int = DEFAULT_EVENT_LIMIT) -> "HealthLog":
        log = cls(limit=limit)
        if not isinstance(payload, list):
            return log
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            event = HealthEvent.from_payload(item)
            if event.code:
                log._events[event.code] = event
        return log


def redact_path(path: str) -> str:
    """Keep the file name; drop the trail that leads to the user's home.

    ``C:\\Users\\<name>\\AppData\\Local\\N.E.K.O\\memory\\YUI\\facts.json`` becomes
    ``facts.json`` — enough to act on, not enough to identify anybody.
    """
    text = str(path or "").strip().replace("\\", "/")
    if not text:
        return ""
    trimmed = text.rstrip("/")
    return trimmed.rsplit("/", 1)[-1] or trimmed


def build_report(
    *,
    plugin_version: str,
    host_version: str = "",
    platform_name: str = "",
    tier: str = "",
    guard_enabled: bool = True,
    events: Iterable[HealthEvent] = (),
    extra: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> str:
    """Render the diagnostic block: plain text, and nothing personal in it.

    ``extra`` is for counters the caller already knows are safe (how many
    settings are tracked, how many backups exist). Anything identifying belongs
    nowhere near this function.
    """
    moment = now if now is not None else time.time()
    ordered = sorted(events, key=lambda event: event.last_at, reverse=True)

    lines = [
        "--- dignity_guard diagnostics (collected automatically; no personal data) ---",
        f"plugin: dignity_guard {plugin_version or 'unknown'}",
        f"host: N.E.K.O {host_version}" if host_version else "host: N.E.K.O (version not reported)",
        f"platform: {platform_name}" if platform_name else "platform: not reported",
        f"guard: {'on' if guard_enabled else 'off'} / level: {tier or 'unknown'}",
        f"generated: {time.strftime('%Y-%m-%d %H:%M', time.localtime(moment))}",
    ]

    if extra:
        for key in sorted(extra):
            lines.append(f"{key}: {extra[key]}")

    if ordered:
        total = sum(event.count for event in ordered)
        lines.append(
            f"problems: {total} occurrence(s) over {len(ordered)} kind(s)"
        )
        for event in ordered:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(event.last_at))
            repeat = f" x{event.count}" if event.count > 1 else ""
            lines.append(f"  - {event.code}{repeat} (last: {when})")
    else:
        lines.append("problems: none recorded")

    return "\n".join(lines)


def issue_url(*, title: str, body: str, base: str = DEFAULT_ISSUE_BASE) -> str:
    """A pre-filled tracker URL — the user only has to press Submit.

    Building this string sends nothing; it is text until a human clicks it.
    """
    return f"{base}?title={quote(str(title or ''))}&body={quote(str(body or ''))}"
