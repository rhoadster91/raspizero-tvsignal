import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlparse
from urllib.request import parse_http_list, parse_keqv_list

import pytest

from httpfake import serve
from tvsignal.model import SinkError
from tvsignal.sinks.shelly import ShellySink


class FakePlug:
    """Shelly Gen2 RPC subset, with optional SHA-256 digest auth like the real firmware."""

    realm = "shellyplusplugukfake"

    def __init__(self, password=None):
        self.password = password
        self.output = False
        self.requests = []
        self.nonce = secrets.token_hex(8)

    def _authorized(self, h) -> bool:
        header = h.headers.get("Authorization", "")
        if not header.startswith("Digest "):
            return False
        p = parse_keqv_list(parse_http_list(header[len("Digest "):]))
        sha = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731
        ha1 = sha(f"admin:{self.realm}:{self.password}")
        ha2 = sha(f"GET:{p['uri']}")
        expected = sha(f"{ha1}:{p['nonce']}:{p['nc']}:{p['cnonce']}:{p['qop']}:{ha2}")
        return p.get("username") == "admin" and p.get("response") == expected

    def __call__(self, h):
        if self.password and not self._authorized(h):
            challenge = (f'Digest qop="auth", realm="{self.realm}", nonce="{self.nonce}", '
                         f'algorithm=SHA-256')
            return 401, {"WWW-Authenticate": challenge}, ""
        u = urlparse(h.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.requests.append((u.path, q))
        if u.path == "/rpc/Switch.Set":
            was, self.output = self.output, q["on"] == "true"
            return 200, {}, {"was_on": was}
        if u.path == "/rpc/Switch.GetStatus":
            return 200, {}, {"id": 0, "source": "http", "output": self.output, "apower": 4.2 if self.output else 0.0,
                             "voltage": 240.1}
        return 404, {}, {"code": 404, "message": "No handler"}


def host_of(server):
    return f"127.0.0.1:{server.server_address[1]}"


async def test_set_and_get():
    plug = FakePlug()
    with serve(plug) as srv:
        sink = ShellySink(host_of(srv))
        try:
            assert await sink.get() is False
            await sink.set(True)
            assert await sink.get() is True
            assert sink.apower == 4.2
            await sink.set(False)
            assert await sink.get() is False
        finally:
            await sink.aclose()
    assert ("/rpc/Switch.Set", {"id": "0", "on": "true"}) in plug.requests


async def test_digest_auth_ok():
    plug = FakePlug(password="s3cret")
    with serve(plug) as srv:
        sink = ShellySink(host_of(srv), "s3cret")
        try:
            await sink.set(True)
            assert await sink.get() is True
        finally:
            await sink.aclose()


@pytest.mark.parametrize("password", [None, "wrong"])
async def test_digest_auth_rejected(password):
    with serve(FakePlug(password="s3cret")) as srv:
        sink = ShellySink(host_of(srv), password)
        try:
            with pytest.raises(SinkError, match="401"):
                await sink.set(True)
        finally:
            await sink.aclose()


@pytest.mark.parametrize(
    "response, match",
    [
        ((500, {}, {"code": -1, "message": "internal"}), "HTTP 500"),
        ((200, {}, "not json"), "not JSON"),
        ((200, {}, {"id": 0}), "no boolean 'output'"),
    ],
)
async def test_bad_responses(response, match):
    with serve(lambda h: response) as srv:
        sink = ShellySink(host_of(srv))
        try:
            with pytest.raises(SinkError, match=match):
                await sink.get()
        finally:
            await sink.aclose()


async def test_timeout():
    def slow(h):
        time.sleep(1.0)
        return 200, {}, {"output": True}

    with serve(slow) as srv:
        sink = ShellySink(host_of(srv), timeout=0.2)
        try:
            with pytest.raises(SinkError, match="timed out"):
                await sink.get()
        finally:
            await sink.aclose()


async def test_unreachable():
    with serve(FakePlug()) as srv:
        host = host_of(srv)
    sink = ShellySink(host, timeout=1)  # server is closed now
    try:
        with pytest.raises(SinkError):
            await sink.set(True)
    finally:
        await sink.aclose()
