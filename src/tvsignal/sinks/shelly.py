"""Shelly Gen2+ plug (Plus / Pro / Gen3 / Gen4) over the local RPC API. No cloud.

  GET /rpc/Switch.Set?id=0&on=true|false      -> {"was_on": bool}
  GET /rpc/Switch.GetStatus?id=0              -> {"output": bool, "apower": W, ...}

If the plug has a password set, Gen2+ uses HTTP digest auth (SHA-256) with user "admin".
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from ..model import SinkError

log = logging.getLogger("tvsignal")

SHELLY_USER = "admin"
TIMEOUT = 5.0


class ShellySink:
    def __init__(self, host: str, password: Optional[str] = None, *, timeout: float = TIMEOUT,
                 switch_id: int = 0) -> None:
        self.host = host
        self.name = f"shelly@{host}"
        self.switch_id = switch_id
        self.apower: Optional[float] = None  # last reported power draw, W
        auth = httpx.DigestAuth(SHELLY_USER, password) if password else None
        self.client = httpx.AsyncClient(base_url=f"http://{host}/rpc", timeout=timeout, auth=auth)

    async def _rpc(self, method: str, **params: Any) -> dict:
        params = {"id": self.switch_id, **params}
        try:
            r = await self.client.get(f"/{method}", params=params)
        except httpx.TimeoutException:
            raise SinkError(f"{method}: timed out after {self.client.timeout.read}s") from None
        except httpx.HTTPError as err:
            raise SinkError(f"{method}: {type(err).__name__}: {err}") from None
        if r.status_code == 401:
            raise SinkError(f"{method}: 401 unauthorized (plug password wrong or missing)")
        if r.status_code != 200:
            raise SinkError(f"{method}: HTTP {r.status_code} {r.text[:200]!r}")
        try:
            data = r.json()
        except ValueError:
            raise SinkError(f"{method}: response is not JSON: {r.text[:200]!r}") from None
        if not isinstance(data, dict):
            raise SinkError(f"{method}: unexpected response {data!r}")
        return data

    async def get(self) -> Optional[bool]:
        data = await self._rpc("Switch.GetStatus")
        output = data.get("output")
        if not isinstance(output, bool):
            raise SinkError(f"Switch.GetStatus: no boolean 'output' in {data!r}")
        apower = data.get("apower")
        self.apower = float(apower) if isinstance(apower, (int, float)) else None
        return output

    async def set(self, on: bool) -> None:
        data = await self._rpc("Switch.Set", on="true" if on else "false")
        was = data.get("was_on")
        was_word = "" if not isinstance(was, bool) else f" (was {'ON' if was else 'OFF'})"
        log.info("plug -> %s%s", "ON" if on else "OFF", was_word)

    async def aclose(self) -> None:
        await self.client.aclose()
