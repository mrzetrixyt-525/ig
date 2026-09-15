import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import secrets
import shutil
import socket
import subprocess
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psutil

BASE = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE / "config" / "config.json"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 5665,
    "monitor_interval": 2,
    "dashboard": {"token": ""},
    "safe_ips": [],
    "safe_networks": [
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
    ],
    "thresholds": {
        "cpu_percent": 95,
        "memory_percent": 95,
        "disk_percent": 95,
        "connections_per_ip": 120,
        "connection_events_per_minute": 240,
        "syn_like_states_per_ip": 80,
        "global_connection_events_per_minute": 3000,
        "block_score": 6,
        "repeat_offense_window": 300,
        "repeat_offense_count": 2,
    },
    "protection": {
        "auto_block": True,
        "block_seconds": 1800,
        "max_blocks": 500,
        "firewall_backend": "auto",
        "auto_stop_confirmed_miners": False,
        "enable_linux_hardening": False,
    },
    "miner_detection": {
        "enabled": True,
        "confirm_cpu_percent": 70,
        "miner_keywords": [
            "xmrig",
            "minerd",
            "cpuminer",
            "cgminer",
            "bfgminer",
            "nanominer",
            "t-rex",
            "lolminer",
            "nbminer",
            "teamredminer",
            "phoenixminer",
            "ethminer",
            "rigel",
            "wildrig",
            "hellminer",
            "gminer",
        ],
        "network_keywords": [
            "stratum+tcp",
            "stratum+ssl",
            "stratum1+tcp",
            "mining.pool",
            "pool.minergate",
        ],
    },
}


def deep_merge(base, override):
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            result[key] = deep_merge(current, value)
        else:
            result[key] = value
    return result


def load_config():
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        if CONFIG_FILE.exists():
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("config root must be an object")
        else:
            raw = {}
        merged = deep_merge(DEFAULT_CONFIG, raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logging.getLogger("rgnodes").error("Invalid config: %s", exc)
        merged = deep_merge(DEFAULT_CONFIG, {})

    try:
        CONFIG_FILE.write_text(
            json.dumps(merged, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logging.getLogger("rgnodes").warning(
            "Could not write normalized config: %s", exc
        )
    return merged


CONFIG = load_config()
try:
    CONFIG_MTIME_NS = CONFIG_FILE.stat().st_mtime_ns
except OSError:
    CONFIG_MTIME_NS = 0

logger = logging.getLogger("rgnodes")
logger.setLevel(logging.INFO)
file_handler = RotatingFileHandler(
    LOG_DIR / "agent.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(message)s")
)
logger.addHandler(file_handler)
logger.addHandler(logging.StreamHandler())

state_lock = threading.RLock()
state = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "last_update": None,
    "platform": platform.platform(),
    "hostname": socket.gethostname(),
    "status": "starting",
    "agent_pid": os.getpid(),
    "firewall": {"backend": "unknown", "ready": False, "reason": ""},
    "alerts": deque(maxlen=200),
    "blocked_ips": {},
    "metrics": {},
    "processes": [],
    "connections": {},
    "interfaces": {},
}

ip_events = defaultdict(lambda: deque(maxlen=1200))
offense_history = defaultdict(lambda: deque(maxlen=20))
blocked_until = {}
firewall_rules = {}
stop_event = threading.Event()
alert_events = deque(maxlen=5000)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def add_alert(level, title, detail, source="agent"):
    now = now_ts()
    limit = max(1, int(CONFIG.get("protection", {}).get("max_alerts_per_minute", 120)))
    with state_lock:
        cutoff = now - 60
        while alert_events and alert_events[0] < cutoff:
            alert_events.popleft()
        if len(alert_events) >= limit:
            # Keep one compact notice rather than allowing repeated alerts to
            # consume memory, rotate logs, and overwhelm the dashboard.
            if not alert_events or now - alert_events[-1] > 60:
                alert_events.append(now)
                state["alerts"].appendleft({
                    "time": now_iso(),
                    "level": "warning",
                    "title": "Alert rate limited",
                    "detail": f"Protection generated more than {limit} alerts/minute; excess alerts were suppressed.",
                    "source": "agent",
                })
            return
        alert_events.append(now)
        item = {
            "time": now_iso(),
            "level": level,
            "title": title,
            "detail": detail,
            "source": source,
        }
        state["alerts"].appendleft(item)
    logger.info("[%s] %s: %s", level.upper(), title, detail)


def run_command(args, timeout=5, input_text=None):
    try:
        return subprocess.run(
            args,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (
        FileNotFoundError,
        PermissionError,
        subprocess.TimeoutExpired,
        OSError,
    ) as exc:
        logger.debug("command failed %r: %s", args, exc)
        return None


def windows():
    return platform.system().lower() == "windows"


def linux():
    return platform.system().lower() == "linux"


def valid_ip(value):
    try:
        return ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return None


def local_addresses():
    result = set()
    try:
        for addrs in psutil.net_if_addrs().values():
            for item in addrs:
                parsed = valid_ip(item.address)
                if parsed:
                    result.add(str(parsed))
    except Exception as exc:
        logger.debug("local address scan failed: %s", exc)
    return result


def safe_ip(ip):
    parsed = valid_ip(ip)
    if parsed is None:
        return True

    if parsed.version == 6 and parsed.ipv4_mapped:
        parsed = parsed.ipv4_mapped

    if (
        parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_unspecified
        or parsed.is_reserved
        or parsed.is_private
    ):
        return True

    if str(parsed) in local_addresses():
        return True

    for raw in CONFIG.get("safe_ips", []):
        trusted = valid_ip(raw)
        if trusted is None:
            continue
        if trusted == parsed:
            return True

    for raw in CONFIG.get("safe_networks", []):
        try:
            network = ipaddress.ip_network(str(raw), strict=False)
            if parsed.version == network.version and parsed in network:
                return True
        except ValueError:
            continue
    return False


def firewall_candidates():
    forced = str(
        CONFIG.get("protection", {}).get("firewall_backend", "auto")
    ).lower()
    if forced in {"nftables", "iptables", "windows"}:
        return [forced]
    if windows():
        return ["windows"]
    if linux():
        # Prefer nftables, but automatically fall back to iptables when the
        # nft binary exists but kernel/container permissions prevent setup.
        return [backend for backend in ("nftables", "iptables") if (
            shutil.which("nft") if backend == "nftables" else shutil.which("iptables")
        )]
    return []

def choose_firewall():
    candidates = firewall_candidates()
    return candidates[0] if candidates else "unsupported"


def nft_script(script):
    return run_command(["nft", "-f", "-"], input_text=script)


def init_nftables():
    table = run_command(
        ["nft", "list", "table", "inet", "rgnodes_protect"]
    )
    if not table or table.returncode != 0:
        res = nft_script("add table inet rgnodes_protect\n")
        if not res or res.returncode != 0:
            return False, "could not create nftables table"

    set4 = run_command(
        ["nft", "list", "set", "inet", "rgnodes_protect", "blocked4"]
    )
    if not set4 or set4.returncode != 0:
        res = nft_script(
            "add set inet rgnodes_protect blocked4 { "
            "type ipv4_addr; flags timeout; }\n"
        )
        if not res or res.returncode != 0:
            return False, "could not create IPv4 block set"

    set6 = run_command(
        ["nft", "list", "set", "inet", "rgnodes_protect", "blocked6"]
    )
    if not set6 or set6.returncode != 0:
        res = nft_script(
            "add set inet rgnodes_protect blocked6 { "
            "type ipv6_addr; flags timeout; }\n"
        )
        if not res or res.returncode != 0:
            return False, "could not create IPv6 block set"

    chain = run_command(
        ["nft", "list", "chain", "inet", "rgnodes_protect", "input"]
    )
    if not chain or chain.returncode != 0:
        res = nft_script(
            "add chain inet rgnodes_protect input { "
            "type filter hook input priority -50; policy accept; }\n"
        )
        if not res or res.returncode != 0:
            return False, "could not create nftables input chain"

    listing = run_command(
        ["nft", "list", "chain", "inet", "rgnodes_protect", "input"]
    )
    text = listing.stdout if listing else ""
    additions = []
    if "ip saddr @blocked4 drop" not in text:
        additions.append(
            "add rule inet rgnodes_protect input "
            "ip saddr @blocked4 drop"
        )
    if "ip6 saddr @blocked6 drop" not in text:
        additions.append(
            "add rule inet rgnodes_protect input "
            "ip6 saddr @blocked6 drop"
        )
    if additions:
        res = nft_script("\n".join(additions) + "\n")
        if not res or res.returncode != 0:
            return False, "could not install nftables drop rules"
    return True, "nftables ready"


def init_iptables():
    chain = run_command(["iptables", "-N", "RGNODES_PROTECT"])
    if chain and chain.returncode not in (0, 1):
        return False, "could not create RGNODES_PROTECT chain"

    check = run_command(
        ["iptables", "-C", "INPUT", "-j", "RGNODES_PROTECT"]
    )
    if not check or check.returncode != 0:
        res = run_command(
            ["iptables", "-I", "INPUT", "1", "-j", "RGNODES_PROTECT"]
        )
        if not res or res.returncode != 0:
            return False, "could not attach RGNODES_PROTECT to INPUT"
    return True, "iptables ready"


def init_windows_firewall():
    probe = run_command(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-NetFirewallProfile | Out-Null",
        ]
    )
    if not probe or probe.returncode != 0:
        return False, "Windows Defender Firewall is unavailable"
    return True, "Windows Defender Firewall ready"


def init_firewall():
    candidates = firewall_candidates()
    if not candidates:
        state["firewall"].update(
            backend="unsupported",
            ready=False,
            reason="no supported firewall backend detected",
        )
        return False

    failures = []
    for backend in candidates:
        if backend == "nftables":
            ok, reason = init_nftables()
        elif backend == "iptables":
            ok, reason = init_iptables()
        elif backend == "windows":
            ok, reason = init_windows_firewall()
        else:
            ok, reason = False, "unsupported backend"

        if ok:
            state["firewall"].update(backend=backend, ready=True, reason=reason)
            return True
        failures.append(f"{backend}: {reason}")

    state["firewall"].update(
        backend=candidates[0],
        ready=False,
        reason="; ".join(failures) or "firewall initialization failed",
    )
    return False


def iptables_existing_blocks():
    result = {}
    listing = run_command(["iptables", "-S", "RGNODES_PROTECT"])
    if not listing or listing.returncode != 0:
        return result

    for line in listing.stdout.splitlines():
        if not line.startswith("-A RGNODES_PROTECT "):
            continue
        parts = line.split()
        try:
            src = parts[parts.index("-s") + 1]
            comment_index = parts.index("--comment")
            comment = parts[comment_index + 1]
            prefix = "RGNODES_PROTECT|"
            if not comment.startswith(prefix):
                continue
            expires = int(comment[len(prefix):])
            parsed = valid_ip(src)
            if parsed:
                result[str(parsed)] = expires
        except (ValueError, IndexError):
            continue
    return result


def sync_iptables_blocks():
    existing = iptables_existing_blocks()
    now = int(now_ts())
    for ip, expires in existing.items():
        if expires <= now or safe_ip(ip):
            run_command(
                [
                    "iptables",
                    "-D",
                    "RGNODES_PROTECT",
                    "-s",
                    ip,
                    "-j",
                    "DROP",
                    "-m",
                    "comment",
                    "--comment",
                    f"RGNODES_PROTECT|{expires}",
                ]
            )
            continue
        blocked_until[ip] = float(expires)
        firewall_rules[ip] = "iptables"


def make_windows_rule_name(ip):
    safe = ip.replace(":", "_").replace(".", "_")
    return f"RGNodesProtect {safe}"


def sync_windows_blocks():
    if not windows():
        return
    script = (
        "Get-NetFirewallRule -Group 'RG Nodes VPS Protect' "
        "-ErrorAction SilentlyContinue | "
        "Get-NetFirewallAddressFilter | "
        "Select-Object InstanceID,RemoteAddress"
    )
    result = run_command(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ]
    )
    if not result or result.returncode != 0:
        return
    # Windows rules created by this agent include an expiry marker.
    marker_script = (
        "Get-NetFirewallRule -Group 'RG Nodes VPS Protect' "
        "-ErrorAction SilentlyContinue | "
        "Select-Object Name,Description"
    )
    result = run_command(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            marker_script,
        ]
    )
    if not result or result.returncode != 0:
        return
    for line in result.stdout.splitlines():
        if "RGNodesProtect|" not in line:
            continue
        try:
            marker = line.split("RGNodesProtect|", 1)[1].split(None, 1)[0]
            expires = int(marker)
            # The Windows query output can expose the address on the same line
            # or on a separate address-filter query depending on PowerShell.
            # When an IPv4/IPv6 literal is present, restore its local expiry
            # bookkeeping; the firewall rule itself remains authoritative.
            for candidate in re.findall(r"(?:\d{1,3}\.){3}\d{1,3}|[0-9A-Fa-f:]{2,}", line):
                parsed = valid_ip(candidate)
                if parsed and not safe_ip(str(parsed)) and expires > int(now_ts()):
                    blocked_until[str(parsed)] = float(expires)
                    firewall_rules[str(parsed)] = "windows"
        except (IndexError, ValueError):
            continue


def block_ip(ip, seconds=None, reason="anomaly"):
    parsed = valid_ip(ip)
    if parsed is None or safe_ip(ip):
        return False, "safe/invalid address"
    if not CONFIG["protection"].get("auto_block", True):
        return False, "auto_block disabled"
    if not state["firewall"]["ready"]:
        return False, state["firewall"]["reason"] or "firewall not ready"

    now = now_ts()
    seconds = int(
        seconds or CONFIG["protection"].get("block_seconds", 1800)
    )
    expires = now + max(30, min(seconds, 604800))
    existing = blocked_until.get(str(parsed), 0)
    if existing > now:
        return False, "already blocked"

    cleanup_expired_blocks()
    if len(blocked_until) >= int(
        CONFIG["protection"].get("max_blocks", 500)
    ):
        return False, "block table full"

    backend = state["firewall"]["backend"]
    target = str(parsed)
    ok = False

    if backend == "nftables":
        set_name = "blocked4" if parsed.version == 4 else "blocked6"
        spec = (
            f"add element inet rgnodes_protect {set_name} "
            f"{{ {target} timeout {int(expires - now)}s }}\n"
        )
        res = nft_script(spec)
        ok = bool(res and res.returncode == 0)
    elif backend == "iptables" and parsed.version == 4:
        comment = f"RGNODES_PROTECT|{int(expires)}"
        check = run_command(
            [
                "iptables",
                "-C",
                "RGNODES_PROTECT",
                "-s",
                target,
                "-j",
                "DROP",
                "-m",
                "comment",
                "--comment",
                comment,
            ]
        )
        if check and check.returncode == 0:
            ok = True
        else:
            res = run_command(
                [
                    "iptables",
                    "-I",
                    "RGNODES_PROTECT",
                    "-s",
                    target,
                    "-m",
                    "comment",
                    "--comment",
                    comment,
                    "-j",
                    "DROP",
                ]
            )
            ok = bool(res and res.returncode == 0)
    elif backend == "windows":
        name = make_windows_rule_name(target)
        description = f"RGNodesProtect|{int(expires)}|{reason[:120]}"
        run_command(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "Remove-NetFirewallRule -DisplayName "
                    f"'{name}' -ErrorAction SilentlyContinue; "
                    "New-NetFirewallRule "
                    f"-DisplayName '{name}' "
                    "-Direction Inbound -Action Block "
                    f"-RemoteAddress '{target}' "
                    "-Group 'RG Nodes VPS Protect' "
                    f"-Description '{description}' | Out-Null"
                ),
            ]
        )
        check = run_command(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "Get-NetFirewallRule -DisplayName "
                    f"'{name}' -ErrorAction SilentlyContinue | "
                    "Select-Object -First 1 -ExpandProperty Name"
                ),
            ]
        )
        ok = bool(check and check.returncode == 0 and check.stdout.strip())
        firewall_rules[target] = name

    if ok:
        blocked_until[target] = expires
        firewall_rules[target] = backend
        add_alert(
            "critical",
            "IP automatically blocked",
            f"{target} for {int(expires - now)}s — {reason} ({backend})",
            "firewall",
        )
        return True, backend
    return False, f"{backend} block operation failed"


def unblock_ip(ip):
    parsed = valid_ip(ip)
    target = str(parsed) if parsed else str(ip)
    backend = state["firewall"]["backend"]
    expires = int(blocked_until.get(target, 0))

    if backend == "nftables" and parsed:
        set_name = "blocked4" if parsed.version == 4 else "blocked6"
        nft_script(
            f"delete element inet rgnodes_protect {set_name} {{ {target} }}\n"
        )
    elif backend == "iptables" and parsed and parsed.version == 4:
        comment = f"RGNODES_PROTECT|{expires}"
        run_command(
            [
                "iptables",
                "-D",
                "RGNODES_PROTECT",
                "-s",
                target,
                "-j",
                "DROP",
                "-m",
                "comment",
                "--comment",
                comment,
            ]
        )
    elif backend == "windows":
        name = firewall_rules.get(target) or make_windows_rule_name(target)
        run_command(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"Remove-NetFirewallRule -DisplayName '{name}' "
                "-ErrorAction SilentlyContinue",
            ]
        )
    blocked_until.pop(target, None)
    firewall_rules.pop(target, None)


def cleanup_expired_blocks():
    now = now_ts()
    for ip, until in list(blocked_until.items()):
        if until <= now:
            unblock_ip(ip)
            add_alert("info", "Automatic block expired", ip, "firewall")


def record_offense(ip):
    now = now_ts()
    history = offense_history[ip]
    history.append(now)
    cutoff = now - int(
        CONFIG["thresholds"].get("repeat_offense_window", 300)
    )
    while history and history[0] < cutoff:
        history.popleft()
    return len(history)


def sample_connections():
    counts = defaultdict(int)
    states_by_ip = defaultdict(lambda: defaultdict(int))
    timestamp = now_ts()
    try:
        conns = psutil.net_connections(kind="inet")
    except Exception as exc:
        logger.warning("net_connections failed: %s", exc)
        conns = []

    for conn in conns:
        if not conn.raddr:
            continue
        remote_ip = str(conn.raddr.ip).split("%", 1)[0]
        if safe_ip(remote_ip):
            continue
        counts[remote_ip] += 1
        states_by_ip[remote_ip][conn.status] += 1
        ip_events[remote_ip].append(timestamp)

    cutoff = timestamp - 60
    events_per_minute = 0
    for ip, events in list(ip_events.items()):
        while events and events[0] < cutoff:
            events.popleft()
        if events:
            events_per_minute += len(events)
        else:
            ip_events.pop(ip, None)

    threshold = CONFIG["thresholds"]
    offenders = []
    for ip, count in counts.items():
        recent = len(ip_events.get(ip, ()))
        syn_like = sum(
            states_by_ip[ip].get(status, 0)
            for status in ("SYN_SENT", "SYN_RECV")
        )
        close_wait = states_by_ip[ip].get("CLOSE_WAIT", 0)
        score = 0
        reasons = []

        if count >= int(threshold["connections_per_ip"]):
            score += 5
            reasons.append(f"{count} concurrent connections")
        if recent >= int(threshold["connection_events_per_minute"]):
            score += 4
            reasons.append(f"{recent} connection events/min")
        if syn_like >= int(threshold["syn_like_states_per_ip"]):
            score += 4
            reasons.append(f"{syn_like} SYN-related states")
        if close_wait >= int(threshold["connections_per_ip"]):
            score += 3
            reasons.append(f"{close_wait} CLOSE_WAIT sockets")

        repeats = record_offense(ip) if score else 0
        if repeats >= int(threshold["repeat_offense_count"]):
            score += 2
            reasons.append(f"repeat offense {repeats}x")
        if score:
            offenders.append((ip, score, reasons))

    total = sum(counts.values())
    established = sum(
        values.get(psutil.CONN_ESTABLISHED, 0)
        for values in states_by_ip.values()
    )
    global_pressure = events_per_minute >= int(
        threshold.get("global_connection_events_per_minute", 3000)
    )
    return {
        "total": total,
        "established": established,
        "global_pressure": global_pressure,
        "unique_ips": len(counts),
        "top_ips": sorted(
            counts.items(), key=lambda item: item[1], reverse=True
        )[:12],
        "offenders": offenders[:20],
        "events_per_minute": events_per_minute,
    }


def process_snapshot():
    detection = CONFIG["miner_detection"]
    if not detection.get("enabled", True):
        return []

    suspicious = []
    keywords = [
        str(item).lower() for item in detection.get("miner_keywords", [])
    ]
    network_keywords = [
        str(item).lower()
        for item in detection.get("network_keywords", [])
    ]
    cpu_threshold = float(detection.get("confirm_cpu_percent", 70))

    try:
        iterator = psutil.process_iter(
            [
                "pid",
                "name",
                "username",
                "cpu_percent",
                "memory_percent",
                "cmdline",
                "exe",
                "ppid",
            ]
        )
        for process in iterator:
            try:
                info = process.info
                name = str(info.get("name") or "").lower()
                command = " ".join(info.get("cmdline") or []).lower()
                exe = str(info.get("exe") or "").lower()
                cpu = float(info.get("cpu_percent") or 0)
                combined = f"{name} {command} {exe}"
                direct = next(
                    (item for item in keywords if item in combined), None
                )
                network = next(
                    (item for item in network_keywords if item in command),
                    None,
                )
                if not direct and not (network and cpu >= cpu_threshold):
                    continue
                suspicious.append(
                    {
                        "pid": info.get("pid"),
                        "name": info.get("name"),
                        "user": info.get("username"),
                        "cpu": round(cpu, 1),
                        "memory": round(
                            float(info.get("memory_percent") or 0), 1
                        ),
                        "match": direct or network,
                        "exe": info.get("exe"),
                        "ppid": info.get("ppid"),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as exc:
        logger.warning("process scan failed: %s", exc)
    return sorted(
        suspicious, key=lambda item: item["cpu"], reverse=True
    )[:30]


def metrics_snapshot():
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.abspath(os.sep))
    try:
        load = os.getloadavg()
    except (AttributeError, OSError):
        load = (0.0, 0.0, 0.0)
    net = psutil.net_io_counters()
    boot = psutil.boot_time()
    return {
        "cpu": round(psutil.cpu_percent(interval=None), 1),
        "memory": round(vm.percent, 1),
        "disk": round(disk.percent, 1),
        "load1": round(load[0], 2),
        "load5": round(load[1], 2),
        "load15": round(load[2], 2),
        "bytes_sent": net.bytes_sent,
        "bytes_recv": net.bytes_recv,
        "boot_time": datetime.fromtimestamp(
            boot, timezone.utc
        ).isoformat(),
        "uptime_seconds": max(0, int(now_ts() - boot)),
    }


def interface_snapshot():
    result = {}
    try:
        counters = psutil.net_io_counters(pernic=True)
        for name, counter in counters.items():
            result[name] = {
                "bytes_sent": counter.bytes_sent,
                "bytes_recv": counter.bytes_recv,
                "packets_sent": counter.packets_sent,
                "packets_recv": counter.packets_recv,
                "errin": counter.errin,
                "errout": counter.errout,
                "dropin": counter.dropin,
                "dropout": counter.dropout,
            }
    except Exception as exc:
        logger.warning("interface scan failed: %s", exc)
    return result


def linux_hardening():
    if not linux() or not CONFIG["protection"].get(
        "enable_linux_hardening", False
    ):
        return
    values = {
        "net.ipv4.tcp_syncookies": "1",
        "net.ipv4.conf.all.rp_filter": "1",
        "net.ipv4.conf.default.rp_filter": "1",
    }
    for key, value in values.items():
        run_command(["sysctl", "-w", f"{key}={value}"])


def maybe_stop_miner(item):
    if not CONFIG["protection"].get(
        "auto_stop_confirmed_miners", False
    ):
        return
    try:
        process = psutil.Process(int(item["pid"]))
        process.terminate()
        try:
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            process.kill()
        add_alert(
            "critical",
            "Confirmed miner stopped",
            f"PID {item['pid']} {item['name']} ({item['match']})",
            "miner",
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError) as exc:
        add_alert(
            "warning",
            "Miner could not be stopped",
            f"PID {item.get('pid')}: {exc}",
            "miner",
        )


def sync_nftables_blocks():
    """Rehydrate nftables timeout entries into process state after restart.

    nftables owns the actual timeout, so the agent only needs to reconstruct
    the entries for dashboard visibility, max-block accounting, and cleanup.
    The parser accepts both the classic text representation and the JSON
    representation emitted by modern nft versions.
    """
    now = now_ts()
    for family, set_name in (("ip", "blocked4"), ("ip6", "blocked6")):
        result = run_command([
            "nft", "-j", "list", "set", "inet", "rgnodes_protect", set_name,
        ], timeout=8)
        if not result or result.returncode != 0:
            # Text fallback for older nft builds without JSON support.
            result = run_command([
                "nft", "list", "set", "inet", "rgnodes_protect", set_name,
            ], timeout=8)
            if not result or result.returncode != 0:
                continue
            text = result.stdout
            for match in re.finditer(
                r"(?m)^\s*([0-9a-fA-F:.]+)/?(?:\d+)?\s+timeout\s+([0-9]+)([smhd])",
                text,
            ):
                ip = valid_ip(match.group(1))
                if not ip or safe_ip(str(ip)):
                    continue
                value = int(match.group(2))
                multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(3)]
                blocked_until[str(ip)] = now + max(1, value * multiplier)
                firewall_rules[str(ip)] = "nftables"
            continue

        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

        def walk(node):
            if isinstance(node, dict):
                elem = node.get("elem")
                if isinstance(elem, dict):
                    value = elem.get("val")
                    timeout = elem.get("expires", elem.get("timeout"))
                    ip = valid_ip(value)
                    try:
                        seconds = float(timeout)
                    except (TypeError, ValueError):
                        seconds = 0
                    if ip and not safe_ip(str(ip)) and seconds > 0:
                        blocked_until[str(ip)] = now + seconds
                        firewall_rules[str(ip)] = "nftables"
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(payload)

def sync_existing_firewall_blocks():
    backend = state["firewall"]["backend"]
    if backend == "nftables":
        sync_nftables_blocks()
    elif backend == "iptables":
        sync_iptables_blocks()
    elif backend == "windows":
        sync_windows_blocks()


def reload_config_if_changed():
    """Reload protection configuration after an atomic config-file update."""
    global CONFIG, CONFIG_MTIME_NS
    try:
        mtime = CONFIG_FILE.stat().st_mtime_ns
    except OSError:
        return False
    if mtime == CONFIG_MTIME_NS:
        return False
    try:
        CONFIG = load_config()
        CONFIG_MTIME_NS = mtime
        add_alert("info", "Protection configuration reloaded", "config.json changed", "config")
        return True
    except Exception as exc:
        logger.exception("Protection configuration reload failed")
        add_alert("error", "Protection configuration reload failed", str(exc), "config")
        return False


def worker():
    psutil.cpu_percent(interval=None)
    firewall_ok = init_firewall()
    if firewall_ok:
        sync_existing_firewall_blocks()
    linux_hardening()
    add_alert(
        "info",
        "Protection agent started",
        (
            f"{platform.system()} / firewall="
            f"{state['firewall']['backend']} / 24x7 monitor active"
        ),
    )

    if not firewall_ok:
        add_alert(
            "error",
            "Firewall protection unavailable",
            state["firewall"]["reason"],
            "firewall",
        )

    last_firewall_retry = 0.0
    while not stop_event.is_set():
        started = now_ts()
        try:
            if reload_config_if_changed():
                firewall_ok = False
                last_firewall_retry = now_ts() - 29
            if not firewall_ok and now_ts() - last_firewall_retry >= 30:
                last_firewall_retry = now_ts()
                firewall_ok = init_firewall()
                if firewall_ok:
                    sync_existing_firewall_blocks()
                    add_alert("info", "Firewall protection restored", state["firewall"]["backend"], "firewall")
            elif firewall_ok:
                # A backend can disappear after boot (package reload, policy
                # change, or firewall service restart). Probe cheaply and allow
                # the next retry to select a fallback backend.
                backend = state["firewall"]["backend"]
                if backend == "nftables":
                    probe = run_command(["nft", "list", "table", "inet", "rgnodes_protect"], timeout=5)
                    if not probe or probe.returncode != 0:
                        firewall_ok = False
                        state["firewall"]["ready"] = False
                        last_firewall_retry = now_ts() - 29
                elif backend == "iptables":
                    probe = run_command(["iptables", "-S", "RGNODES_PROTECT"], timeout=5)
                    if not probe or probe.returncode != 0:
                        firewall_ok = False
                        state["firewall"]["ready"] = False
                        last_firewall_retry = now_ts() - 29
            cleanup_expired_blocks()
            metrics = metrics_snapshot()
            connections = sample_connections()
            miners = process_snapshot()
            interfaces = interface_snapshot()
            threshold = CONFIG["thresholds"]

            if metrics["cpu"] >= threshold["cpu_percent"]:
                add_alert(
                    "warning",
                    "Critical CPU pressure",
                    f"CPU {metrics['cpu']}%",
                    "resource",
                )
            if metrics["memory"] >= threshold["memory_percent"]:
                add_alert(
                    "warning",
                    "Critical memory pressure",
                    f"RAM {metrics['memory']}%",
                    "resource",
                )
            if metrics["disk"] >= threshold["disk_percent"]:
                add_alert(
                    "warning",
                    "Critical disk pressure",
                    f"Disk {metrics['disk']}%",
                    "resource",
                )

            for item in miners:
                add_alert(
                    "critical",
                    "Confirmed mining signature",
                    (
                        f"PID {item['pid']} {item['name']} — "
                        f"{item['match']} — {item['cpu']}% CPU"
                    ),
                    "miner",
                )
                maybe_stop_miner(item)

            if connections.get("global_pressure"):
                add_alert(
                    "critical",
                    "Global connection pressure",
                    f"{connections['events_per_minute']} "
                    "connection events/min",
                    "network",
                )

            for ip, score, reasons in connections["offenders"]:
                reason = "; ".join(reasons)
                if score >= int(threshold["block_score"]):
                    ok, method = block_ip(ip, reason=reason)
                    if not ok and method != "already blocked":
                        add_alert(
                            "warning",
                            "Critical traffic anomaly",
                            f"{ip}: {reason}; auto-block failed: {method}",
                            "network",
                        )
                else:
                    add_alert(
                        "warning",
                        "Suspicious traffic",
                        f"{ip}: {reason} (score={score})",
                        "network",
                    )

            danger = bool(miners) or bool(connections["offenders"])
            danger = danger or bool(connections.get("global_pressure"))
            danger = danger or metrics["cpu"] >= threshold["cpu_percent"]
            danger = danger or (
                metrics["memory"] >= threshold["memory_percent"]
            )
            with state_lock:
                state["metrics"] = metrics
                state["connections"] = {
                    key: value
                    for key, value in connections.items()
                    if key != "offenders"
                }
                state["processes"] = miners
                state["interfaces"] = interfaces
                state["blocked_ips"] = {
                    ip: max(0, int(until - now_ts()))
                    for ip, until in blocked_until.items()
                    if until > now_ts()
                }
                if not firewall_ok:
                    state["status"] = "firewall-unavailable"
                else:
                    state["status"] = (
                        "under-pressure" if danger else "protected"
                    )
                state["last_update"] = now_iso()
        except Exception as exc:
            logger.exception("monitor loop error")
            add_alert(
                "error",
                "Monitor loop recovered",
                str(exc),
                "agent",
            )
        elapsed = now_ts() - started
        interval = float(CONFIG.get("monitor_interval", 2))
        stop_event.wait(max(0.5, interval - elapsed))


HTML_FILE = BASE / "templates" / "index.html"
CSS_FILE = BASE / "static" / "style.css"


def dashboard_token_ok(path, headers, client_host=""):
    token = str(CONFIG.get("dashboard", {}).get("token", "") or "")
    query_token = parse_qs(urlparse(path).query).get("token", [""])[0]
    supplied = query_token or headers.get("X-RGNodes-Token", "")
    if token:
        return secrets.compare_digest(str(supplied), token)

    # A blank dashboard token must never mean "public dashboard" because the
    # status API contains hostnames, process names, network metadata, alerts,
    # and blocked addresses. Permit the tokenless dashboard only from local or
    # private addresses; set dashboard.token for deliberate remote access.
    try:
        peer = ipaddress.ip_address(str(client_host).split("%", 1)[0])
        return peer.is_loopback or peer.is_private or peer.is_link_local
    except ValueError:
        return False


def json_bytes(obj):
    return json.dumps(
        obj,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "RGNodesProtect/3"

    def log_message(self, fmt, *args):
        rendered = fmt % args
        # Never persist dashboard access tokens that may appear in query strings.
        rendered = re.sub(r"([?&]token=)[^&\s]+", r"\1<redacted>", rendered, flags=re.I)
        logger.info("dashboard %s - %s", self.address_string(), rendered)

    def send_body(self, status, body, content_type):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path != "/health" and not dashboard_token_ok(self.path, self.headers, self.client_address[0] if self.client_address else ""):
            self.send_body(
                401,
                b"Unauthorized",
                "text/plain; charset=utf-8",
            )
            return
        try:
            if path == "/":
                self.send_body(
                    200,
                    HTML_FILE.read_bytes(),
                    "text/html; charset=utf-8",
                )
                return
            if path == "/static/style.css":
                self.send_body(
                    200,
                    CSS_FILE.read_bytes(),
                    "text/css; charset=utf-8",
                )
                return
            if path == "/api/status":
                with state_lock:
                    payload = {
                        "started_at": state["started_at"],
                        "last_update": state["last_update"],
                        "status": state["status"],
                        "platform": state["platform"],
                        "hostname": state["hostname"],
                        "agent_pid": state["agent_pid"],
                        "firewall": dict(state["firewall"]),
                        "metrics": dict(state["metrics"]),
                        "connections": dict(state["connections"]),
                        "processes": list(state["processes"]),
                        "blocked_ips": dict(state["blocked_ips"]),
                        "interfaces": dict(state["interfaces"]),
                        "alerts": list(state["alerts"])[:100],
                    }
                self.send_body(
                    200,
                    json_bytes(payload),
                    "application/json; charset=utf-8",
                )
                return
            if path == "/health":
                with state_lock:
                    payload = {
                        "ok": state["status"] == "protected",
                        "status": state["status"],
                        "last_update": state["last_update"],
                        "firewall": dict(state["firewall"]),
                        "time": now_iso(),
                    }
                self.send_body(
                    200,
                    json_bytes(payload),
                    "application/json; charset=utf-8",
                )
                return
            self.send_body(
                404,
                b"Not found",
                "text/plain; charset=utf-8",
            )
        except Exception as exc:
            logger.exception("dashboard request failed: %s", exc)
            self.send_body(
                500,
                b"Internal Server Error",
                "text/plain; charset=utf-8",
            )


def dashboard_server():
    server = ThreadingHTTPServer(
        (str(CONFIG["host"]), int(CONFIG["port"])),
        DashboardHandler,
    )
    server.daemon_threads = True
    server.timeout = 1
    logger.info(
        "dashboard listening on %s:%s",
        CONFIG["host"],
        CONFIG["port"],
    )
    try:
        while not stop_event.is_set():
            server.handle_request()
    finally:
        server.server_close()


def main():
    monitor = threading.Thread(
        target=worker,
        name="rgnodes-monitor",
        daemon=True,
    )
    monitor.start()
    try:
        dashboard_server()
    except KeyboardInterrupt:
        logger.info("shutdown requested")
    finally:
        stop_event.set()
        monitor.join(timeout=5)


if __name__ == "__main__":
    main()
