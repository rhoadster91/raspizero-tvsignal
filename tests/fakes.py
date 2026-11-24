"""Test doubles for the daemon."""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from tvsignal.model import Observation, SinkError


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeSource:
    """Plays (time, observation) pairs; calls on_exhausted after the last one.

    Use `source.now` as the daemon's clock: it returns the time of the observation about
    to be made (the daemon reads the clock just before observing).
    """

    def __init__(self, script: Iterable[tuple[float, Observation]], clock: FakeClock) -> None:
        self.script = list(script)
        self.clock = clock
        self.on_exhausted: Optional[Callable[[], None]] = None
        self.closed = False
        self.calls = 0

    def now(self) -> float:
        if self.calls < len(self.script):
            self.clock.now = self.script[self.calls][0]
        return self.clock.now

    async def observe(self) -> Observation:
        t, obs = self.script[self.calls]
        self.calls += 1
        self.clock.now = t
        if self.calls == len(self.script) and self.on_exhausted:
            self.on_exhausted()
        return obs

    async def aclose(self) -> None:
        self.closed = True


class FakeSink:
    name = "fake"

    def __init__(self, state: Optional[bool] = None, *, get_fails: bool = False, set_failures: int = 0) -> None:
        self.state = state
        self.get_fails = get_fails
        self.set_failures = set_failures  # fail this many set() calls first
        self.calls: list[bool] = []       # every set() attempt, failed or not
        self.closed = False

    async def get(self) -> Optional[bool]:
        if self.get_fails:
            raise SinkError("boom")
        return self.state

    async def set(self, on: bool) -> None:
        self.calls.append(on)
        if self.set_failures > 0:
            self.set_failures -= 1
            raise SinkError("plug unreachable")
        self.state = on

    async def aclose(self) -> None:
        self.closed = True
