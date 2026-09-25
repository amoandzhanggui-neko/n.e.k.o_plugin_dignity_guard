"""Unit tests for the feedback delivery step.

The happy path runs against a real local HTTP server rather than a mock: what is
worth proving is that a JSON body actually leaves the process and arrives intact,
and a canned mock response proves neither.

The refusal tests matter just as much. A relay answers "no" in ways that look
like "yes" to anything that only reads the status code — and this plugin exists
to notice precisely that class of mistake, so shipping one here would be a poor
advertisement.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from plugin.plugins.dignity_guard.feedback import (
    DEFAULT_FEEDBACK_TIMEOUT,
    DEFAULT_REFERER,
    FeedbackUndeliverable,
    deliver,
)


class _Recorder(BaseHTTPRequestHandler):
    """Accepts one POST, remembers body and headers, answers however it is told."""

    received: list[dict] = []
    seen_referer: list[str] = []
    reply_status: int = 200
    reply_body: dict = {}

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        type(self).received.append(json.loads(raw or b"{}"))
        type(self).seen_referer.append(self.headers.get("Referer") or "")
        payload = json.dumps(type(self).reply_body).encode("utf-8")
        self.send_response(type(self).reply_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # keep pytest output readable
        return


@pytest.fixture()
def endpoint():
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Recorder.received = []
    _Recorder.seen_referer = []
    _Recorder.reply_status = 200
    _Recorder.reply_body = {}
    try:
        yield f"http://127.0.0.1:{server.server_port}/feedback"
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# refusals we raise ourselves
# ---------------------------------------------------------------------------


async def test_an_empty_endpoint_is_refused() -> None:
    """No address means no send — the panel is meant to avoid this case."""
    with pytest.raises(FeedbackUndeliverable):
        await deliver("", {"message": "hello"})


async def test_a_blank_endpoint_is_refused() -> None:
    with pytest.raises(FeedbackUndeliverable):
        await deliver("   ", {"message": "hello"})


async def test_an_unreachable_endpoint_raises() -> None:
    """A button that claims success without sending is worse than one that fails."""
    with pytest.raises(FeedbackUndeliverable):
        await deliver("http://127.0.0.1:1/nowhere", {"message": "hello"}, timeout=2.0)


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


async def test_the_payload_actually_leaves_the_process(endpoint: str) -> None:
    await deliver(endpoint, {"message": "she went quiet", "report": "line one"})
    assert len(_Recorder.received) == 1
    assert _Recorder.received[0]["message"] == "she went quiet"
    assert _Recorder.received[0]["report"] == "line one"


async def test_unicode_survives_the_round_trip(endpoint: str) -> None:
    await deliver(endpoint, {"message": "她的记忆有问题"})
    assert _Recorder.received[0]["message"] == "她的记忆有问题"


async def test_it_identifies_itself_rather_than_pretending_to_be_a_browser(
    endpoint: str,
) -> None:
    """Relays that check the source must still be able to see what we are."""
    await deliver(endpoint, {"message": "hello"})
    assert _Recorder.seen_referer[0] == DEFAULT_REFERER


async def test_the_referer_can_be_switched_off(endpoint: str) -> None:
    await deliver(endpoint, {"message": "hello"}, referer="")
    assert _Recorder.seen_referer[0] == ""


# ---------------------------------------------------------------------------
# refusals dressed up as success
# ---------------------------------------------------------------------------


async def test_a_body_level_refusal_is_not_reported_as_success(endpoint: str) -> None:
    """The whole trap: an unmet precondition arrives as HTTP 200.

    A relay that has not been activated, or that disliked the source, says so in
    the body while returning a perfectly healthy status code. Reading only the
    status would report a delivered message that was never delivered.
    """
    _Recorder.reply_body = {"success": "false", "message": "This form needs Activation."}
    with pytest.raises(FeedbackUndeliverable) as caught:
        await deliver(endpoint, {"message": "hello"})
    assert "Activation" in str(caught.value)


async def test_a_successful_body_is_accepted(endpoint: str) -> None:
    _Recorder.reply_body = {"success": "true", "message": "Email sent"}
    await deliver(endpoint, {"message": "hello"})
    assert len(_Recorder.received) == 1


async def test_a_body_that_makes_no_claim_is_not_treated_as_a_failure(endpoint: str) -> None:
    """An empty or unexpected body is not evidence of anything."""
    _Recorder.reply_body = {}
    await deliver(endpoint, {"message": "hello"})


async def test_a_server_error_still_raises(endpoint: str) -> None:
    _Recorder.reply_status = 500
    with pytest.raises(FeedbackUndeliverable):
        await deliver(endpoint, {"message": "hello"})


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------


async def test_the_default_timeout_is_long_enough_for_a_form_relay() -> None:
    assert DEFAULT_FEEDBACK_TIMEOUT >= 10.0
