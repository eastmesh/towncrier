#!/usr/bin/env bash
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRANCH=main
VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv}"
PYTHON="${PYTHON:-python3}"
RESTART=auto
SERVICE=towncrier

usage() {
  cat <<'EOF'
Usage: ./update.sh [options]

Safely update an existing Git-based Towncrier installation, install Python
package dependencies into its virtual environment, and verify the CLI.

Options:
  --venv PATH       Virtual environment to create or update.
  --branch NAME     Git branch to update from (default: main).
  --restart         Restart the towncrier systemd service after updating.
  --no-restart      Do not restart the systemd service.
  --service NAME    systemd service name for --restart (default: towncrier).
  -h, --help        Show this help.

The script refuses to run when the repository has uncommitted changes or is
not checked out on the selected branch. It performs only a fast-forward update
from origin/BRANCH and preserves the Towncrier database.
EOF
}

while (($#)); do
  case "$1" in
    --venv) VENV_DIR=${2:?--venv requires a path}; shift ;;
    --branch) BRANCH=${2:?--branch requires a name}; shift ;;
    --restart) RESTART=yes ;;
    --no-restart) RESTART=no ;;
    --service) SERVICE=${2:?--service requires a name}; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[[ -d "$REPO_DIR/.git" ]] || { echo "Run update.sh from a Git checkout of Towncrier." >&2; exit 1; }
[[ -z "$(git -C "$REPO_DIR" status --porcelain)" ]] || { echo "Refusing to update: the Towncrier checkout has uncommitted changes." >&2; exit 1; }
CURRENT_BRANCH=$(git -C "$REPO_DIR" branch --show-current)
[[ "$CURRENT_BRANCH" == "$BRANCH" ]] || { echo "Refusing to update: checked out on '$CURRENT_BRANCH', expected '$BRANCH'." >&2; exit 1; }
command -v "$PYTHON" >/dev/null || { echo "Python interpreter '$PYTHON' was not found." >&2; exit 1; }
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ is required"'
"$PYTHON" -m venv --help >/dev/null || { echo "Python venv support is required." >&2; exit 1; }

git -C "$REPO_DIR" fetch origin "$BRANCH"
git -C "$REPO_DIR" merge --ff-only "origin/$BRANCH"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV_DIR"
fi
BUILD_DIR_CREATED=0
[[ -e "$REPO_DIR/build" ]] || BUILD_DIR_CREATED=1
cleanup_build() {
  if ((BUILD_DIR_CREATED)); then rm -rf -- "$REPO_DIR/build"; fi
}
trap cleanup_build EXIT
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_DIR/bin/python" -m pip install --upgrade "$REPO_DIR"
"$VENV_DIR/bin/towncrier" --help >/dev/null

if [[ "$RESTART" == auto ]]; then
  if systemctl cat "$SERVICE" >/dev/null 2>&1; then RESTART=yes; else RESTART=no; fi
fi
if [[ "$RESTART" == yes ]]; then
  if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    systemctl restart "$SERVICE"
  else
    command -v sudo >/dev/null || { echo "sudo is required to restart $SERVICE." >&2; exit 1; }
    sudo systemctl restart "$SERVICE"
  fi
  systemctl is-active --quiet "$SERVICE" || { echo "$SERVICE did not become active after restart." >&2; exit 1; }
fi

printf 'Towncrier updated on branch %s using %s.\n' "$BRANCH" "$VENV_DIR"
