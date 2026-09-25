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
import time
from pathlib import Path
from typing import Any, Mapping

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
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
from .feedback import DEFAULT_FEEDBACK_ENDPOINT, FeedbackUndeliverable, deliver
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
)
from .memory_guard import (
    MemoryChange,
    MemorySnapshot,
    diff_memory,
    restore_blocker,
)
from .settings_guard import (
    DEFAULT_LEVEL,
    DEFAULT_TIER,
    GUARD_SWITCH_PATH,
    REVERTIBLE_CATFIELDS,
    TIERS,
    Evaluation,
    GuardState,
    SettingChange,
    character_name,
    classify,
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

#: Must match ``version`` in ``plugin.toml``. ``tests/test_smoke.py`` asserts the
#: two agree, because a diagnostic that reports the wrong version costs the
#: reader time in exactly the situation where it is least affordable.
PLUGIN_VERSION = "0.1.0"

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

#: The largest message we advertise. Derived from the two above so the panel and
#: the check can never drift apart.
FEEDBACK_LIMIT_BYTES = FEEDBACK_BODY_LIMIT_BYTES - FEEDBACK_ENVELOPE_BYTES

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

#: One note per this many seconds, per running plugin.
#:
#: The relay and the mailbox behind it are shared by every installed copy, so a
#: stuck button or an over-eager script must not be able to drain the quota. A
#: minute is far above what a human would trip and far below what a retry loop
#: would.
FEEDBACK_COOLDOWN_SECONDS = 60.0

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

__all__ = ["DignityGuardPlugin", "POLL_SECONDS"]


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
        #: Outcome of the most recent revert attempt, per path. ``""`` means the
        #: value went back; anything else is a reason code the panel translates.
        self._revert_outcomes: dict[str, str] = {}
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

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
        #: Approximate start time, so a report can say how long it has been up.
        self._started_at = time.time()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        await self._reload_config()
        await self._load_persisted_state()
        self._client = MainServerClient(self._base_url, timeout=DEFAULT_TIMEOUT_SECONDS)
        self._watcher = SettingsWatcher(
            self._client,
            self._state,
            full_rescan_seconds=self._full_rescan_seconds,
        )
        if not self.store.enabled:
            self._health.record("store_disabled")
            self.logger.warning(
                "dignity_guard: plugin store is disabled; the dispute record "
                "will not survive a restart. Set [plugin.store].enabled = true."
            )
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
        await self._reload_config()
        if self._watcher is not None:
            self._watcher.full_rescan_seconds = self._full_rescan_seconds
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
        self._tier_default = normalize_tier(section.get("guard_level", DEFAULT_TIER))

        configured_endpoint = section.get("feedback_endpoint")
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
        self._memory_backup_keep = (
            _non_negative_int(keep)
            if isinstance(keep, (int, float))
            else DEFAULT_MEMORY_BACKUP_KEEP
        )
        # The root may have moved under a different storage policy, so drop the
        # cached answer and let the next check resolve it again.
        self._memory_root = None

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    async def _load_persisted_state(self) -> None:
        payload = await self._store_read(STORE_KEY)
        if isinstance(payload, dict):
            self._state = GuardState.from_payload(payload)
            self._enabled = bool(payload.get("guard_enabled", self._enabled_default))
            self._disabled_at = _optional_float(payload.get("guard_disabled_at"))
            self._off_since = _optional_float(payload.get("guard_off_since"))
            self._disable_count = _non_negative_int(payload.get("guard_disable_count"))
            self._last_off_seconds = _optional_float(payload.get("guard_last_off_seconds"))
            self._tier = normalize_tier(payload.get("guard_tier", self._tier_default))
            self._health = HealthLog.from_payload(payload.get("health"))
        else:
            self._enabled = self._enabled_default
            self._tier = self._tier_default
        self._user_locale = None

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
        # The chosen tier lives here rather than in the config file so the panel
        # can change it without touching configuration.
        payload["guard_tier"] = self._tier
        # The health record travels with the state, so a problem that only shows
        # up on startup is still there to be described later.
        payload["health"] = self._health.to_payload()
        return await self._store_write(STORE_KEY, payload)

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
        if not self._enabled:
            return Ok({"status": "skipped", "reason": "guard_disabled"})
        result = await self._run_poll(force=False)
        # Her memory rides along on the same timer instead of getting its own:
        # the SDK takes a literal cadence, and this one throttles itself anyway.
        await self._maybe_check_memory()
        return result

    def _poll_lock(self) -> asyncio.Lock:
        """Return a lock bound to the loop that is calling right now.

        Timer callbacks and entry handlers may run on different loops, and an
        ``asyncio.Lock`` may not be shared across loops. Re-creating it per loop
        keeps that from raising while still serialising the common single-loop
        case (which is what prevents a duplicated spoken message).
        """
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def _run_poll(self, *, force: bool):
        watcher, client = self._watcher, self._client
        if watcher is None or client is None:
            return Err(SdkError("plugin is not started yet", code="not_ready"))

        lock = self._poll_lock()
        if lock.locked():
            return Ok({"status": "busy"})

        async with lock:
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

            if evaluation.first_seen:
                await self._refresh_user_locale(client)
                await self._persist_state()
                return Ok(
                    {
                        "status": "baseline",
                        "tracked_paths": len(self._state.snapshot),
                    }
                )

            if evaluation.has_changes:
                await self._persist_state()
                self._speak(evaluation)

            # She has spoken; now the high tier may put something back. Kept
            # separate from ``_speak`` on purpose: a revert we cannot carry out
            # must never be a reason she falls silent.
            if evaluation.to_revert:
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
            "revision": self._watcher.last_probe.revision if self._watcher else None,
        }

    # ------------------------------------------------------------------
    # speaking
    # ------------------------------------------------------------------

    def _text(self, key: str, *, default: str = "", **params: Any) -> str:
        return self.i18n.t(key, locale=self._user_locale, default=default, **params)

    def _speak(self, evaluation: Evaluation) -> None:
        """Hand the situation to the model and let her answer in her own voice.

        We only supply the situation; the tone is hers. The host expands
        ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` per session, so we never guess a
        name ourselves.
        """
        lines = [self._text("speech.intro", default=DEFAULT_SPEECH_INTRO)]
        for dispute in evaluation.raised:
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

        receipt = self.push_message(
            parts=[{"type": "text", "text": "\n".join(lines)}],
            ai_behavior="respond",
            # Collapse a burst of cues into the newest one; the panel keeps the
            # full record, so nothing is lost when an earlier cue is replaced.
            coalesce_key="dignity_guard.settings_changed",
            metadata={"description": "dignity_guard.settings_changed"},
        )
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
        """
        self._revert_outcomes = {}
        for change in evaluation.to_revert:
            reason = await self._revert_change(change)
            self._revert_outcomes[change.path] = reason
            if reason:
                self.logger.info(
                    "dignity_guard: left {} as it was ({})", change.path, reason
                )

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
            snapshot = scan_memory(root)
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
            self._memory_snapshot = snapshot
            await self._backup_memory(reason="baseline", now=moment)
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
            report = create_backup(root, backup_root, stamp=stamp)
        except OSError as exc:
            self._memory_error = "backup_failed"
            self._health.record("memory_backup_failed")
            self.logger.warning("dignity_guard: memory backup failed: {}", exc)
            return

        self._last_memory_backup_at = now
        pruned = prune_backups(backup_root, daily_keep=self._memory_backup_keep)
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

    def _memory_status(self) -> dict[str, Any]:
        root = self._memory_root
        try:
            backups = list_backups(self._memory_backup_root())
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

    def _build_diagnostics_report(self) -> str:
        """Assemble the report. ``diagnostics.py`` documents what is left out."""
        try:
            backups = len(list_backups(self._memory_backup_root()))
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
            # Empty means "no endpoint configured" — the panel then offers
            # copy-and-open rather than a Send button that would fail.
            "feedback_endpoint": self._feedback_endpoint,
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
            "her_words": HER_PROTECTION_STATEMENT,
            "memory": self._memory_status(),
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
            "last_error": self._watcher.last_error if self._watcher else "",
            "revision": self._watcher.last_probe.revision if self._watcher else None,
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

    @ui.action(
        id="accept_setting",
        label=tr("actions.acceptSetting.label", default="Accept"),
        icon="✅",
        tone="success",
        group="guard",
        order=20,
        refresh_context=True,
    )
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
            return Err(SdkError("path is required", code="invalid_argument"))
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
        await self._persist_state()
        return Ok(
            {
                "status": "accepted",
                "path": grant.path,
                "granted_at": grant.granted_at,
                "expires_at": grant.expires_at,
                "pending_total": len(self._state.pending()),
            }
        )

    @ui.action(
        id="keep_objecting",
        label=tr("actions.keepObjecting.label", default="Keep objecting"),
        icon="🙅",
        tone="warning",
        group="guard",
        order=30,
        refresh_context=True,
    )
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
            return Err(SdkError("path is required", code="invalid_argument"))
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
        await self._persist_state()
        return Ok(
            {
                "status": "objecting",
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
            if self._off_since is not None:
                # Coming back on: remember how long it was dark, so the panel can
                # say more than "it is on now".
                self._last_off_seconds = max(0.0, time.time() - self._off_since)
                self._off_since = None
            self._enabled = True
            self._pending_disable = None
            await self._persist_state()
            return Ok(
                {
                    "status": "enabled",
                    "enabled": True,
                    "message": self._text(
                        "messages.guard_enabled",
                        default="The dignity guard is on again.",
                    ),
                }
            )

        if not consent_token:
            token = secrets.token_urlsafe(16)
            self._pending_disable = {"token": token, "requested_at": time.time()}
            receipt = self._push_disable_request(token)
            return Ok(
                {
                    "status": "consent_pending",
                    "enabled": True,
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

        pending = self._pending_disable
        expected = str(pending.get("token")) if pending else ""
        if not pending or not secrets.compare_digest(str(consent_token), expected):
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
        # Leave a mark. This is the honest half of the promise: we cannot verify
        # who agreed, so at minimum the panel must be able to say that the guard
        # was turned off, when, and how many times.
        self._disabled_at = now
        self._off_since = now
        self._disable_count += 1
        self._pending_disable = None
        await self._persist_state()
        return Ok(
            {
                "status": "disabled",
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
        previous, self._tier = self._tier, cleaned
        if self._watcher is not None:
            self._watcher.tier = self._tier
        await self._persist_state()
        return Ok(
            {
                "status": "updated",
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

        count = len(list_backups(self._memory_backup_root()))
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
        report = self._build_diagnostics_report()
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
        timeout=30.0,
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
            "report": self._build_diagnostics_report(),
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
            backups = list_backups(backup_root)
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
            available = scan_memory(backup_root / chosen / MEMORY_DIR_NAME)
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
            reason = restore_file(backup_root, chosen, root, relative)
            if reason:
                skipped.append({"path": redact_path(relative), "reason": reason})
                self._health.record(f"memory_restore_{reason}")
            else:
                planned.append(redact_path(relative))

        if not dry_run and planned:
            # The snapshot we hold no longer describes what is on disk.
            self._last_memory_check_at = None

        return Ok(
            {
                "status": "planned" if dry_run else "restored",
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
