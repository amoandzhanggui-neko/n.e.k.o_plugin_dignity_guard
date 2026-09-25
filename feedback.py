"""Delivering feedback, when the author has provided somewhere to deliver it.

One click is only possible if there is somewhere to click *to*. This module is
that step and nothing else: it takes a URL and a payload, posts them, and either
returns or raises.

Why the endpoint is configuration rather than a constant
--------------------------------------------------------
A built-in address would mean a built-in credential, and a credential shipped
inside a plugin is not a credential — anybody who unpacks the file has it. So the
author supplies an address they control. Without one, the panel does not offer to
send at all; it falls back to copy-and-open rather than show a button that cannot
work. That is also why there is no "post it to the project tracker for you"
route here: a tracker needs an account, and an account needs a key.

Nothing in this module runs on its own
--------------------------------------
The user presses Send. That single act is the entire difference between a
feedback button and surveillance, and it is why this file has no timer, no
"pending queue", and no retry-on-startup.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

import httpx

__all__ = [
    "DEFAULT_FEEDBACK_TIMEOUT",
    "FEEDBACK_RETRY_DELAYS",
    "FeedbackRateLimited",
    "FeedbackUndeliverable",
    "deliver",
]

#: Long enough for a slow form service, short enough that the button does not
#: feel broken while the user waits.
DEFAULT_FEEDBACK_TIMEOUT = 15.0

#: How long to wait before re-posting, when the relay says "too many".
#:
#: The relay answers a rate-limited post in about a second, so a wait here costs
#: the user almost nothing — while the alternative costs them the whole report
#: they just wrote by hand. Sending them away with "try again later" would be a
#: poor trade against six seconds of patience.
#:
#: Measured 2026-09-25: five posts two seconds apart all went through and a
#: sixth one second later came back ``429``, so this only ever fires when
#: somebody really is sending in a hurry.
FEEDBACK_RETRY_DELAYS: tuple[float, ...] = (6.0, 15.0)

#: Body-level phrasing that means "come back later" rather than "never".
#: FormSubmit reports some refusals as ``HTTP 200`` with ``success: false``, so
#: the status code alone cannot tell a rate limit from a misconfiguration.
_RATE_LIMIT_HINTS = ("rate", "limit", "too many", "throttl", "slow down")

#: Some relay services (FormSubmit, for one) refuse a request that arrives with
#: no ``Referer``, so that local HTML files cannot use them as a free backend.
#: Sending the plugin's own page identifies us honestly instead of impersonating
#: a browser — and it is the same value the channel was activated under, so the
#: service recognises the form.
DEFAULT_REFERER = "https://github.com/amoandzhanggui-neko/n.e.k.o_plugin_dignity_guard"

#: What we call ourselves in the request. Deliberately the plugin rather than a
#: browser string: whoever reads their server logs deserves to know.
DEFAULT_USER_AGENT = "dignity_guard-plugin/0.1.0"

#: Where a one-click report goes by default: a FormSubmit relay keyed to the
#: maintainer's mailbox.
#:
#: Note it is the *hash* FormSubmit issued, not an address. The mailbox behind it
#: appears nowhere in the plugin, so unpacking the file hands nobody an address
#: to spam; and the worst a leaked key can do is put mail into an inbox that
#: exists to receive it. A credential that could *read* that mailbox would be a
#: different matter entirely, which is why nothing of the sort is in this file
#: or anywhere near it.
#:
#: Set ``feedback_endpoint`` in the configuration to point somewhere else.
DEFAULT_FEEDBACK_ENDPOINT = "https://formsubmit.co/ajax/ffa69e2f591e397d28f8aaa7b66a4ec7"


class FeedbackUndeliverable(RuntimeError):
    """The note could not be handed over. Carries the endpoint for the log."""

    def __init__(self, endpoint: str, cause: object) -> None:
        super().__init__(f"could not deliver feedback to {endpoint!r}: {cause}")
        self.endpoint = endpoint
        self.cause = cause


class FeedbackRateLimited(FeedbackUndeliverable):
    """The relay is busy, not broken. Waited out; still said no.

    Kept separate because it deserves different words in front of a user. "It
    could not be sent" invites them to check their network and their text; "the
    channel is busy, give it a minute" tells them their report is fine and the
    fix is to press Send again shortly. The second one is the truth here.
    """


async def deliver(
    endpoint: str,
    payload: Mapping[str, Any],
    *,
    timeout: float = DEFAULT_FEEDBACK_TIMEOUT,
    referer: str = DEFAULT_REFERER,
) -> None:
    """POST ``payload`` as JSON to ``endpoint``.

    Raises :class:`FeedbackUndeliverable` on anything whatsoever. A feedback
    button that reports success without sending anything is worse than one that
    admits it failed.

    **A 200 is not enough.** FormSubmit reports refusal as ``HTTP 200`` with
    ``{"success": "false"}`` in the body — an unactivated form, a missing
    Referer, a rate limit all arrive looking like success to anyone who only
    checks the status code. So the body is inspected too, and a relayed refusal
    is raised as loudly as a connection error. This is the same "reports success,
    did nothing" failure mode this plugin exists to watch for in other software;
    it would be poor form to ship it here.

    Note the difference from the main-server client: this one **honours the
    environment's proxy settings**. The main server lives on loopback, where a
    system-wide proxy famously breaks requests; a feedback endpoint lives on the
    open internet, where the proxy is exactly what you want.
    """
    target = str(endpoint or "").strip()
    if not target:
        raise FeedbackUndeliverable(target, "no endpoint configured")

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    if referer:
        headers["Referer"] = referer

    # "Come back later" is not the same answer as "no". A relay that is busy, or
    # one that thinks we are posting too fast, is telling us to wait — and the
    # user is standing there with a note they already wrote. So those two answers
    # are waited out rather than handed back as a failure.
    attempts = len(FEEDBACK_RETRY_DELAYS) + 1
    last_error: object = "not attempted"

    for attempt in range(attempts):
        retry_after: float | None = None

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    target, json=dict(payload), headers=headers
                )
        except Exception as exc:  # noqa: BLE001 - transport failure, retried below
            last_error = exc
        else:
            if response.status_code == 429:
                last_error = "rate limited (HTTP 429)"
                retry_after = _retry_after_seconds(response)
            elif response.status_code >= 500:
                last_error = f"the relay is unwell (HTTP {response.status_code})"
            else:
                try:
                    response.raise_for_status()
                except Exception as exc:  # noqa: BLE001 - a 4xx that is not 429
                    raise FeedbackUndeliverable(target, exc) from exc

                # Body-level refusal. Deliberately tolerant about the shape: a
                # service that answers with HTML, or with no body at all, is not
                # thereby a failure.
                body: object = None
                try:
                    body = response.json()
                except Exception:  # noqa: BLE001 - not JSON means "no claim made"
                    body = None

                refused = isinstance(body, Mapping) and (
                    str(body.get("success", "")).lower() == "false"
                )
                if not refused:
                    return  # delivered

                reason = str(
                    body.get("message") or body.get("error") or "the service refused it"
                )
                # A refusal that *sounds* like "slow down" gets one more try.
                # Anything else is a real answer, and repeating it would only
                # delay telling the user the truth.
                if not any(hint in reason.lower() for hint in _RATE_LIMIT_HINTS):
                    raise FeedbackUndeliverable(target, reason)
                last_error = reason

        if attempt < len(FEEDBACK_RETRY_DELAYS):
            wait = (
                retry_after
                if retry_after is not None
                else FEEDBACK_RETRY_DELAYS[attempt]
            )
            await asyncio.sleep(max(0.0, wait))

    # Out of attempts. If what kept us waiting was the relay being busy rather
    # than anything about the message, say so — it changes what the user does
    # next (press Send again shortly, versus go looking for a problem).
    if isinstance(last_error, str) and "429" in last_error:
        raise FeedbackRateLimited(target, last_error)
    raise FeedbackUndeliverable(target, last_error)


def _retry_after_seconds(response: "httpx.Response") -> float | None:
    """Read ``Retry-After`` when the relay bothers to send one.

    It may be seconds or an HTTP date; only the first form is used, because a
    date would mean trusting two clocks to agree.
    """
    raw = (response.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return max(0.0, min(seconds, 30.0))  # never wait longer than a caller would
