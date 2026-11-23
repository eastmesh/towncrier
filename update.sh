#!/usr/bin/env bash
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRANCH=main
PREFIX=${PREFIX:-/opt/towncrier} # Same installation directory as install.sh.
CUSTOM_VENV=${VENV_DIR:-}
PYTHON=${PYTHON:-python3}
RESTART=auto
SERVICE=towncrier

fail() { printf 'Update stopped: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: ./update.sh [options]

Run from the Git clone you used for installation, as your usual user.
By default, update /opt/towncrier/.venv and restart and check towncrier.
Set PREFIX to the same value used with install.sh for a custom installation.

Options:
  --venv PATH       Use a different virtual environment (also VENV_DIR).
                    Creates it if missing; does not restart by default.
  --branch NAME     Update from origin/NAME (default: main).
  --restart         Restart and verify the systemd service after installing.
  --no-restart      Install now but leave the running service alone.
  --service NAME    systemd unit to restart (default: towncrier).
  -h, --help        Show this help.

The selected branch must already be checked out and have no local changes.
Only a fast-forward to the fetched branch is allowed. This script does not
change the systemd unit or the database.
EOF
}

while (($#)); do
  case "$1" in
    --venv) CUSTOM_VENV=${2:?--venv requires a path}; shift ;;
    --branch) BRANCH=${2:?--branch requires a name}; shift ;;
    --restart) RESTART=yes ;;
    --no-restart) RESTART=no ;;
    --service) SERVICE=${2:?--service requires a name}; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# Git runs as the clone owner, not root. Refuse edits rather than stashing or
# overwriting them. A .git file is valid too (for example, in a Git worktree).
[[ -e "$REPO_DIR/.git" ]] || fail 'Run update.sh from a Git checkout of Towncrier.'
STATUS=$(git -C "$REPO_DIR" status --porcelain)
[[ -z "$STATUS" ]] || fail 'The Towncrier checkout has uncommitted changes.'
CURRENT_BRANCH=$(git -C "$REPO_DIR" branch --show-current)
[[ "$CURRENT_BRANCH" == "$BRANCH" ]] || fail "Checked out on '$CURRENT_BRANCH', expected '$BRANCH'."

VENV_DIR=${CUSTOM_VENV:-$PREFIX/.venv}
# A relative override is relative to the directory you ran the command from.
[[ "$VENV_DIR" == /* ]] || VENV_DIR="$PWD/$VENV_DIR"
if [[ "$RESTART" == auto ]]; then
  if [[ -n "$CUSTOM_VENV" ]]; then RESTART=no; else RESTART=yes; fi
fi
if [[ -z "$CUSTOM_VENV" ]]; then
  [[ -f "$VENV_DIR/pyvenv.cfg" && -x "$VENV_DIR/bin/python" ]] ||
    fail "Installed virtual environment not found at $VENV_DIR. Run install.sh first or set PREFIX to your installation directory."
fi

# Only the installed environment and service may need administrator access.
INSTALL_AS=()
SERVICE_AS=()
if ((EUID != 0)); then
  if [[ -z "$CUSTOM_VENV" && ! -w "$VENV_DIR" ]]; then INSTALL_AS=(sudo); fi
  if [[ "$RESTART" == yes ]]; then SERVICE_AS=(sudo); fi
  if ((${#INSTALL_AS[@]} || ${#SERVICE_AS[@]})); then
    command -v sudo >/dev/null || fail 'sudo is required to update the system installation or restart the service.'
  fi
fi
if [[ "$RESTART" == yes ]]; then
  command -v systemctl >/dev/null || fail 'systemctl is required to restart the service; use --no-restart to skip it.'
fi

# FETCH_HEAD is the exact branch tip just fetched, even when the clone's
# remote-tracking configuration does not include that branch.
git -C "$REPO_DIR" fetch origin "refs/heads/$BRANCH"
TARGET=$(git -C "$REPO_DIR" rev-parse FETCH_HEAD)
git -C "$REPO_DIR" merge-base --is-ancestor HEAD "$TARGET" ||
  fail "Cannot fast-forward to origin/$BRANCH; resolve the local commits before retrying."
git -C "$REPO_DIR" merge --ff-only "$TARGET"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV_DIR"
fi
[[ -f "$VENV_DIR/pyvenv.cfg" ]] || fail "Not a Python virtual environment: $VENV_DIR"

# Build in a disposable directory, so pip cannot leave build files in the
# clone. Build without sudo; only install into the protected venv with sudo.
STAGE=$(mktemp -d)
trap 'rm -rf -- "$STAGE"' EXIT
mkdir "$STAGE/source" "$STAGE/wheels"
git -C "$REPO_DIR" archive "$TARGET" | tar -x -C "$STAGE/source"
"$VENV_DIR/bin/python" -m pip wheel --no-deps --wheel-dir "$STAGE/wheels" "$STAGE/source"
# Releases can share a version number. Force installation of this wheel so
# pip never leaves an older copy of Towncrier in the service's environment.
"${INSTALL_AS[@]}" "$VENV_DIR/bin/python" -m pip install --upgrade --force-reinstall "$STAGE/wheels/"*.whl
"$VENV_DIR/bin/towncrier" --help >/dev/null

if [[ "$RESTART" == yes ]]; then
  "${SERVICE_AS[@]}" systemctl restart -- "$SERVICE" ||
    fail "Could not restart $SERVICE. Check: sudo journalctl -u $SERVICE -n 50"
  systemctl is-active --quiet -- "$SERVICE" ||
    fail "$SERVICE did not stay active. Check: sudo journalctl -u $SERVICE -n 50"
  printf 'Service %s is active.\n' "$SERVICE"
fi

printf 'Towncrier updated on branch %s using %s.\n' "$BRANCH" "$VENV_DIR"
