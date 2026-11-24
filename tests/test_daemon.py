import asyncio
import logging
import os
import signal

import pytest

from fakes import FakeClock, FakeSink, FakeSource
from tvsignal.cli import install_signal_handlers
from tvsignal.daemon import Daemon
from tvsignal.model import AuthError, Follower, Observation

ON = Observation(True, "On", "On")
OFF = Observation(True, "Standby", "Off")
GONE = Observation(False)


def make(script, sink, *, heartbeat=600):
    clock = FakeClock()
    source = FakeSource(script, clock)
    d = Daemon(source, sink, Follower(on_delay=0, off_delay=10),
               interval=0, heartbeat=heartbeat, clock=source.now)
    source.on_exhausted = d.stop
    return d, source


async def test_seeds_from_sink_and_does_not_reassert():
    sink = FakeSink(state=True)
    d, source = make([(t, ON) for t in range(0, 20, 2)], sink)
    await d.run()
    assert sink.calls == []                    # already ON: nothing sent
    assert source.closed and sink.closed


async def test_unknown_state_sends_first_decision():
    sink = FakeSink(state=None)
    d, _ = make([(0, OFF), (5, OFF), (10, OFF), (12, OFF)], sink)
    await d.run()
    assert sink.calls == [False]               # after off_delay, once


async def test_get_failure_is_treated_as_unknown(caplog):
    sink = FakeSink(state=True, get_fails=True)
    d, _ = make([(0, ON), (2, ON)], sink)
    with caplog.at_level(logging.WARNING, logger="tvsignal"):
        await d.run()
    assert sink.calls == [True]                # unknown -> first decision sent
    assert "could not read fake state" in caplog.text


async def test_set_failure_streak_logs_once_then_recovery(caplog):
    sink = FakeSink(state=False, set_failures=3)
    d, _ = make([(t, ON) for t in range(0, 12, 2)], sink)
    with caplog.at_level(logging.INFO, logger="tvsignal"):
        await d.run()
    assert sink.calls == [True, True, True, True]   # retried every poll until it worked
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "FAILED" in errors[0].getMessage()
    assert sum("recovered" in r.getMessage() for r in caplog.records) == 1
    assert d.follower.plug_on is True


async def test_owner_flicker_example():
    sink = FakeSink(state=False)
    d, _ = make([(0, ON), (0.5, OFF), (1, ON), (2, GONE), (3, ON), (5, ON)], sink)
    await d.run()
    assert sink.calls == [True]


async def test_logs_changes_and_heartbeat(caplog):
    sink = FakeSink(state=False)
    script = [(0, OFF), (100, OFF), (600, OFF), (700, OFF), (1200, OFF)]
    d, _ = make(script, sink, heartbeat=600)
    with caplog.at_level(logging.INFO, logger="tvsignal"):
        await d.run()
    msgs = [r.getMessage() for r in caplog.records]
    assert sum(m.startswith("tv reachable=") for m in msgs) == 1
    assert sum(m.startswith("heartbeat") for m in msgs) == 2   # at 600 and 1200


async def test_auth_error_propagates_and_closes():
    class AuthFailSource(FakeSource):
        async def observe(self):
            raise AuthError("re-pair")

    clock = FakeClock()
    source, sink = AuthFailSource([], clock), FakeSink(state=True)
    d = Daemon(source, sink, Follower(), interval=0, heartbeat=600, clock=clock)
    with pytest.raises(AuthError):
        await d.run()
    assert source.closed and sink.closed and sink.calls == []


async def test_sigterm_stops_cleanly_without_touching_plug():
    class Endless(FakeSource):
        async def observe(self):
            self.calls += 1
            return ON

    clock = FakeClock()
    source, sink = Endless([], clock), FakeSink(state=True)
    d = Daemon(source, sink, Follower(), interval=0.01, heartbeat=600, clock=clock)
    install_signal_handlers(d)
    try:
        task = asyncio.create_task(d.run())
        await asyncio.sleep(0.05)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(task, timeout=2)
    finally:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
    assert source.calls > 0 and sink.calls == [] and sink.closed
