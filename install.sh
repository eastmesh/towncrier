#!/usr/bin/env bash
set -euo pipefail
[[ "$(uname -s)" == "Linux" ]] || { echo "Towncrier requires Linux" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd/systemctl is required" >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ is required"'
python3 -m venv --help >/dev/null || { echo "python3-venv is required" >&2; exit 1; }
command -v pip3 >/dev/null || { echo "pip3 is required" >&2; exit 1; }
if [[ ${EUID:-$(id -u)} -eq 0 ]]; then SUDO=""; else command -v sudo >/dev/null || { echo "sudo is required" >&2; exit 1; }; SUDO=sudo; fi
PREFIX=${PREFIX:-/opt/towncrier}; SERVICE_USER=${SERVICE_USER:-towncrier}; SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
[[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "SERVICE_USER must be a simple Linux username" >&2; exit 1; }
$SUDO mkdir -p "$PREFIX"; $SUDO cp -a "$SCRIPT_DIR"/. "$PREFIX"/
$SUDO python3 -m venv "$PREFIX/.venv"; $SUDO "$PREFIX/.venv/bin/python" -m pip install --upgrade pip; $SUDO "$PREFIX/.venv/bin/pip" install "$PREFIX"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then $SUDO useradd --system --home-dir "$PREFIX" --shell /usr/sbin/nologin "$SERVICE_USER"; fi
if getent group dialout >/dev/null 2>&1; then $SUDO usermod -aG dialout "$SERVICE_USER"; fi
$SUDO mkdir -p /var/lib/towncrier; $SUDO chown -R "$SERVICE_USER:$SERVICE_USER" /var/lib/towncrier
$SUDO sed -e "s|^User=.*|User=$SERVICE_USER|" -e "s|^WorkingDirectory=.*|WorkingDirectory=$PREFIX|" -e "s|^ExecStart=.*|ExecStart=$PREFIX/.venv/bin/towncrier --db /var/lib/towncrier/towncrier.db --host 0.0.0.0 --port 8080|" "$PREFIX/systemd/towncrier.service" > /tmp/towncrier.service
$SUDO install -m 0644 /tmp/towncrier.service /etc/systemd/system/towncrier.service
rm -f /tmp/towncrier.service
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now towncrier
printf '\nTowncrier is installed and running. Open http://127.0.0.1:8080/ in a browser.\n'
