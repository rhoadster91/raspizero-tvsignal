# AGENTS.md

Guidance for coding agents working in this repo. `CLAUDE.md` just imports this file.

## What this is

`tvsignal`: an asyncio Python daemon for a Raspberry Pi Zero 2 W. It polls a Philips
Android TV (JointSpace API v6, HTTPS :1926, via `ha-philipsjs`) and switches a Shelly
Gen2+ smart plug (local RPC) to follow it, or only logs what it would do (`log` sink).
See README.md for user-facing docs.

```
SignalSource --Observation--> Daemon (Follower debounce, logging) --bool--> SignalSink
(sources/philips.py)          (daemon.py, model.py)                     (sinks/shelly.py, sinks/log.py)
```

- `model.py`: `Observation.active`, `Follower`, the `SignalSource`/`SignalSink` protocols,
  `SinkError`, `AuthError`. Pure, clock injected.
- `daemon.py` knows nothing about Philips or Shelly. Keep it that way.
- `cli.py` wires config → source/sink → daemon and maps errors to exit codes
  (2 config, 3 re-pair; the systemd unit has `RestartPreventExitStatus=2 3`).
- Sinks log their own successful switch; the daemon logs failures (once per streak) and recovery.

## Verified behaviour: do not change without a reason

These were verified against the real TV (port of an earlier proof of concept). Tests in
`tests/test_model.py` pin them.

- Active = `reachable and powerstate == "On" and screenstate in ("On", None)`.
- ON at the first active reading (`on_delay = 0`); OFF only after `off_delay` (10 s)
  of *continuous* inactivity; any active reading resets the timer. Poll every 2 s.
- Seed the plug state from `sink.get()` at start-up only; never re-assert periodically.
- Unreachable TV = inactive. `AutenticationFailure` (a subclass of `GeneralFailure`, so
  catch it first) means re-pair and exits 3.
- SIGTERM/SIGINT: close clients, exit 0, never touch the plug.
- stdout logging only, no files (SD-card wear). Library loggers at WARNING unless `-v`.

Known status: the Philips source runs against the real TV. The Shelly sink is tested only
against a fake server (`tests/test_shelly.py`, incl. SHA-256 digest auth). It has not yet
run against real hardware.

## Constraints

- Target is **Python 3.11** (Pi OS Bookworm). No 3.12+ syntax or stdlib APIs.
- Keep `ha-philipsjs==3.3.0` pinned: it's the version tested against the TV.
- Keep dependencies minimal (`ha-philipsjs`, `httpx`). `cryptography` must install from a
  binary wheel on aarch64.
- Don't add OS-level changes to `deploy/install.sh` beyond `python3-venv`.
- `deploy/install.sh` must stay idempotent and never overwrite `/etc/tvsignal/config.toml`.

## Public repo: keep local details out

This repository is public. Never commit:

- real IP addresses, hostnames (incl. `.local` mDNS names), SSH users, MAC addresses,
  device serials, room names, router brand or exact TV model numbers;
- credentials, plug passwords, or `credentials.json`.

Use placeholders in docs (`<tv-ip>`, `<plug-ip>`, `user@host`) and RFC 5737 addresses
(`192.0.2.x`) in examples and tests that need an address-shaped value; tests that open
sockets use `127.0.0.1`. Real values live only in untracked files: `/etc/tvsignal/config.toml`
on the Pi, a local `config.toml`, `scripts/deploy.env` (all covered by `.gitignore`).
When pasting logs into issues, commits or docs, redact addresses. Before committing, run:

```sh
git grep -nE '([0-9]{1,3}\.){3}[0-9]{1,3}' -- ':!*.lock' | grep -vE '192\.0\.2\.|127\.0\.0\.1|0\.0\.0\.0'
git grep -nE '\.local\b' -- ':!AGENTS.md' ':!.gitignore'
```

Both should print nothing (version numbers like `0.1.0` don't match the first pattern).

## Commands

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q                       # all tests; test_philips binds 127.0.0.1:1926
.venv/bin/tvsignal run --sink log         # against the real TV, log-only
.venv/bin/tvsignal sink status --sink shelly --sink-host <plug-ip>
scripts/deploy.sh                         # needs scripts/deploy.env (TVSIGNAL_PI=user@host)
```
