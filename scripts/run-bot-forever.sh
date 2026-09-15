#!/usr/bin/env bash
set -Euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
RESTART_DELAY="${RESTART_DELAY:-3}"
cd "$ROOT"

command -v "$PYTHON" >/dev/null 2>&1 || { echo "Python 3 is required." >&2; exit 1; }

while true; do
  if "$PYTHON" "$ROOT/vm.py"; then
    code=0
  else
    code=$?
  fi
  echo "RGNODES VPS bot exited with code ${code}; restarting in ${RESTART_DELAY}s..." >&2
  sleep "$RESTART_DELAY"
done
