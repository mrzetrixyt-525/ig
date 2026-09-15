# RGNODES™ Security & Reliability Notes

## Deployment safety

The bot refuses to create a systemd guest when the host Docker CLI cannot provide the required capabilities. It never treats SSHx availability as VPS readiness.

Deployment state is persisted only after the container passes the configured readiness gate. Cancellation/error cleanup removes the matching SQLite row and Docker container together when a transaction has already persisted.

## Firewall safety

Automatic blocks exclude loopback, private, link-local and configured safe networks. Linux startup prefers nftables but falls back to iptables if nftables initialization fails. A firewall backend failure never pretends that protection is active.

## Required upstream controls

Use the provider firewall/security group in front of the host. Host-layer blocking cannot stop volumetric DDoS traffic before it consumes upstream bandwidth.

## Public networking

A detected public IPv4 address is informational unless the provider/network actually routes inbound traffic for the selected port. NAT, CGNAT, Cloudflare proxying and provider filtering can prevent direct inbound SSH/port forwarding.

## Secrets

Do not commit:
- `.env`
- `vps_bot.db`
- `vps_bot.log`
- runtime SSHx state
- `/var/lib/rgnodes` contents

The distributed project contains only `.env.example` and no previous runtime database/logs.

## Second-pass hardening (2026-09-14)

- A blank protection-dashboard token is now local/private-network only. Public access requires an explicit dashboard token.
- nftables timeout blocks are rehydrated into in-memory state after agent restart.
- Windows protection rule expiry markers are restored into in-memory state when the PowerShell output exposes the protected address.
- VPS resource-capacity checks are performed inside the serialized creation lock to prevent concurrent over-allocation.
