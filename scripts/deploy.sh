#!/usr/bin/env bash
# Copy this checkout to the Pi and run deploy/install.sh there. Run from the Mac.
# Target: $TVSIGNAL_PI (user@host), or set it in scripts/deploy.env (gitignored).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$ROOT/scripts/deploy.env" ]] && source "$ROOT/scripts/deploy.env"
: "${TVSIGNAL_PI:?set TVSIGNAL_PI=user@host (env or scripts/deploy.env)}"
REMOTE_DIR="${TVSIGNAL_REMOTE_DIR:-tvsignal-src}"

rsync -az --delete \
    --exclude .git --exclude .venv --exclude '__pycache__' --exclude '*.egg-info' \
    --exclude .pytest_cache --exclude build --exclude dist \
    --exclude 'scripts/deploy.env' --exclude '/config.toml' --exclude 'credentials*.json' \
    "$ROOT/" "$TVSIGNAL_PI:$REMOTE_DIR/"

ssh -t "$TVSIGNAL_PI" "sudo bash \"\$HOME/$REMOTE_DIR/deploy/install.sh\""
