#!/usr/bin/env bash
set -Eeuo pipefail
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files >/dev/null 2>&1; then
  systemctl restart rgnodes-vps-protect.service
  systemctl restart rgnodes-vps-bot.service
else
  exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run-all.sh"
fi
