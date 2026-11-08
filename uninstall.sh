#!/usr/bin/env bash
set -euo pipefail

PREFIX=${PREFIX:-/opt/towncrier}
DATA_DIR=/var/lib/towncrier
[[ "$PREFIX" == /* && "$PREFIX" != "/" && "$PREFIX" != "/var/lib" && "$PREFIX" != "$DATA_DIR" ]] || { echo "PREFIX must be a safe absolute application path" >&2; exit 2; }
PURGE_DATA=0

usage() {
  cat <<'EOF'
Usage: sudo ./uninstall.sh [--purge-data]

Stops and removes the Towncrier systemd service and application directory.
The schedule database in /var/lib/towncrier is preserved by default.

  --purge-data  Also permanently remove /var/lib/towncrier after an
                interactive DELETE confirmation.
  -h, --help    Show this help.

The towncrier Linux service account is retained.
Set PREFIX to remove an installation made with a custom prefix.
EOF
}

while (($#)); do
  case "$1" in
    --purge-data) PURGE_DATA=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[[ "$(uname -s)" == "Linux" ]] || { echo "Towncrier uninstall requires Linux" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd/systemctl is required" >&2; exit 1; }
if [[ ${EUID:-$(id -u)} -eq 0 ]]; then SUDO=""; else command -v sudo >/dev/null || { echo "sudo is required" >&2; exit 1; }; SUDO=sudo; fi

if ((PURGE_DATA)); then
  [[ -t 0 ]] || { echo "--purge-data requires an interactive terminal; database left untouched" >&2; exit 1; }
  printf 'This permanently deletes all Towncrier schedules and settings in %s.\nType DELETE to continue: ' "$DATA_DIR"
  read -r confirmation
  [[ "$confirmation" == "DELETE" ]] || { echo "Confirmation did not match; database left untouched"; exit 1; }
fi

$SUDO systemctl disable --now towncrier || true
$SUDO rm -f /etc/systemd/system/towncrier.service
$SUDO systemctl daemon-reload
$SUDO systemctl reset-failed towncrier || true
$SUDO rm -rf -- "$PREFIX"
if ((PURGE_DATA)); then
  $SUDO rm -rf -- "$DATA_DIR"
else
  printf 'Towncrier data was preserved at %s.\n' "$DATA_DIR"
fi
printf 'Towncrier service and application were removed. The towncrier service account was retained.\n'
