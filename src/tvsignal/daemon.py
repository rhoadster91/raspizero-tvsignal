"""The poll loop. Knows nothing about Philips or Shelly: only Observation, Follower, sinks."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from .model import Follower, Observation, SignalSink, SignalSource, SinkError

log = logging.getLogger("tvsignal")


def _word(on: Optional[bool]) -> str:
    return "unknown" if on is None else ("ON" if on else "OFF")


class Daemon:
    def __init__(
        self,
        source: SignalSource,
        sink: SignalSink,
        follower: Follower,
        *,
        interval: float,
        heartbeat: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source = source
        self.sink = sink
        self.follower = follower
        self.interval = interval
        self.heartbeat = heartbeat
        self.clock = clock
        self._stop = asyncio.Event()
        self._last_obs: Optional[Observation] = None
        self._last_log = 0.0
        self._sink_failing = False

    def stop(self) -> None:
        """Ask the loop to finish (safe to call from a signal handler)."""
        self._stop.set()

    async def run(self) -> None:
        """Run until stop(). AuthError from the source propagates. Never touches the plug on exit."""
        try:
            await self._seed()
            while not self._stop.is_set():
                await self.tick()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.source.aclose()
            await self.sink.aclose()
            log.info("stopped")

    async def _seed(self) -> None:
        """Re-sync with the sink once, at start-up only."""
        try:
            state = await self.sink.get()
        except SinkError as err:
            log.warning("could not read %s state, treating it as unknown: %s", self.sink.name, err)
            state = None
        self.follower.plug_on = state
        log.info("%s initial state: %s", self.sink.name, _word(state))

    async def tick(self) -> None:
        now = self.clock()
        obs = await self.source.observe()
        if obs != self._last_obs:
            log.info(
                "tv reachable=%s power=%s screen=%s -> active=%s",
                obs.reachable, obs.powerstate, obs.screenstate, obs.active,
            )
            self._last_obs, self._last_log = obs, now
        elif now - self._last_log >= self.heartbeat:
            log.info("heartbeat: unchanged (active=%s, plug=%s)", obs.active, _word(self.follower.plug_on))
            self._last_log = now

        action = self.follower.update(obs, now)
        if action is not None:
            await self._apply(action)

    async def _apply(self, on: bool) -> None:
        try:
            await self.sink.set(on)
        except SinkError as err:
            self.follower.plug_on = None  # unknown: the Follower retries on the next poll
            if not self._sink_failing:
                log.error("%s -> %s FAILED (retrying every poll, logged once): %s",
                          self.sink.name, _word(on), err)
                self._sink_failing = True
            return
        if self._sink_failing:
            log.info("%s recovered", self.sink.name)
            self._sink_failing = False
