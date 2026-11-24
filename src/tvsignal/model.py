"""Core types and the decision logic. Pure: no I/O, clock injected.

The active rule and the Follower are ported unchanged from the proof of concept, which
was verified against the real TV. Do not change them without a reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


class SinkError(Exception):
    """A sink could not read or set its state (network, HTTP or protocol error)."""


class AuthError(Exception):
    """The TV rejected our credentials (or there are none). The fix is to re-pair."""


@dataclass(frozen=True)
class Observation:
    reachable: bool
    powerstate: Optional[str] = None   # e.g. "On", "Standby", "StandbyKeep"
    screenstate: Optional[str] = None  # e.g. "On", "Off" (None if not supported)

    @property
    def active(self) -> bool:
        """True when someone is plausibly watching: powered on AND screen on.

        Requiring the screen to be on filters out background wakes (updates, network
        wakes) where the TV reports a power state with the panel dark.
        """
        if not self.reachable or self.powerstate != "On":
            return False
        # If the TV doesn't report screen state at all, fall back to powerstate.
        return self.screenstate in (None, "On")


@dataclass
class Follower:
    """Debounce: ON after `on_delay` s active, OFF after `off_delay` s continuously inactive."""

    on_delay: float = 0.0     # seconds TV must look active before plug ON
    off_delay: float = 10.0   # seconds TV must look inactive before plug OFF
    plug_on: Optional[bool] = None  # last commanded state (None = unknown)
    _candidate: Optional[bool] = field(default=None, repr=False)
    _since: float = field(default=0.0, repr=False)

    def update(self, obs: Observation, now: float) -> Optional[bool]:
        """Feed one observation. Returns True/False when the plug should switch, else None."""
        want = obs.active
        if want != self._candidate:
            self._candidate, self._since = want, now
        if want == self.plug_on:
            return None
        delay = self.on_delay if want else self.off_delay
        if now - self._since >= delay:
            self.plug_on = want
            return want
        return None


class SignalSource(Protocol):
    """Something that says whether the TV is on."""

    async def observe(self) -> Observation:
        """One reading. Never raises for "unreachable"; raises AuthError for bad credentials."""
        ...

    async def aclose(self) -> None: ...


class SignalSink(Protocol):
    """Something that follows the signal (a smart plug, or just the log)."""

    name: str  # short description for logs, e.g. "log" or "shelly@<host>"

    async def get(self) -> Optional[bool]:
        """Current state; None if unknown. Raises SinkError on failure."""
        ...

    async def set(self, on: bool) -> None:
        """Switch. Raises SinkError on failure."""
        ...

    async def aclose(self) -> None: ...
