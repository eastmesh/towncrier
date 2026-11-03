#!/usr/bin/env bash
set -euo pipefail
[[ "$(uname -s)" == "Linux" ]] || { echo "Towncrier requires Linux" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ is required"'
python3 -m venv --help >/dev/null || { echo "python3-venv is required" >&2; exit 1; }
command -v pip3 >/dev/null || { echo "pip3 is required" >&2; exit 1; }
PREFIX=${PREFIX:-/opt/towncrier}; SERVICE_USER=${SERVICE_USER:-towncrier}; SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
sudo mkdir -p "$PREFIX"; sudo cp -a "$SCRIPT_DIR"/. "$PREFIX"/
sudo python3 -m venv "$PREFIX/.venv"; sudo "$PREFIX/.venv/bin/python" -m pip install --upgrade pip; sudo "$PREFIX/.venv/bin/pip" install "$PREFIX"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then sudo useradd --system --home-dir "$PREFIX" --shell /usr/sbin/nologin "$SERVICE_USER"; fi
sudo mkdir -p /var/lib/towncrier; sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$PREFIX" /var/lib/towncrier; sudo install -m 0644 "$PREFIX/systemd/towncrier.service" /etc/systemd/system/towncrier.service; sudo systemctl daemon-reload
printf '\nInstalled. Configure the local web UI, then run: sudo systemctl enable --now towncrier\n'
