#!/usr/bin/env bash
# Install or update tvsignal on the Pi. Run as root from the repo checkout:
#   sudo bash deploy/install.sh
# Idempotent: safe to re-run. Never overwrites an existing /etc/tvsignal/config.toml.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME=tvsignal
PREFIX=/opt/tvsignal
VENV="$PREFIX/venv"
ETC=/etc/tvsignal
STATE=/var/lib/tvsignal
UNIT=/etc/systemd/system/tvsignal.service

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }

say() { printf '==> %s\n' "$*"; }

if ! id -u "$USER_NAME" >/dev/null 2>&1; then
    say "creating system user $USER_NAME"
    useradd --system --no-create-home --home-dir "$STATE" --shell /usr/sbin/nologin "$USER_NAME"
fi

# python3-venv is the only OS package this script may add.
probe="$(mktemp -d)"
if ! python3 -m venv "$probe/v" >/dev/null 2>&1; then
    say "python3 -m venv doesn't work; installing python3-venv"
    apt-get update -qq
    apt-get install -y python3-venv
fi
rm -rf "$probe"

say "directories"
install -d -m 0755 -o root -g root "$PREFIX" "$ETC"
install -d -m 0700 -o "$USER_NAME" -g "$USER_NAME" "$STATE"

say "venv at $VENV"
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
# Fail loudly instead of compiling cryptography/cffi from source on a Pi Zero.
"$VENV/bin/pip" install --quiet --prefer-binary --only-binary=cryptography,cffi "$SRC"
# Same version number on every deploy: force the package itself (not its deps) to reinstall.
"$VENV/bin/pip" install --quiet --no-deps --force-reinstall "$SRC"

if [[ ! -e "$ETC/config.toml" ]]; then
    say "installing example config to $ETC/config.toml (edit it!)"
    install -m 0640 -o root -g "$USER_NAME" "$SRC/deploy/config.example.toml" "$ETC/config.toml"
else
    say "keeping existing $ETC/config.toml"
fi

say "systemd unit"
install -m 0644 -o root -g root "$SRC/deploy/tvsignal.service" "$UNIT"
systemctl daemon-reload
systemctl enable tvsignal.service >/dev/null

if [[ -e "$STATE/credentials.json" ]]; then
    say "restarting tvsignal"
    systemctl restart tvsignal.service
    systemctl --no-pager --lines=5 status tvsignal.service || true
else
    systemctl stop tvsignal.service 2>/dev/null || true
    say "not paired yet. With the TV on, run:"
    echo "    sudo -u $USER_NAME $VENV/bin/tvsignal pair --config $ETC/config.toml"
    echo "    sudo systemctl restart tvsignal"
fi
