# raspizero-tvsignal

A small always-on service for a Raspberry Pi Zero 2 W. It switches a smart plug on and
off to follow a TV's power state, so powered speakers without auto-wake come on with the
TV and go off after it.

- **Source:** Philips Android TV (2016+), local JointSpace API v6 over HTTPS (port 1926),
  digest auth after a one-off PIN pairing. Uses [`ha-philipsjs`](https://pypi.org/project/ha-philipsjs/)
  (the Home Assistant library), pinned to the version tested against a real TV.
- **Sink:** Shelly Gen2+ plug (e.g. Shelly Plus Plug UK) over its local RPC API. No cloud.
  Or a **log-only** sink that just logs what it would do.

## How it decides

The TV is polled every 2 s. It counts as **active** only if it's reachable, reports power
`On` **and** screen `On` (or doesn't report a screen state at all):

| TV reports | Typically seen | Active? |
|---|---|---|
| `On` + screen `On` | watching | yes |
| `On` + screen `Off` | ~2 s while waking, before the picture | no |
| `Standby` + screen `On` | briefly while waking | no |
| `Standby` + screen `Off` | just after power-off | no |
| `StandbyKeep` + screen `Off` | background network wake (no picture) | no |
| unreachable | network asleep | no |

Debounce: the plug turns **on at the first active reading** (`on_delay = 0`) and turns
**off only after `off_delay` seconds (default 10) of continuous inactivity**. Any active
reading restarts that timer, so flicker (On-Off-On-Off-On) or one failed poll never
cycles the speakers. Requiring the screen to be on filters out background wakes; an
overnight log-only run saw dozens of them and no false ON.

Other rules:
- At start-up the plug's state is read once to seed the logic. The plug is **not**
  re-asserted periodically: if you switch it by hand, the next TV state *change* wins.
- If switching fails, the error is logged once per failure streak and retried every poll.
- SIGTERM/SIGINT: exits 0 without touching the plug.
- Logs go to stdout (journald): state changes, plus a heartbeat every 10 min. Nothing is
  written to disk in steady state.

## Commands

```
tvsignal pair  --host <tv-ip>          # one-off; type the PIN the TV shows
tvsignal run   [--config PATH]         # the daemon
tvsignal check [--config PATH]         # one TV reading + plug state, then exit
tvsignal sink  status|on|off           # read or switch the plug by hand
```

Every config key can be overridden by a flag (`--tv-host`, `--credentials`, `--sink`,
`--sink-host`, `--interval`, `--on-delay`, `--off-delay`, `--heartbeat`, `-v`). See
`tvsignal <command> --help`. The plug password is read only from the config file or the
`TVSIGNAL_SINK_PASSWORD` environment variable, never from a flag.

Exit codes: `0` ok, `1` failure (e.g. plug unreachable in `check`/`sink`, pairing
refused), `2` config error, `3` TV credentials missing or rejected (re-pair), `130`
interrupted. systemd doesn't restart on `2` or `3`.

## Configuration

`/etc/tvsignal/config.toml` (see [`deploy/config.example.toml`](deploy/config.example.toml)):

```toml
[tv]
host = "<tv-ip>"
credentials = "/var/lib/tvsignal/credentials.json"

[sink]
type = "log"            # "log" during the trial, then "shelly"
host = "<plug-ip>"      # shelly only
# password = ""         # only if the plug has a password

[timing]
interval = 2
on_delay = 0
off_delay = 10
heartbeat = 600
```

Without a config file (e.g. on a Mac) the defaults above apply, with credentials in
`~/.config/tvsignal/credentials.json`. If `tv.host` is unset, the host saved at pairing
is used. Give the TV, the plug and the Pi reserved addresses in your router.

## Development (macOS or Linux)

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q

.venv/bin/tvsignal pair --host <tv-ip>   # TV on; type the PIN it shows
.venv/bin/tvsignal run                   # log-only by default; Ctrl-C to stop
```

On macOS, allow your terminal app to "find devices on your local network" when asked,
or every request fails as unreachable. The Pi runs Python 3.11, so don't use newer
language features.

## Setting up the Shelly plug

1. Add it to your WiFi with the Shelly app or its web UI, and reserve its IP.
2. Set its **power-on default to off**, so a power cut leaves the speakers off until the
   TV is on.
3. Optional: set a password (Settings → Authentication). Then put it in the config.
4. Test it on its own before letting the daemon switch it:
   ```sh
   tvsignal sink status --sink shelly --sink-host <plug-ip>
   tvsignal sink on     --sink shelly --sink-host <plug-ip>
   tvsignal sink off    --sink shelly --sink-host <plug-ip>
   ```
   `status` prints the switch state and the power draw in W.

## Deploying to the Pi

Assumes Raspberry Pi OS Bookworm (Debian 12, Python 3.11) and SSH access with sudo.
From the Mac:

```sh
cp scripts/deploy.env.example scripts/deploy.env   # set TVSIGNAL_PI=user@host (gitignored)
scripts/deploy.sh
```

This copies the checkout to the Pi with `rsync` and runs [`deploy/install.sh`](deploy/install.sh)
there. The script is idempotent: it creates the `tvsignal` system user,
`/opt/tvsignal/venv`, `/var/lib/tvsignal` (0700) and `/etc/tvsignal`; installs the
package (binary wheels only for `cryptography`/`cffi`, so nothing compiles on the Pi);
installs the example config only if none exists; installs and enables
[`deploy/tvsignal.service`](deploy/tvsignal.service). The only OS package it may add is
`python3-venv`, and only if `python3 -m venv` doesn't work.

Then, once, with the TV on:

```sh
sudo nano /etc/tvsignal/config.toml           # set tv.host (and the plug, later)
sudo -u tvsignal /opt/tvsignal/venv/bin/tvsignal pair --config /etc/tvsignal/config.toml
sudo systemctl restart tvsignal
journalctl -u tvsignal -f
```

Check: `systemctl is-enabled tvsignal`, `systemctl is-active tvsignal`, and that it
survives a reboot. The first log line shows the effective config.

Memory use (RSS) on the Pi Zero 2 W: _to be measured_ (`systemctl status tvsignal`).

### Rollout

1. Run with `type = "log"` for about a week. Check the journal for `WOULD switch plug ON`
   at times nobody was watching (especially around TV software updates).
2. Test the plug with `tvsignal sink`, then set `type = "shelly"` and restart.

### Optional: less SD-card wear

The service writes nothing in steady state, but journald may persist logs to the SD card.
Setting `Storage=volatile` in `/etc/systemd/journald.conf` keeps logs in RAM only (lost
on reboot). That's an OS-wide change, so the install script doesn't do it.

## Layout

```
src/tvsignal/
  model.py            Observation + active rule, Follower (debounce), Source/Sink protocols
  daemon.py           poll loop, logging, failure handling (no TV/plug specifics)
  config.py           TOML + CLI overrides + validation
  cli.py              pair / run / check / sink
  sources/philips.py  Philips JointSpace source, pairing, credentials
  sinks/shelly.py     Shelly Gen2+ RPC sink
  sinks/log.py        log-only sink
tests/                unit tests, fake HTTP(S) servers for the plug and the TV
deploy/               systemd unit, example config, install.sh (runs on the Pi)
scripts/deploy.sh     rsync + install from the dev machine
```
