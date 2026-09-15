#!/usr/bin/env bash
set -Euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
RESTART_DELAY="${RESTART_DELAY:-2}"

cd "$ROOT"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3 is required." >&2
    exit 1
fi

while true; do
    if "$PYTHON" "$ROOT/app/main.py"; then
        code=0
    else
        code=$?
    fi
    echo "RG Nodes Protect exited with code ${code}; restarting in ${RESTART_DELAY}s..." >&2
    sleep "$RESTART_DELAY"
done
