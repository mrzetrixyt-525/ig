# RGNODES™ VPS Manager — Deep Repair Audit

## Release

This build was audited from the previous deep-repaired release and extended with an administrator-controlled runtime configuration layer.

## Admin-customizable runtime controls

- VPS deployment cost
- Additional slot price
- Maximum user slots
- Deployment duplicate-click cooldown
- Per-user default slot allowance
- Global running VPS limit
- Global VPS record/create limit
- Maximum port forwards per VPS
- Public port range
- SSH forwarding range
- Default OS
- Default RAM / CPU / disk
- Minimum/maximum RAM / CPU / disk
- Guest bootstrap timeout
- SSHx timeout controls
- Work/hour/day/week/month/year rewards
- Invite-to-coin conversion rate
- Quiz reward
- Guest systemd mode
- Nested Docker mode
- Wings installation
- KVM/libvirt guest installation
- Persistent guest data
- Automatic SSH forwarding

Runtime values persist in SQLite and survive bot restarts.

## Plan system

Plans are editable presets with name, price, status, RAM, CPU and disk. Administrators can add, edit, enable/disable, and remove plans. Plan purchases are atomic and grant one additional slot only when the complete transaction succeeds.

## Economy reliability

- Wallet deductions are transaction-guarded.
- Slot purchases are atomic.
- Plan purchases are atomic.
- Redeem coin/slot rewards are atomic.
- Deposit is atomic.
- Failed VPS deployments refund the deployment charge.
- Deployment cooldown is a duplicate-click guard and is cleared on terminal failure.

## Protection

Protection settings can be updated through the bot and are written atomically to `config/config.json`. The protection agent watches the file and reloads it automatically. Firewall backend fallback and recovery remain enabled.

## VPS lifecycle

- Docker daemon preflight
- Privileged child-container runtime preflight
- Resource validation inside the serialized deployment section
- Deployment rollback
- Container state reconciliation
- Per-VPS lifecycle locking
- Port allocation locking
- SSHx failure isolation
- Guest bootstrap timeout handling
- Persistent data policy
- KVM/libvirt best-effort guest installation

## Verification performed

- Python 3.10 grammar parse: PASS
- Python bytecode compilation: PASS
- Shell syntax checks: PASS
- Slash command duplicate scan: PASS (0 duplicate names)
- Prefix command duplicate scan: PASS (0 duplicate names)
- Alias duplicate scan: PASS (0 duplicate aliases)
- `os.system()` scan: PASS (0)
- `shell=True` scan: PASS (0)
- Configuration JSON validation: PASS
- Release archive integrity: PASS

## Environment-dependent verification

A real Discord login and a real Docker child-container deployment cannot be executed in an offline build environment. The release therefore includes `scripts/self-check.py` and `scripts/host-diagnostics.sh` for target-host validation, including the exact privileged nested-Docker/AppArmor boundary that previously caused deployment failure.
