#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
UPDATE="$ROOT/update.sh"

[[ -x "$UPDATE" ]]
bash -n "$UPDATE"

HELP=$($UPDATE --help)
grep -Fq 'Usage:' <<<"$HELP"
grep -Fq -- '--venv PATH' <<<"$HELP"
grep -Fq -- '--restart' <<<"$HELP"
grep -Fq -- '--no-restart' <<<"$HELP"
