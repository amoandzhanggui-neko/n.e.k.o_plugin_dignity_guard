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

    # It spoke, in the user's language, with the situation rather than a script.
    assert len(ctx.pushed_messages) == 1
    spoken = ctx.pushed_messages[0]
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
