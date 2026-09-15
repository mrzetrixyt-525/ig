#!/usr/bin/env bash
set -u
printf '%s\n' '=== RGNODES Host / Nested-Docker Diagnostics ==='
printf 'Virtualization: '; systemd-detect-virt 2>/dev/null || true
printf 'PID 1: '; ps -p 1 -o comm= 2>/dev/null || true
printf 'AppArmor enabled: '; cat /sys/module/apparmor/parameters/enabled 2>/dev/null || printf 'unknown\n'
printf 'KVM device: '; if [ -e /dev/kvm ]; then printf 'available\n'; else printf 'unavailable\n'; fi
printf 'libvirt: '; if command -v virsh >/dev/null 2>&1; then virsh --version 2>/dev/null || printf 'installed\n'; else printf 'missing\n'; fi
printf 'Docker: '; command -v docker || printf 'missing\n'
if command -v docker >/dev/null 2>&1; then
  docker info >/dev/null 2>&1 && echo 'daemon reachable' || echo 'daemon unreachable'
  echo '--- privileged child probe ---'
  docker run --rm --privileged \
    --security-opt seccomp=unconfined \
    --security-opt apparmor=unconfined \
    ubuntu:24.04 /bin/sh -lc 'echo RGNODES-RUNTIME-PREFLIGHT-OK' 2>&1 || true
fi
printf '%s\n' '=== Interpretation ==='
printf '%s\n' 'If the probe reports an AppArmor permission-denied error while Docker info succeeds, the outer LXC/LXD/host policy must allow Docker nesting. This cannot be overridden from inside the child VPS.'
