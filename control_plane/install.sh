#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/easy-enclave}"
VENV_DIR="${REPO_DIR}/control_plane/.venv"
ENV_DIR="/etc/easy-enclave/control-plane"
SERVICE_UNIT_SRC="${REPO_DIR}/control_plane/systemd/ee-control-plane@.service"
SERVICE_UNIT_DST="/etc/systemd/system/ee-control-plane@.service"
CADDYFILE_SRC="${REPO_DIR}/control_plane/Caddyfile"
CADDYFILE_DST="/etc/caddy/Caddyfile"

if [ ! -d "$REPO_DIR" ]; then
  echo "Repo not found at $REPO_DIR" >&2
  exit 1
fi

sudo mkdir -p "$ENV_DIR"

if ! command -v caddy >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y caddy
fi

sudo apt-get update
sudo apt-get install -y python3-venv

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/control_plane/requirements.txt"

sudo install -m 0644 "$SERVICE_UNIT_SRC" "$SERVICE_UNIT_DST"
sudo systemctl daemon-reload

if [ -f "$CADDYFILE_SRC" ]; then
  sudo install -m 0644 "$CADDYFILE_SRC" "$CADDYFILE_DST"
  sudo systemctl reload caddy || sudo systemctl restart caddy
fi

echo "Control plane base install complete."
