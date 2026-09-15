#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${EUID}" -eq 0 ]] || { echo "Run as root." >&2; exit 1; }

systemctl disable --now rgnodes-vps-bot.service rgnodes-vps-protect.service 2>/dev/null || true
rm -f /etc/systemd/system/rgnodes-vps-bot.service /etc/systemd/system/rgnodes-vps-protect.service
systemctl daemon-reload

if command -v nft >/dev/null 2>&1; then
  nft delete table inet rgnodes_protect 2>/dev/null || true
fi
if command -v iptables >/dev/null 2>&1; then
  iptables -D INPUT -j RGNODES_PROTECT 2>/dev/null || true
  iptables -F RGNODES_PROTECT 2>/dev/null || true
  iptables -X RGNODES_PROTECT 2>/dev/null || true
fi

rm -rf /opt/rgnodes-vps-manager
printf 'RGNODES VPS Manager + Protection removed.
'
