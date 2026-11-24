"""Configuration: TOML file, then CLI overrides, then validation."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Optional

DEFAULT_CONFIG_PATH = Path("/etc/tvsignal/config.toml")
PASSWORD_ENV = "TVSIGNAL_SINK_PASSWORD"
SINK_TYPES = ("log", "shelly")


def default_credentials_path() -> Path:
    return Path.home() / ".config" / "tvsignal" / "credentials.json"


class ConfigError(Exception):
    """The configuration is missing, unreadable or invalid."""


@dataclass(frozen=True)
class TVConfig:
    host: Optional[str] = None  # None: use the host saved at pairing
    credentials: Path = field(default_factory=default_credentials_path)


@dataclass(frozen=True)
class SinkConfig:
    type: str = "log"
    host: Optional[str] = None
    password: Optional[str] = None


@dataclass(frozen=True)
class TimingConfig:
    interval: float = 2.0
    on_delay: float = 0.0
    off_delay: float = 10.0
    heartbeat: float = 600.0


@dataclass(frozen=True)
class Config:
    tv: TVConfig = field(default_factory=TVConfig)
    sink: SinkConfig = field(default_factory=SinkConfig)
    timing: TimingConfig = field(default_factory=TimingConfig)

    def describe(self) -> str:
        """One line with the effective values (never the password)."""
        sink = self.sink.type if self.sink.type == "log" else f"{self.sink.type}@{self.sink.host}"
        if self.sink.password:
            sink += " (auth)"
        t = self.timing
        return (
            f"tv={self.tv.host or '<from credentials>'} sink={sink} interval={t.interval:g}s "
            f"on_delay={t.on_delay:g}s off_delay={t.off_delay:g}s heartbeat={t.heartbeat:g}s "
            f"credentials={self.tv.credentials}"
        )


_SECTIONS: dict[str, tuple[str, ...]] = {
    "tv": ("host", "credentials"),
    "sink": ("type", "host", "password"),
    "timing": ("interval", "on_delay", "off_delay", "heartbeat"),
}


def _check_keys(data: Mapping[str, Any], source: str) -> None:
    for section, values in data.items():
        if section not in _SECTIONS:
            raise ConfigError(f"{source}: unknown section [{section}]")
        if not isinstance(values, Mapping):
            raise ConfigError(f"{source}: [{section}] must be a table")
        for key in values:
            if key not in _SECTIONS[section]:
                raise ConfigError(f"{source}: unknown key {section}.{key}")


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"timing.{name} must be a number, got {value!r}")
    return float(value)


def _string(value: Any, name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string, got {value!r}")
    return value or None


def from_mapping(data: Mapping[str, Any], source: str = "config") -> Config:
    _check_keys(data, source)
    tv, sink, timing = (data.get(s, {}) for s in ("tv", "sink", "timing"))
    defaults = Config()
    creds = _string(tv.get("credentials"), "tv.credentials")
    return Config(
        tv=TVConfig(
            host=_string(tv.get("host"), "tv.host"),
            credentials=Path(creds).expanduser() if creds else defaults.tv.credentials,
        ),
        sink=SinkConfig(
            type=_string(sink.get("type"), "sink.type") or defaults.sink.type,
            host=_string(sink.get("host"), "sink.host"),
            password=_string(sink.get("password"), "sink.password"),
        ),
        timing=TimingConfig(
            **{k: _number(v, k) for k, v in timing.items()},
        ),
    )


def load(path: Optional[Path]) -> Config:
    """Load `path`, or the default path if None. A missing default file means built-in defaults."""
    explicit = path is not None
    path = path or DEFAULT_CONFIG_PATH
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if explicit:
            raise ConfigError(f"config file not found: {path}") from None
        return Config()
    except OSError as err:
        raise ConfigError(f"cannot read {path}: {err.strerror}") from None
    try:
        data = tomllib.loads(raw.decode())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as err:
        raise ConfigError(f"{path}: {err}") from None
    return from_mapping(data, str(path))


def apply_overrides(
    cfg: Config,
    *,
    tv_host: Optional[str] = None,
    credentials: Optional[Path] = None,
    sink: Optional[str] = None,
    sink_host: Optional[str] = None,
    interval: Optional[float] = None,
    on_delay: Optional[float] = None,
    off_delay: Optional[float] = None,
    heartbeat: Optional[float] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Config:
    """CLI flags (None = not given) and the password env var win over the file."""
    env = os.environ if env is None else env

    def pick(new, old):
        return old if new is None else new

    password = env.get(PASSWORD_ENV) or cfg.sink.password
    return replace(
        cfg,
        tv=replace(cfg.tv, host=pick(tv_host, cfg.tv.host),
                   credentials=pick(credentials, cfg.tv.credentials)),
        sink=replace(cfg.sink, type=pick(sink, cfg.sink.type),
                     host=pick(sink_host, cfg.sink.host), password=password),
        timing=replace(
            cfg.timing,
            interval=pick(interval, cfg.timing.interval),
            on_delay=pick(on_delay, cfg.timing.on_delay),
            off_delay=pick(off_delay, cfg.timing.off_delay),
            heartbeat=pick(heartbeat, cfg.timing.heartbeat),
        ),
    )


def validate(cfg: Config) -> Config:
    if cfg.sink.type not in SINK_TYPES:
        raise ConfigError(f"sink.type must be one of {', '.join(SINK_TYPES)}, got {cfg.sink.type!r}")
    if cfg.sink.type == "shelly" and not cfg.sink.host:
        raise ConfigError("sink.type = \"shelly\" needs sink.host (or --sink-host)")
    t = cfg.timing
    if t.interval <= 0:
        raise ConfigError("timing.interval must be > 0")
    for name in ("on_delay", "off_delay", "heartbeat"):
        if getattr(t, name) < 0:
            raise ConfigError(f"timing.{name} must be >= 0")
    return cfg
