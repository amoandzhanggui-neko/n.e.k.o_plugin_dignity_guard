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
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from plugin.plugins.dignity_guard import feedback as feedback_module
from plugin.plugins.dignity_guard.feedback import (
    DEFAULT_FEEDBACK_TIMEOUT,
    DEFAULT_REFERER,
    FEEDBACK_RETRY_DELAYS,
    TOTAL_BUDGET_SECONDS,
    FeedbackRateLimited,
    FeedbackUndeliverable,
    deliver,
)

#: Matches the ``timeout`` declared on the ``submit_feedback`` entry. See
#: ``__init__.py`` (the ``@plugin_entry(id="submit_feedback", timeout=75.0)``).
#: The whole ``deliver`` call must finish inside it.
SUBMIT_FEEDBACK_ENTRY_TIMEOUT = 75.0


class _Recorder(BaseHTTPRequestHandler):
    """Accepts one POST, remembers body and headers, answers however it is told."""

    received: list[dict] = []
    seen_referer: list[str] = []
    reply_status: int = 200
    reply_body: dict = {}
    #: Refuse this many times before settling down — what a real rate limit looks
    #: like from the caller's side. Zero means "never busy".
    rate_limit_times: int = 0
    #: Answer a body-level refusal this many times before settling down. Unlike
    #: ``rate_limit_times`` the status stays 200 with success:false, which is the
    #: trap this module must not mis-classify as a hard failure.
    body_refusal_times: int = 0
    body_refusal_body: dict = {}
    #: If set, a 429 response carries this ``Retry-After`` header so the
    #: budget-clipping path (not the fixed ``FEEDBACK_RETRY_DELAYS``) can be
    #: exercised.
    retry_after: str = ""

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        type(self).received.append(json.loads(raw or b"{}"))
        type(self).seen_referer.append(self.headers.get("Referer") or "")

        status = type(self).reply_status
        body = type(self).reply_body
        if type(self).rate_limit_times > 0:
            type(self).rate_limit_times -= 1
            status = 429
        elif type(self).body_refusal_times > 0:
            type(self).body_refusal_times -= 1
            status = 200
            body = type(self).body_refusal_body

        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if status == 429 and type(self).retry_after:
            self.send_header("Retry-After", type(self).retry_after)
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
    _Recorder.rate_limit_times = 0
    _Recorder.body_refusal_times = 0
    _Recorder.body_refusal_body = {}
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


async def test_a_server_error_still_raises(endpoint: str, monkeypatch) -> None:
    """A 5xx is retried a couple of times, and then admitted.

    The waits are zeroed here: what is under test is the verdict, not the
    patience, and a suite that sits through the real backoff for no added
    coverage is a suite people stop running.
    """
    monkeypatch.setattr(feedback_module, "FEEDBACK_RETRY_DELAYS", (0.0, 0.0))
    _Recorder.reply_status = 500
    with pytest.raises(FeedbackUndeliverable):
        await deliver(endpoint, {"message": "hello"})


# ---------------------------------------------------------------------------
# "come back later" is not "no"
# ---------------------------------------------------------------------------


async def test_a_rate_limit_that_never_clears_gets_its_own_error(
    endpoint: str, monkeypatch
) -> None:
    """A busy relay must not read to the user as a broken one.

    "It could not be sent" sends somebody hunting for a problem with their text
    or their network. "The channel is busy, press Send again shortly" tells them
    the truth and costs them one press. The two call for different sentences, so
    they need different types — if these ever collapse into one, the distinction
    disappears from the UI without anybody noticing.
    """
    monkeypatch.setattr(feedback_module, "FEEDBACK_RETRY_DELAYS", (0.0, 0.0))
    _Recorder.reply_status = 429
    with pytest.raises(FeedbackRateLimited):
        await deliver(endpoint, {"message": "hello"})


def test_the_backoff_fits_inside_the_entry_timeout() -> None:
    """Total waiting has to stay under what the SDK will allow for the action.

    A backoff longer than the entry timeout does not buy a retry — it buys a
    timeout, which looks like a hang to the person waiting. The budget is not
    just the retries: every attempt also spends the default request timeout, and
    the whole ``deliver`` call must land inside the entry timeout or the SDK
    kills it first.
    """
    attempts = len(FEEDBACK_RETRY_DELAYS) + 1
    spent = attempts * DEFAULT_FEEDBACK_TIMEOUT + sum(FEEDBACK_RETRY_DELAYS)
    assert spent <= SUBMIT_FEEDBACK_ENTRY_TIMEOUT


async def test_a_huge_retry_after_is_clipped_to_the_budget(
    endpoint: str, monkeypatch
) -> None:
    """A server that says "come back in an hour" must not cost an hour.

    ``Retry-After`` is honoured, but only up to the time ``deliver`` is willing
    to spend. Anything beyond the budget is clipped — otherwise a single rude
    relay could hold the whole action hostage until the SDK times it out.
    """
    monkeypatch.setattr(feedback_module, "FEEDBACK_RETRY_DELAYS", (0.0, 0.0))
    _Recorder.reply_status = 429
    _Recorder.retry_after = "3600"
    with pytest.raises(FeedbackRateLimited):
        await deliver(endpoint, {"message": "hello"})
    # The clip lives in the module; assert the declared ceiling is the only thing
    # a pathological Retry-After can approach, so the test documents the bound.
    assert TOTAL_BUDGET_SECONDS <= SUBMIT_FEEDBACK_ENTRY_TIMEOUT


def test_the_relay_is_not_hammered() -> None:
    """Retries are for a blip, not for insisting."""
    assert 1 <= len(FEEDBACK_RETRY_DELAYS) <= 3


async def test_a_rate_limit_that_clears_is_delivered(endpoint: str, monkeypatch) -> None:
    """The whole point: a busy relay costs a wait, not the report.

    The relay is told to refuse once and then accept, which is what a real rate
    limit looks like from the caller's side.
    """
    monkeypatch.setattr(feedback_module, "FEEDBACK_RETRY_DELAYS", (0.0, 0.0))
    # ``rate_limit_times`` refuses for a moment and then accepts; ``reply_status``
    # is left alone, because that is what the server goes back to.
    _Recorder.rate_limit_times = 1
    try:
        await deliver(endpoint, {"message": "hello"})
    finally:
        _Recorder.rate_limit_times = 0
    assert len(_Recorder.received) >= 1


async def test_a_body_level_rate_limit_is_its_own_error(
    endpoint: str, monkeypatch
) -> None:
    """A busy relay that answers 200 + success:false must not read as broken.

    This is the path the old string match missed: the status code is 200, so the
    verdict used to be FeedbackUndeliverable and the user was told to go hunt for
    a problem with their text or network. The relay was merely busy. With the
    explicit flag the right exception now fires, and it is waited out rather than
    raised on the first try.
    """
    monkeypatch.setattr(feedback_module, "FEEDBACK_RETRY_DELAYS", (0.0, 0.0))
    _Recorder.body_refusal_body = {"success": "false", "message": "Too many requests"}
    # Refuse on every attempt so we exhaust the loop and reach the final verdict.
    _Recorder.body_refusal_times = 3
    try:
        with pytest.raises(FeedbackRateLimited):
            await deliver(endpoint, {"message": "hello"})
    finally:
        _Recorder.body_refusal_times = 0
    # Three attempts means three posts went out, not one.
    assert len(_Recorder.received) == 3


async def test_a_body_level_real_refusal_is_not_retried(
    endpoint: str, monkeypatch
) -> None:
    """A refusal that is not "slow down" is the truth, not a blip.

    "Invalid form key" contains none of the rate-limit hints, so it must be
    raised immediately as FeedbackUndeliverable — no waiting, no retries. This
    guards the other edge of the body-level branch: only the *rate-sounding*
    refusals get the forgiving treatment.
    """
    monkeypatch.setattr(feedback_module, "FEEDBACK_RETRY_DELAYS", (0.0, 0.0))
    _Recorder.body_refusal_body = {"success": "false", "message": "Invalid form key"}
    _Recorder.body_refusal_times = 3
    try:
        with pytest.raises(FeedbackUndeliverable):
            await deliver(endpoint, {"message": "hello"})
    finally:
        _Recorder.body_refusal_times = 0
    # Raised on the first attempt, so exactly one post should have been made.
    assert len(_Recorder.received) == 1


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------


async def test_the_default_timeout_is_long_enough_for_a_form_relay() -> None:
    assert DEFAULT_FEEDBACK_TIMEOUT >= 10.0
