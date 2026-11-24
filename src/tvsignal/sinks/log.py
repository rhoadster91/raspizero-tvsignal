"""Log-only sink: says what it would do. Used for the trial period, or when the speakers stay on."""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("tvsignal")


class LogSink:
    name = "log-only sink"

    def __init__(self) -> None:
        self.state: Optional[bool] = None

    async def get(self) -> Optional[bool]:
        return self.state

    async def set(self, on: bool) -> None:
        self.state = on
        log.info("WOULD switch plug %s", "ON" if on else "OFF")

    async def aclose(self) -> None:
        pass
