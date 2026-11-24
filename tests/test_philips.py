"""PhilipsSource against a local fake TLS server. The library hard-codes port 1926."""

import datetime
import socket
import ssl
import time

import pytest

from httpfake import serve
from tvsignal.model import AuthError
from tvsignal.sources.philips import Credentials, PhilipsSource, load_credentials, save_credentials

JSON = {"Content-Type": "application/json"}
CREDS = Credentials(host="127.0.0.1", username="user", password="pass")


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # ignore TIME_WAIT from earlier tests
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


@pytest.fixture(scope="module")
def tls_context(tmp_path_factory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-tv")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    d = tmp_path_factory.mktemp("tls")
    (d / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (d / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                  serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(d / "cert.pem", d / "key.pem")
    return ctx


@pytest.fixture
def fake_tv(tls_context):
    if not _port_free(1926):
        pytest.skip("port 1926 is in use; the TV library always connects to it")
    state = {"power": "On", "screen": "On", "status": 200, "delay": 0.0}

    def route(h):
        time.sleep(state["delay"])
        if state["status"] != 200:
            return state["status"], {}, ""
        if h.path == "/6/powerstate":
            return 200, JSON, {"powerstate": state["power"]}
        if h.path == "/6/screenstate":
            return 200, JSON, {"screenstate": state["screen"]}
        return 404, {}, ""

    with serve(route, port=1926, ssl_context=tls_context):
        yield state


@pytest.mark.parametrize("power, screen, active", [
    ("On", "On", True), ("On", "Off", False), ("Standby", "Off", False), ("StandbyKeep", "Off", False),
])
async def test_observe_states(fake_tv, power, screen, active):
    fake_tv.update(power=power, screen=screen)
    src = PhilipsSource("127.0.0.1", CREDS)
    try:
        obs = await src.observe()
    finally:
        await src.aclose()
    assert (obs.reachable, obs.powerstate, obs.screenstate, obs.active) == (True, power, screen, active)


async def test_auth_failure_raises(fake_tv):
    fake_tv["status"] = 401
    src = PhilipsSource("127.0.0.1", CREDS)
    try:
        with pytest.raises(AuthError, match="pair again"):
            await src.observe()
    finally:
        await src.aclose()


async def test_slow_tv_counts_as_unreachable(fake_tv):
    fake_tv["delay"] = 1.0
    src = PhilipsSource("127.0.0.1", CREDS, timeout=0.3)
    try:
        assert (await src.observe()).reachable is False
    finally:
        await src.aclose()


async def test_unreachable():
    if not _port_free(1926):
        pytest.skip("port 1926 is in use")
    src = PhilipsSource("127.0.0.1", CREDS)  # nothing listening
    try:
        obs = await src.observe()
    finally:
        await src.aclose()
    assert obs.reachable is False and not obs.active


def test_credentials_roundtrip(tmp_path):
    p = tmp_path / "sub" / "credentials.json"
    save_credentials(p, CREDS)
    assert p.stat().st_mode & 0o777 == 0o600
    assert load_credentials(p) == CREDS
    assert [f.name for f in p.parent.iterdir()] == ["credentials.json"]


def test_missing_credentials(tmp_path):
    with pytest.raises(AuthError, match="pair"):
        load_credentials(tmp_path / "nope.json")
