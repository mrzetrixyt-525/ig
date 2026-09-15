#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files >/dev/null 2>&1; then
    systemctl restart rgnodes-vps-protect.service
    systemctl restart rgnodes-vps-bot.service
    exit 0
fi

exec "$ROOT/scripts/run-all.sh"
