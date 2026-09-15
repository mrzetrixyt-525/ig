#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

APP_DIR="/opt/rgnodes-vps-manager"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOT_SERVICE="rgnodes-vps-bot.service"
PROTECT_SERVICE="rgnodes-vps-protect.service"

[[ "${EUID}" -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
command -v apt-get >/dev/null 2>&1 || { echo "This installer targets Debian/Ubuntu." >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates curl nftables iptables socat

# Optional host virtualization toolchain. It is best-effort so an LXC/container
# host without /dev/kvm does not make the RGNODES bot installation fail.
if ! apt-get install -y qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst virt-manager; then
  echo "WARN: full KVM/libvirt package set is unavailable; trying core headless tools." >&2
  apt-get install -y qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst || true
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now libvirtd.service >/dev/null 2>&1 || true
  systemctl enable --now virtqemud.socket >/dev/null 2>&1 || true
fi
if [[ -e /dev/kvm ]]; then
  echo "KVM device: /dev/kvm available"
else
  echo "WARN: /dev/kvm unavailable; host/provider virtualization access is restricted." >&2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI is required. Install Docker first, then rerun this installer." >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now docker.service >/dev/null 2>&1 || true
  fi
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker CLI is present but the daemon/socket is not reachable." >&2
  echo "Fix Docker service/socket permissions before deploying VPS instances." >&2
  exit 3
fi

install -d -m 0755 "$APP_DIR"
cp -a "$SRC_DIR/app" "$APP_DIR/"
cp -a "$SRC_DIR/config" "$APP_DIR/"
cp -a "$SRC_DIR/static" "$APP_DIR/"
cp -a "$SRC_DIR/templates" "$APP_DIR/"
cp -a "$SRC_DIR/scripts" "$APP_DIR/"
cp -a "$SRC_DIR/vm.py" "$APP_DIR/"
cp -a "$SRC_DIR/requirements.txt" "$APP_DIR/"
cp -a "$SRC_DIR/README.md" "$APP_DIR/"
cp -a "$SRC_DIR/ADMIN_CONFIG.md" "$APP_DIR/"
[[ -f "$SRC_DIR/.env" ]] && cp -a "$SRC_DIR/.env" "$APP_DIR/.env" || true
[[ -f "$APP_DIR/.env" ]] || cp -a "$SRC_DIR/.env.example" "$APP_DIR/.env"

install -d -m 0750 "$APP_DIR/logs"

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/pip" install --no-cache-dir -r "$APP_DIR/requirements.txt"

"$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/self-check.py" --quiet

cat >"/etc/systemd/system/$BOT_SERVICE" <<EOF
[Unit]
Description=RGNODES VPS Management Bot
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/vm.py
Restart=always
RestartSec=3
TimeoutStopSec=15
User=root
Group=root
LimitNOFILE=131072
NoNewPrivileges=false
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$APP_DIR $APP_DIR/logs
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

cat >"/etc/systemd/system/$PROTECT_SERVICE" <<EOF
[Unit]
Description=RGNODES Host Protection Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/app/main.py
Restart=always
RestartSec=3
TimeoutStopSec=10
User=root
Group=root
LimitNOFILE=131072
NoNewPrivileges=false
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$APP_DIR/logs $APP_DIR/config
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$BOT_SERVICE" "$PROTECT_SERVICE"
systemctl restart "$PROTECT_SERVICE"
systemctl restart "$BOT_SERVICE"

echo
echo "RGNODES VPS Manager installed at $APP_DIR"
echo "Bot service:      $BOT_SERVICE"
echo "Protection:       $PROTECT_SERVICE"
echo "Edit:             $APP_DIR/.env"
echo "Bot health:       http://SERVER-IP:247/health"
echo "Protect health:   http://SERVER-IP:5665/health"
