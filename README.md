# RGNODES™ VPS Manager + Host Protection

A production-oriented Discord VPS deployment bot with a native Docker guest backend, persistent SQLite state, SSHx console integration, TCP port forwarding, host protection, health monitoring, and supervised Linux services.

## Architecture

`vm.py` is the Discord VPS manager. It creates Linux VPS guests directly through the host Docker daemon.

`app/main.py` is the independent host-protection/monitoring agent. It monitors CPU, memory, disk, process signatures, connection pressure, and automatically blocks abusive public IPs through nftables/iptables/Windows Defender Firewall when available.

The two services are intentionally separate so a Discord gateway outage does not stop the host protection service.

## Important runtime requirements

Supported host: Debian/Ubuntu Linux with a working Docker daemon and permission to create privileged containers when systemd guest mode is enabled.

The bot uses Python 3.10+.

The final project intentionally does **not** ship the old SQLite database, old logs, or a live `.env`; those may contain VPS credentials, user data, or runtime secrets. Start from `.env.example`.

## Install

```bash
git clone <your-repository>
cd Rain-Cloud-Free-VPS-Manager
cp .env.example .env
nano .env
bash scripts/install-linux.sh
```

The installer:
- installs Python/virtualenv and required runtime packages;
- validates Docker and firewall tooling;
- installs `rgnodes-vps-bot.service`;
- installs `rgnodes-vps-protect.service`;
- enables both services at boot;
- creates the application directory under `/opt/rgnodes-vps-manager`.

## Start / stop / status

```bash
systemctl restart rgnodes-vps-bot.service
systemctl restart rgnodes-vps-protect.service
systemctl status rgnodes-vps-bot.service --no-pager -l
systemctl status rgnodes-vps-protect.service --no-pager -l
journalctl -u rgnodes-vps-bot.service -f
journalctl -u rgnodes-vps-protect.service -f
```

Without systemd:

```bash
bash scripts/run-bot-forever.sh
bash scripts/run-forever.sh
```

## Health endpoints

Bot:

```text
http://SERVER-IP:247/
http://SERVER-IP:247/health
```

Protection dashboard:

```text
http://SERVER-IP:5665/
http://SERVER-IP:5665/health
http://SERVER-IP:5665/api/status
```

When a dashboard token is configured, `/health` stays publicly readable for monitoring while the dashboard/API require authentication.

## Deployment behavior

A VPS is only considered ready after:
1. Docker preflight succeeds.
2. The requested image pulls successfully.
3. The guest container is created.
4. The container reaches `running`.
5. Native Linux/systemd guest readiness passes when systemd mode is enabled.
6. The SQLite VPS record is persisted.
7. SSH forwarding is attempted as a non-fatal step.
8. SSHx console setup is attempted as a non-fatal step.

SSHx or port-forward setup failures do **not** invalidate an otherwise healthy VPS.

If a deployment is cancelled or fails after persistence, the container and corresponding SQLite record are cleaned up together to prevent ghost VPS slots.

## Guest storage

Persistent application/data directories are backed by named Docker volumes. Reinstall replaces the guest container while preserving its persistent volume namespace where supported by the configuration.

## Protection defaults

The host protection agent prefers nftables and automatically falls back to iptables on Linux when nftables is present but cannot be initialized. On Windows it uses Windows Defender Firewall.

Private/local ranges are excluded from automatic public-IP blocking.

Process detection is evidence-based: direct miner signatures are flagged, while mining-network signatures require elevated CPU usage.

Automatic miner termination remains disabled by default. Enable it only after validating your workload.

## Command duplication audit

Prefix and slash interfaces intentionally expose the same management functions. These are aliases/parallel interfaces, not duplicate implementation paths:

| Function | Slash | Prefix |
|---|---|---|
| Deploy | `/deploy` | `-deploy` |
| My VPS | `/myvm`, `/myvps` | `-myvps`, `-myvm` |
| Manage | `/manage` | `-manage` |
| Start/Stop/Restart | `/start`, `/stop`, `/restart` | `-start`, `-stop`, `-restart` |
| Console | `/console`, `/sshx` | `-console`, `-sshx` |
| VPS info/stats | `/vps-info`, `/vps-stats`, `/vps-uptime` | `-vpsinfo`, `-vps-stats`, `-vps-uptime` |
| Removal | `/remove` | `-remove` |
| Sharing | `/share-user`, `/unshare-user` | `-share-user`, `-share-ruser`, `-unshare` |
| Admin VPS creation | `/admin-create`, `/create` | `-create`, `-admin...` |
| Admin remove-all | `/remove-all`, `/rm-all` | `-rm-all` |
| Suspend | `/suspend-user`, `/unsuspend-user` | `-suspand`, `-unsuspand` |
| SSH credentials | `/ssh`, `/ssh-user` | `-ssh`, `-ssh-me` |
| Backup | `/backup-vm`, `/vm-backup` | `-backup-vm`, `-vm-backup` |

The historically misspelled commands `suspand`, `unsuspand`, `anty-hacking`, `coinflp`, `spain`, `reedim`, and similar legacy aliases are retained for compatibility.

## Self-check

After installation:

```bash
python3 scripts/self-check.py
```

The self-check validates Python syntax, required imports, SQLite schema initialization, Docker reachability, Docker run feature support, and host port availability.

## Security notes

Never publish `.env`, `vps_bot.db`, `vps_bot.log`, or the `/var/lib/rgnodes` runtime data directory.

Use a firewall/security group at the VPS-provider level as the first network boundary. The RGNODES protection agent is an additional host-layer control, not a replacement for upstream DDoS filtering.

A real public IPv4 address is only useful for direct connections when the provider/NAT path actually routes the selected host port to this machine.

## Files

- `vm.py` — Discord VPS manager
- `app/main.py` — host protection and dashboard
- `scripts/install-linux.sh` — installer
- `scripts/run-bot-forever.sh` — bot supervisor without systemd
- `scripts/run-forever.sh` — protection supervisor without systemd
- `scripts/self-check.py` — preflight/repair diagnostics
- `config/config.json` — protection policy
- `.env.example` — sanitized configuration template

## Nested Docker host requirement

RGNODES systemd VPS guests require the host Docker daemon to be able to launch a privileged child container with `seccomp=unconfined` and `apparmor=unconfined`. A Docker daemon being reachable is not sufficient on an LXC/LXD/managed-container host. Run `bash scripts/host-diagnostics.sh`; if the privileged child probe is denied by AppArmor, enable Docker nesting/AppArmor permissions on the outer host/container. The RGNODES bot intentionally refuses to pretend that such a VPS is healthy when the runtime cannot actually create one.

For a normal VM or bare-metal Linux host with a fully functional Docker daemon, the deep runtime preflight should pass automatically.

## Cross-OS guest virtualization

The VPS templates currently supported by RGNODES are Ubuntu 22.04/24.04/26.04 and Debian 11/12/13. Every supported Debian/Ubuntu guest attempts to install the requested KVM/libvirt toolchain:

```bash
apt-get install -y qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst virt-manager
```

This is best-effort and never makes VPS creation fail when the provider does not expose `/dev/kvm` or nested virtualization. In that case libvirt/QEMU tools may be installed, but hardware acceleration cannot be used. The host installer also attempts the same toolchain and reports `/dev/kvm` availability.


## Economy & VPS Slots

Normal users pay **500 coins per VPS deployment**. A deployment charge is atomically deducted from the wallet and is refunded automatically when provisioning fails or times out. Duplicate deployment requests are guarded by an atomic per-user deployment cooldown so rapid clicks/messages cannot stack paid deployments.

Users can check their balance with `-bal` / `-balance` or `/bal` / `/balance`. Wallet and bank balances are displayed separately.

Additional VPS slots can be purchased one at a time with `-buy-slot` or `/buy-slot`. The default price is **1,000 coins per extra slot**, configurable with `SLOT_PURCHASE_COST`. Slot purchases atomically deduct coins and increase the slot count; partial purchases cannot commit.

Reward commands intentionally use low, cooldown-limited payouts to keep the economy sustainable: `-work`, `-hour`, `-day`, `-week`, `-month`, and `-year`.

Economy values can be configured with `DEPLOY_COST`, `SLOT_PURCHASE_COST`, `MAX_USER_SLOTS`, and `DEPLOY_COOLDOWN_SECONDS`.
