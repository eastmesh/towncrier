#!/usr/bin/env bash
set -euo pipefail

# Real Git clones and pip installation; never call the host's service manager.
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
unset VENV_DIR
export PREFIX="$TMP/installed towncrier" SERVICE_LOG="$TMP/service.log"
PYTHON=${PYTHON:-python3}
mkdir "$TMP/bin"
export PATH="$TMP/bin:$PATH"
export GIT_AUTHOR_NAME=Test GIT_AUTHOR_EMAIL=test@example.invalid
export GIT_COMMITTER_NAME=$GIT_AUTHOR_NAME GIT_COMMITTER_EMAIL=$GIT_AUTHOR_EMAIL

cat > "$TMP/bin/sudo" <<'EOF'
#!/usr/bin/env bash
exec "$@"
EOF
cat > "$TMP/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SERVICE_LOG"
case "$*" in
  'restart -- towncrier') test -x "$PREFIX/.venv/bin/towncrier" ;;
  'is-active --quiet -- towncrier') test "${INACTIVE:-0}" = 0 ;;
  *) exit 97 ;;
esac
EOF
chmod +x "$TMP/bin/"*

git init -q -b main "$TMP/upstream"
git -C "$ROOT" archive HEAD | tar -x -C "$TMP/upstream"
cp "$ROOT/update.sh" "$TMP/upstream/update.sh"
git -C "$TMP/upstream" add .
git -C "$TMP/upstream" commit -qm base
git clone -q "$TMP/upstream" "$TMP/clone"
# The app version stays the same; verify that new files still reach the venv.
printf 'MARKER = "from fetched commit"\n' > "$TMP/upstream/towncrier/update_test_marker.py"
git -C "$TMP/upstream" add towncrier/update_test_marker.py
git -C "$TMP/upstream" commit -qm update
"$PYTHON" -m venv "$PREFIX/.venv"
"$PREFIX/.venv/bin/python" -m pip install "$TMP/clone"
# Remove the installer build output from the disposable clone, not the real repo.
rm -rf -- "$TMP/clone/build"

cd "$TMP"
"$TMP/clone/update.sh"
[[ $(git -C "$TMP/clone" rev-parse HEAD) == "$(git -C "$TMP/upstream" rev-parse HEAD)" ]]
"$PREFIX/.venv/bin/python" -c 'import meshcore; from towncrier.update_test_marker import MARKER; assert MARKER == "from fetched commit"'
[[ ! -e "$TMP/clone/.venv" ]]
[[ ! -e "$TMP/clone/build" ]]
[[ $(<"$SERVICE_LOG") == $'restart -- towncrier\nis-active --quiet -- towncrier' ]]

: > "$SERVICE_LOG"
"$TMP/clone/update.sh" --no-restart
[[ ! -s "$SERVICE_LOG" ]]
if INACTIVE=1 "$TMP/clone/update.sh" > "$TMP/error" 2>&1; then exit 1; fi
grep -q 'did not stay active' "$TMP/error"

# Neither a dirty checkout nor diverged history should touch the service.
: > "$SERVICE_LOG"
touch "$TMP/clone/local-notes"
if "$TMP/clone/update.sh" > "$TMP/error" 2>&1; then exit 1; fi
grep -q 'uncommitted changes' "$TMP/error"
rm "$TMP/clone/local-notes"
git -C "$TMP/clone" commit --allow-empty -qm local
git -C "$TMP/upstream" commit --allow-empty -qm remote
if "$TMP/clone/update.sh" > "$TMP/error" 2>&1; then exit 1; fi
grep -qi 'fast-forward' "$TMP/error"
[[ ! -s "$SERVICE_LOG" ]]
printf 'Updater integration test passed.\n'
