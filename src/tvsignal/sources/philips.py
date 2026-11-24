"""Philips Android TV (JointSpace API v6, 2016+) over HTTPS port 1926, via ha-philipsjs.

The TV uses a self-signed certificate and digest auth with credentials from a one-off
PIN pairing (see `pair`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from haphilipsjs import AutenticationFailure, ConnectionFailure, GeneralFailure, PairingFailure, PhilipsTV

from ..model import AuthError, Observation

log = logging.getLogger("tvsignal")

API_VERSION = 6
PORT = 1926
OBSERVE_TIMEOUT = 5.0  # the library's read timeout is 20 s; don't let one poll stall the loop


@dataclass(frozen=True)
class Credentials:
    host: str
    username: str
    password: str


class PairError(Exception):
    """Pairing failed; the message is meant for the user."""


def load_credentials(path: Path) -> Credentials:
    try:
        data = json.loads(path.read_text())
        return Credentials(host=data["host"], username=data["username"], password=data["password"])
    except FileNotFoundError:
        raise AuthError(f"no credentials at {path}; pair with the TV first (tvsignal pair)") from None
    except PermissionError:
        raise AuthError(f"cannot read {path} (run as the service user?)") from None
    except (ValueError, KeyError, TypeError) as err:
        raise AuthError(f"credentials file {path} is invalid ({err!r}); pair again") from None


def save_credentials(path: Path, creds: Credentials) -> None:
    """Write atomically with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".credentials-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"host": creds.host, "username": creds.username, "password": creds.password}, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def make_tv(host: str, creds: Optional[Credentials] = None) -> PhilipsTV:
    return PhilipsTV(
        host,
        api_version=API_VERSION,
        secured_transport=True,
        username=creds.username if creds else None,
        password=creds.password if creds else None,
    )


class PhilipsSource:
    def __init__(self, host: str, creds: Credentials, *, timeout: float = OBSERVE_TIMEOUT) -> None:
        self.host = host
        self.timeout = timeout
        self.tv = make_tv(host, creds)

    async def _read(self) -> Observation:
        await self.tv.getPowerState()
        await self.tv.getScreenState()
        return Observation(True, self.tv.powerstate, self.tv.screenstate)

    async def observe(self) -> Observation:
        try:
            return await asyncio.wait_for(self._read(), timeout=self.timeout)
        except AutenticationFailure as err:  # subclass of GeneralFailure: must come first
            raise AuthError(f"the TV rejected the saved credentials ({err}); pair again") from err
        except GeneralFailure as err:  # ConnectionFailure, ProtocolFailure, timeouts
            log.debug("TV unreachable: %r", err)
        except asyncio.TimeoutError:
            log.debug("TV unreachable: no answer within %.0fs", self.timeout)
        return Observation(reachable=False)

    async def aclose(self) -> None:
        await self.tv.aclose()


async def pair(host: str, read_pin) -> Credentials:
    """Run the PIN pairing handshake. `read_pin` is an async callable returning the PIN."""
    tv = make_tv(host)
    try:
        state = await tv.pairRequest(
            app_id="app.id",
            app_name="tvsignal",
            device_name=platform.node() or "tvsignal",
            device_os="Linux",
            type="native",
        )
        pin = (await read_pin()).strip()
        username, password = await tv.pairGrant(state, pin)
    except (httpx.ConnectError, httpx.TimeoutException, ConnectionFailure):
        raise PairError(
            f"Could not reach the TV at {host}:{PORT}. Check that the TV is on and the IP is right "
            "(on macOS, also that your terminal app is allowed to access the local network)."
        ) from None
    except PairingFailure as err:
        raise PairError(f"Pairing refused by the TV: {err}. Wrong or expired PIN? Run pair again.") from None
    finally:
        await tv.aclose()
    return Credentials(host=host, username=username, password=password)
