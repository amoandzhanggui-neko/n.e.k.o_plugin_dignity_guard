"""Critical-section exclusion, and the multi-loop trap it used to fall into.

The guard mutates ``_state`` from two kinds of caller: the twenty-second timer
callback and the user-facing entries. Those are not guaranteed to run on the
same event loop, and an ``asyncio.Lock`` belongs to exactly one loop.

The first two tests are a **red/green pair** and are meant to be read together:

* ``test_the_OLD_shape_loses_exclusion_across_loops`` shows the trap. It builds
  the old helper's behaviour (one lock per loop) and demonstrates that a second
  loop's ``locked()`` answers ``False`` while the first loop is holding its own
  lock — i.e. exclusion is gone, with nothing raised and nothing logged.
* ``test_exclusion_holds_across_loops`` asserts the current implementation does
  not have that hole.

Keeping the red test around is deliberate: if somebody later "simplifies" the
flag back into an ``asyncio.Lock``, this pair explains why that is wrong.
"""
from __future__ import annotations

import asyncio
import threading

from plugin.plugins.dignity_guard import DignityGuardPlugin


def _bare_plugin() -> DignityGuardPlugin:
    """An instance with only the exclusion fields set.

    Enough to exercise ``_begin_exclusive`` / ``_end_exclusive`` without
    building a whole plugin (config, store, logger, watcher…).
    """
    plugin = object.__new__(DignityGuardPlugin)
    plugin._busy = False
    plugin._busy_flag = threading.Lock()
    return plugin


# --------------------------------------------------------------------------
# RED: the shape the code used to have
# --------------------------------------------------------------------------


def test_the_OLD_shape_loses_exclusion_across_loops() -> None:
    """The trap: one lock per loop means each loop sees an untouched lock."""
    locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}

    def old_poll_lock() -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if loop not in locks:
            locks[loop] = asyncio.Lock()
        return locks[loop]

    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    acquired: list[asyncio.Lock] = []
    try:

        async def _hold_on_a() -> None:
            lock = old_poll_lock()
            await lock.acquire()
            acquired.append(lock)

        loop_a.run_until_complete(_hold_on_a())

        async def _what_b_sees() -> tuple[bool, asyncio.Lock]:
            lock = old_poll_lock()
            return lock.locked(), lock

        seen_b, lock_b = loop_b.run_until_complete(_what_b_sees())

        assert acquired[0].locked() is True, "loop A really is holding its lock"
        assert lock_b is not acquired[0], "…but loop B was handed a different lock"
        assert seen_b is False, (
            "⚠️ and that is the bug: loop B is told 'not locked' while loop A is "
            "inside the critical section, so both would proceed"
        )
        acquired[0].release()
    finally:
        loop_a.close()
        loop_b.close()


# --------------------------------------------------------------------------
# GREEN: the current implementation
# --------------------------------------------------------------------------


def test_exclusion_holds_across_loops() -> None:
    """A later caller on *another* loop is still refused — the fix."""
    plugin = _bare_plugin()
    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    try:

        async def _enter() -> bool:
            return plugin._begin_exclusive()

        assert loop_a.run_until_complete(_enter()) is True, "first caller enters"
        assert loop_b.run_until_complete(_enter()) is False, (
            "⚠️ the second caller, on a different loop, must be refused — this is "
            "exactly what the old asyncio.Lock-based helper got wrong"
        )

        plugin._end_exclusive()

        assert loop_b.run_until_complete(_enter()) is True, "once released, it opens"
        plugin._end_exclusive()
    finally:
        loop_a.close()
        loop_b.close()


def test_the_flag_is_never_left_set_by_a_raising_critical_section() -> None:
    """``_end_exclusive`` must be reachable from a ``finally``.

    A critical section that raises without clearing the flag would tell every
    later caller ``busy`` forever — a deadlock that looks like "the button
    stopped working" and would be very hard to trace back here.
    """
    plugin = _bare_plugin()

    class _Boom(RuntimeError):
        pass

    try:
        assert plugin._begin_exclusive() is True
        raise _Boom("something inside the critical section failed")
    except _Boom:
        plugin._end_exclusive()

    assert plugin._begin_exclusive() is True, "the flag was released after the error"
    plugin._end_exclusive()


def test_begin_and_end_are_balanced_under_contention() -> None:
    """Many threads racing: exactly one gets in at a time, and all get through.

    Uses real threads (not coroutines) because the flag is deliberately
    thread-agnostic — the same property that makes it loop-agnostic.
    """
    plugin = _bare_plugin()
    inside = 0
    max_inside = 0
    admitted = 0
    refused = 0
    lock = threading.Lock()

    def _worker() -> None:
        nonlocal inside, max_inside, admitted, refused
        for _ in range(200):
            if plugin._begin_exclusive():
                with lock:
                    inside += 1
                    max_inside = max(max_inside, inside)
                    admitted += 1
                with lock:
                    inside -= 1
                plugin._end_exclusive()
            else:
                with lock:
                    refused += 1

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_inside == 1, f"at most one holder at a time, saw {max_inside}"
    assert admitted > 0, "somebody must get in"
    assert plugin._busy is False, "the flag is clear once everyone is done"
