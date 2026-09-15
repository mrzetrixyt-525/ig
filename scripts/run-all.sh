#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait || true
}
trap cleanup INT TERM EXIT
"$ROOT/scripts/run-forever.sh" &
PIDS+=("$!")
"$ROOT/scripts/run-bot-forever.sh" &
PIDS+=("$!")
wait -n "${PIDS[@]}" || true
exit 1
