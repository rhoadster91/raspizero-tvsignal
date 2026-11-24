"""Command line: pair / run / check / sink."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence

from . import __version__, config
from .config import Config, ConfigError
from .daemon import Daemon
from .model import AuthError, Follower, SignalSink, SinkError
from .sinks.log import LogSink

log = logging.getLogger("tvsignal")

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG = 2
EXIT_AUTH = 3  # re-pair needed; systemd must not restart-loop on this (RestartPreventExitStatus)
EXIT_INTERRUPTED = 130

REPAIR_HINT = "re-pair: sudo -u tvsignal /opt/tvsignal/venv/bin/tvsignal pair --config /etc/tvsignal/config.toml"


# --------------------------------------------------------------------------- helpers

def setup_logging(verbose: bool) -> None:
    # journald adds its own timestamps; add ours only when run by hand.
    fmt = "%(levelname)s %(message)s"
    if not os.environ.get("JOURNAL_STREAM"):
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt, stream=sys.stdout,
                        force=True)
    if not verbose:
        for name in ("httpx", "httpcore", "haphilipsjs"):
            logging.getLogger(name).setLevel(logging.WARNING)


def install_signal_handlers(daemon: Daemon) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, daemon.stop)


def load_config(args: argparse.Namespace) -> Config:
    cfg = config.load(args.config)
    cfg = config.apply_overrides(
        cfg,
        tv_host=args.tv_host,
        credentials=args.credentials,
        sink=getattr(args, "sink", None),
        sink_host=getattr(args, "sink_host", None),
        interval=getattr(args, "interval", None),
        on_delay=getattr(args, "on_delay", None),
        off_delay=getattr(args, "off_delay", None),
        heartbeat=getattr(args, "heartbeat", None),
    )
    return config.validate(cfg)


def make_sink(cfg: Config) -> SignalSink:
    if cfg.sink.type == "shelly":
        from .sinks.shelly import ShellySink

        return ShellySink(cfg.sink.host, cfg.sink.password)
    return LogSink()


def _word(on: Optional[bool]) -> str:
    return "unknown" if on is None else ("ON" if on else "OFF")


def _sink_status(sink: SignalSink, state: Optional[bool]) -> str:
    line = f"{sink.name}: {_word(state)}"
    apower = getattr(sink, "apower", None)
    if apower is not None:
        line += f", {apower:g} W"
    return line


def _open_tv(cfg: Config):
    from .sources.philips import PhilipsSource, load_credentials

    creds = load_credentials(cfg.tv.credentials)
    host = cfg.tv.host or creds.host
    return PhilipsSource(host, creds), replace(cfg, tv=replace(cfg.tv, host=host))


# --------------------------------------------------------------------------- commands

async def cmd_pair(args: argparse.Namespace) -> int:
    from .sources.philips import pair, save_credentials

    cfg = load_config(args)
    if not cfg.tv.host:
        raise ConfigError("pair needs the TV's address: --host <tv-ip> (or tv.host in the config)")

    async def read_pin() -> str:
        # input() blocks; run it in a thread so Ctrl-C exits cleanly.
        return await asyncio.to_thread(input, "Enter the PIN shown on the TV: ")

    creds = await pair(cfg.tv.host, read_pin)
    save_credentials(cfg.tv.credentials, creds)
    print(f"Paired. Credentials saved to {cfg.tv.credentials}")
    return EXIT_OK


async def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    source, cfg = _open_tv(cfg)
    log.info("tvsignal %s starting: %s", __version__, cfg.describe())
    t = cfg.timing
    daemon = Daemon(source, make_sink(cfg), Follower(on_delay=t.on_delay, off_delay=t.off_delay),
                    interval=t.interval, heartbeat=t.heartbeat)
    install_signal_handlers(daemon)
    await daemon.run()
    return EXIT_OK


async def cmd_check(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    source, cfg = _open_tv(cfg)
    print(cfg.describe())
    sink = make_sink(cfg)
    status = EXIT_OK
    try:
        obs = await source.observe()
        print(f"tv {cfg.tv.host}: reachable={obs.reachable} power={obs.powerstate} "
              f"screen={obs.screenstate} -> active={obs.active}")
        try:
            print(_sink_status(sink, await sink.get()))
        except SinkError as err:
            print(f"{sink.name}: ERROR {err}")
            status = EXIT_FAILURE
    finally:
        await source.aclose()
        await sink.aclose()
    return status


async def cmd_sink(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    sink = make_sink(cfg)
    try:
        if isinstance(sink, LogSink):
            print("sink.type is \"log\": there is no physical plug. "
                  "Use --sink shelly --sink-host <plug-ip> (or set [sink] in the config).")
            return EXIT_OK if args.action == "status" else EXIT_CONFIG
        if args.action in ("on", "off"):
            await sink.set(args.action == "on")
        print(_sink_status(sink, await sink.get()))
        return EXIT_OK
    except SinkError as err:
        print(f"{sink.name}: ERROR {err}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        await sink.aclose()


# --------------------------------------------------------------------------- argparse

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=None,
                        help=f"TOML config (default {config.DEFAULT_CONFIG_PATH}, if it exists)")
    common.add_argument("--credentials", type=Path, help="TV credentials file from pairing")
    common.add_argument("-v", "--verbose", action="store_true", help="debug logging, incl. libraries")

    tv = argparse.ArgumentParser(add_help=False)
    tv.add_argument("--tv-host", "--host", dest="tv_host", help="TV IP or hostname")

    sink = argparse.ArgumentParser(add_help=False)
    sink.add_argument("--sink", choices=config.SINK_TYPES, help="sink type")
    sink.add_argument("--sink-host", help="plug IP or hostname (shelly)")

    timing = argparse.ArgumentParser(add_help=False)
    timing.add_argument("--interval", type=float, help="poll interval, s (default 2)")
    timing.add_argument("--on-delay", type=float, help="s active before plug ON (default 0)")
    timing.add_argument("--off-delay", type=float, help="s continuously inactive before plug OFF (default 10)")
    timing.add_argument("--heartbeat", type=float, help="log an 'unchanged' line every N s (default 600)")

    p = argparse.ArgumentParser(prog="tvsignal", description=__doc__)
    p.add_argument("--version", action="version", version=f"tvsignal {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pair", parents=[common, tv], help="pair with the TV (one-off; shows a PIN on the TV)")
    sub.add_parser("run", parents=[common, tv, sink, timing], help="run the daemon")
    sub.add_parser("check", parents=[common, tv, sink], help="one TV observation + sink state, then exit")
    sp = sub.add_parser("sink", parents=[common, sink], help="read or switch the plug by hand")
    sp.add_argument("action", choices=("status", "on", "off"))
    sp.set_defaults(tv_host=None)
    return p


COMMANDS = {"pair": cmd_pair, "run": cmd_run, "check": cmd_check, "sink": cmd_sink}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        return asyncio.run(COMMANDS[args.cmd](args))
    except ConfigError as err:
        log.error("config error: %s", err)
        return EXIT_CONFIG
    except AuthError as err:
        log.error("%s", err)
        log.error("%s", REPAIR_HINT)
        return EXIT_AUTH
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception as err:  # PairError and anything unexpected
        from .sources.philips import PairError

        if isinstance(err, PairError):
            log.error("%s", err)
            return EXIT_FAILURE
        raise


if __name__ == "__main__":
    sys.exit(main())
