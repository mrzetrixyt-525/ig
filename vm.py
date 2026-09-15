from __future__ import annotations

import asyncio
import contextlib
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import signal
import shutil
import socket
import ipaddress
import sqlite3
import sys
import json
import time
import random
import zipfile
import tempfile
import aiohttp
import secrets
try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None
import string
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit, quote

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ================================================================
# RGNODES™ VPS Management Bot — hardened/stable build
# UI/command names are intentionally kept compatible with the build
# supplied by the user.
# ================================================================

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def normalize_bot_token(raw: str | None) -> str:
    """Return a clean Discord bot token without exposing it in logs."""
    token = str(raw or "").strip()
    if not token:
        return ""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        token = token[1:-1].strip()
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    token = token.replace("\r", "").replace("\n", "").strip()
    return token


def load_discord_token() -> tuple[str, str]:
    """Return (token, source), supporting common environment variable names."""
    for name in ("TOKEN", "DISCORD_TOKEN", "BOT_TOKEN"):
        token = normalize_bot_token(os.getenv(name))
        if token:
            return token, name
    return "", "none"


TOKEN, TOKEN_SOURCE = load_discord_token()
# Native-Docker guest bootstrap settings.
# Kept near the top because function default arguments are evaluated when the
# function is defined, not when it is called.
GUEST_SYSTEMD_ENABLED = env_bool("GUEST_SYSTEMD_ENABLED", True)
GUEST_NESTED_DOCKER = env_bool("GUEST_NESTED_DOCKER", True)
GUEST_REQUIRE_NESTED_DOCKER = env_bool("GUEST_REQUIRE_NESTED_DOCKER", False)
GUEST_PERSIST_SYSTEM_DIRS = env_bool("GUEST_PERSIST_SYSTEM_DIRS", False)
GUEST_SYSTEMD_PRIVILEGED = env_bool("GUEST_SYSTEMD_PRIVILEGED", True)
GUEST_CGROUPNS_HOST = env_bool("GUEST_CGROUPNS_HOST", True)
GUEST_INSTALL_WINGS = env_bool("GUEST_INSTALL_WINGS", True)
GUEST_REQUIRE_WINGS = env_bool("GUEST_REQUIRE_WINGS", False)
GUEST_INSTALL_WEB_STACK = env_bool("GUEST_INSTALL_WEB_STACK", True)
GUEST_INSTALL_DATABASE_STACK = env_bool("GUEST_INSTALL_DATABASE_STACK", True)
GUEST_INSTALL_KVM_LIBVIRT = env_bool("GUEST_INSTALL_KVM_LIBVIRT", True)
GUEST_BOOTSTRAP_TIMEOUT = env_int("GUEST_BOOTSTRAP_TIMEOUT", 1200, 120, 1800)
GUEST_DOCKER_PACKAGE = os.getenv("GUEST_DOCKER_PACKAGE", "docker.io").strip() or "docker.io"
GUEST_PERSISTENT_DATA = env_bool("GUEST_PERSISTENT_DATA", True)
SSH_PASSWORD_LENGTH = env_int("SSH_PASSWORD_LENGTH", 20, 12, 48)
AUTO_CREATE_SSH_FORWARD = env_bool("AUTO_CREATE_SSH_FORWARD", True)
SSH_FORWARD_PORT_START = env_int("SSH_FORWARD_PORT_START", 22000, 1024, 65530)
SSH_FORWARD_PORT_END = env_int("SSH_FORWARD_PORT_END", 29999, SSH_FORWARD_PORT_START, 65535)

ADMIN_ID = env_int("ADMIN_ID", 0, 0)
def resolve_project_path(raw: str, default_name: str) -> str:
    value = str(raw or default_name).strip() or default_name
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (PROJECT_ROOT / path).resolve())

DATABASE_FILE = resolve_project_path(os.getenv("DATABASE_FILE", "vps_bot.db"), "vps_bot.db")
LOG_FILE = resolve_project_path(os.getenv("LOG_FILE", "vps_bot.log"), "vps_bot.log")
BOT_STATUS_NAME = os.getenv("BOT_STATUS_NAME", "RGNODES™ VPS Management").strip() or "RGNODES™ VPS Management"
PREFIX = (os.getenv("PREFIX") or "-").strip() or "-"
VPS_HOSTNAME_PREFIX = os.getenv("VPS_HOSTNAME_PREFIX", "rgnodes-vps").strip() or "rgnodes-vps"
DEFAULT_RAM = os.getenv("DEFAULT_RAM", "4G").strip() or "4G"
DEFAULT_CPU = os.getenv("DEFAULT_CPU", "1").strip() or "1"
DEFAULT_DISK = os.getenv("DEFAULT_DISK", "10G").strip() or "10G"
DEFAULT_LOCATION = os.getenv("DEFAULT_LOCATION", "SG").strip().upper() or "SG"
if DEFAULT_LOCATION not in {"SG", "IN"}:
    DEFAULT_LOCATION = "SG"
SERVER_LIMIT = env_int("SERVER_LIMIT", 1, 1, 100)
TOTAL_RUNNING_LIMIT = env_int("TOTAL_RUNNING_LIMIT", 1000, 1, 10_000)
ADMIN_BYPASS_LIMITS = env_bool("ADMIN_BYPASS_LIMITS", True)
STATUS_INTERVAL = env_int("STATUS_INTERVAL", 45, 15, 300)
DOCKER_TIMEOUT = env_int("DOCKER_TIMEOUT", 120, 30, 900)
ACCESS_TIMEOUT = env_int("ACCESS_TIMEOUT", 120, 30, 300)
DEPLOY_TIMEOUT = env_int("DEPLOY_TIMEOUT", 1500, 180, 2400)
IMAGE_PULL_TIMEOUT = env_int("IMAGE_PULL_TIMEOUT", 300, 60, 600)
INTERACTION_LOG_UNKNOWN_AS_DEBUG = env_bool("INTERACTION_LOG_UNKNOWN_AS_DEBUG", True)
ENABLE_HARD_DISK_QUOTA = env_bool("ENABLE_HARD_DISK_QUOTA", False)
QUOTA_FALLBACK = env_bool("QUOTA_FALLBACK", True)
DOCKER_FEATURE_FALLBACK = env_bool("DOCKER_FEATURE_FALLBACK", True)
DOCKER_RUNTIME_PREFLIGHT = env_bool("DOCKER_RUNTIME_PREFLIGHT", True)
DOCKER_RETRIES = env_int("DOCKER_RETRIES", 2, 0, 5)
STATUS_CONCURRENCY = env_int("STATUS_CONCURRENCY", 5, 1, 25)
MEMORY_RESERVATION_PERCENT = env_int("MEMORY_RESERVATION_PERCENT", 75, 0, 100)
DISABLE_CONTAINER_SWAP = env_bool("DISABLE_CONTAINER_SWAP", True)
# Never start/reconfigure a host Docker daemon implicitly. This is especially
# important when RGNODES itself runs under Pterodactyl/Wings or another supervisor.
MANAGE_DOCKER_DAEMON = env_bool("MANAGE_DOCKER_DAEMON", False)
AUTO_INSTALL_DOCKER = env_bool("AUTO_INSTALL_DOCKER", True)
AUTO_REPAIR_DOCKER = env_bool("AUTO_REPAIR_DOCKER", True)
DISCORD_API_TIMEOUT = env_int("DISCORD_API_TIMEOUT", 10, 3, 30)
PROGRESS_UPDATE_TIMEOUT = env_int("PROGRESS_UPDATE_TIMEOUT", 5, 2, 20)
SSHX_TOTAL_TIMEOUT = env_int("SSHX_TOTAL_TIMEOUT", 100, 30, 240)
SSHX_START_TIMEOUT = env_int("SSHX_START_TIMEOUT", 45, 15, 120)
SSHX_POLL_SECONDS = env_int("SSHX_POLL_SECONDS", 30, 5, 90)
HOST_TOTAL_RAM = os.getenv("HOST_TOTAL_RAM", "64G").strip() or "64G"
HOST_TOTAL_CPU = env_int("HOST_TOTAL_CPU", 10, 1, 256)
HOST_TOTAL_DISK = os.getenv("HOST_TOTAL_DISK", "10T").strip() or "10T"
MAX_PORTS_PER_VPS = env_int("MAX_PORTS_PER_VPS", 10, 1, 50)
PORT_RANGE_START = env_int("PORT_RANGE_START", 20000, 1024, 65534)
PORT_RANGE_END = env_int("PORT_RANGE_END", 40000, 1025, 65535)
PORT_SUPERVISOR_INTERVAL = env_int("PORT_SUPERVISOR_INTERVAL", 20, 5, 120)
PUBLIC_IP_REFRESH = env_int("PUBLIC_IP_REFRESH", 300, 60, 3600)
REAL_LOCATION_REFRESH = env_int("REAL_LOCATION_REFRESH", 900, 120, 7200)
IPV4_MODE = os.getenv("IPV4_MODE", "shared").strip().lower() or "shared"
if IPV4_MODE not in {"shared"}:
    IPV4_MODE = "shared"
REQUIRE_REAL_PUBLIC_IPV4 = env_bool("REQUIRE_REAL_PUBLIC_IPV4", True)
IPV4_REFRESH = env_int("IPV4_REFRESH", 300, 30, 3600)

# Discord startup/network resilience. A failed HTTPS handshake should not
# terminate the whole service; the health endpoint and worker remain alive
# while Discord login is retried with exponential backoff.
DISCORD_LOGIN_RETRY_BASE = env_int("DISCORD_LOGIN_RETRY_BASE", 5, 1, 60)
DISCORD_LOGIN_RETRY_MAX = env_int("DISCORD_LOGIN_RETRY_MAX", 120, 10, 600)
DISCORD_LOGIN_MAX_ATTEMPTS = env_int("DISCORD_LOGIN_MAX_ATTEMPTS", 0, 0, 1000)

# Hosting-platform health/keep-alive HTTP listener. Most platforms (Render,
# Railway, etc.) provide PORT automatically; locally it falls back to 247.
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0").strip() or "0.0.0.0"
# Never let the bot health server occupy Pterodactyl Wings/SFTP ports on a VM.
# Hosting platforms can opt into their injected PORT explicitly.
USE_PLATFORM_PORT = env_bool("USE_PLATFORM_PORT", False)
WEB_RESERVED_PORTS = {2022, 8080, 8443}
_requested_web_port = env_int("PORT", 247, 1, 65535) if USE_PLATFORM_PORT else env_int("WEB_PORT", 247, 1, 65535)
WEB_PORT = 247 if _requested_web_port in WEB_RESERVED_PORTS else _requested_web_port
WEB_PATH = os.getenv("WEB_PATH", "/").strip() or "/"
TOTAL_CREATE_LIMIT_DEFAULT = env_int("TOTAL_CREATE_LIMIT", 1000, 1, 1000)
DEPLOY_COST = env_int("DEPLOY_COST", 1000, 0, 1000000)
SLOT_PURCHASE_COST = env_int("SLOT_PURCHASE_COST", 1000, 1, 1000000)
MAX_USER_SLOTS = env_int("MAX_USER_SLOTS", 25, 1, 1000)
DEPLOY_COOLDOWN_SECONDS = env_int("DEPLOY_COOLDOWN_SECONDS", 15, 0, 3600)
BOT_VERSION = os.getenv("BOT_VERSION", "1 Pro").strip() or "1 Pro"
HOSTING_NAME = os.getenv("HOSTING_NAME", "RGNODES™").strip() or "RGNODES™"

# VPS backend selection:
#   docker        -> native Docker backend (default, works without Pterodactyl)
#   pterodactyl   -> create/control servers through Pterodactyl Application API
# VM creation is always local. Pterodactyl support means the guest is
# provisioned so Panel/Wings can be installed INSIDE the guest; the bot never
# redirects VM creation through the Pterodactyl Application API.
VPS_BACKEND = "docker"
SSHX_CUSTOM_SCRIPT_URL = os.getenv(
    "SSHX_CUSTOM_SCRIPT_URL",
    "https://raw.githubusercontent.com/mrzetrixyt-525/sshx/main/sshx%20by%20rgnodes.sh",
).strip()

PTERO_URL = os.getenv("PTERO_URL", "").strip().rstrip("/")
PTERO_API_KEY = (os.getenv("PTERO_API_KEY") or os.getenv("PTERODACTYL_APPLICATION_API_KEY") or "").strip()
PTERO_CLIENT_API_KEY = (os.getenv("PTERO_CLIENT_API_KEY") or os.getenv("PTERODACTYL_CLIENT_API_KEY") or "").strip()
PTERO_PANEL_PUBLIC_URL = os.getenv("PTERO_PANEL_PUBLIC_URL", PTERO_URL).strip().rstrip("/")
PTERO_NODE_ID = env_int("PTERO_NODE_ID", 0, 0)
PTERO_NEST_ID = env_int("PTERO_NEST_ID", 0, 0)
PTERO_EGG_ID = env_int("PTERO_EGG_ID", 0, 0)
PTERO_ALLOCATION_ID = env_int("PTERO_ALLOCATION_ID", 0, 0)
PTERO_DEFAULT_USER_ID = env_int("PTERO_DEFAULT_USER_ID", 0, 0)
PTERO_AUTO_CREATE_USERS = env_bool("PTERO_AUTO_CREATE_USERS", True)
PTERO_MEMORY_SWAP = env_int("PTERO_MEMORY_SWAP", 0)
PTERO_IO = env_int("PTERO_IO", 500, 10, 1000)
PTERO_DATABASES = env_int("PTERO_DATABASES", 0, 0, 100)
PTERO_ALLOCATIONS = env_int("PTERO_ALLOCATIONS", 1, 1, 100)
PTERO_BACKUPS = env_int("PTERO_BACKUPS", 5, 0, 100)
PTERO_DOCKER_IMAGE = os.getenv("PTERO_DOCKER_IMAGE", "").strip()
PTERO_STARTUP = os.getenv("PTERO_STARTUP", "").strip()
PTERO_SKIP_SCRIPTS = env_bool("PTERO_SKIP_SCRIPTS", False)
PTERO_ENVIRONMENT_JSON = os.getenv("PTERO_ENVIRONMENT_JSON", "{}").strip() or "{}"
try:
    PTERO_ENVIRONMENT = json.loads(PTERO_ENVIRONMENT_JSON)
    if not isinstance(PTERO_ENVIRONMENT, dict):
        PTERO_ENVIRONMENT = {}
except (TypeError, ValueError, json.JSONDecodeError):
    PTERO_ENVIRONMENT = {}

def ptero_application_configured() -> bool:
    return bool(
        PTERO_URL and PTERO_API_KEY and PTERO_DEFAULT_USER_ID
        and PTERO_NODE_ID and PTERO_NEST_ID and PTERO_EGG_ID
        and PTERO_ALLOCATION_ID
    )


def ptero_client_configured() -> bool:
    return bool(PTERO_URL and PTERO_CLIENT_API_KEY)


def ptero_configured() -> bool:
    return ptero_application_configured() and ptero_client_configured()

def active_backend() -> str:
    # The bot creates the guest locally. Pterodactyl support means the guest
    # is provisioned with Panel/Wings prerequisites; it must not silently
    # switch a VM creation request into a Pterodactyl API server.
    return "docker"

if not WEB_PATH.startswith("/"):
    WEB_PATH = "/" + WEB_PATH

LOCATION_CONFIG = {
    "SG": {"label": "Singapore 🇸🇬", "short": "SG"},
    "IN": {"label": "India 🇮🇳", "short": "IN"},
}

OS_CONFIG = {
    "ubuntu-26.04": {"label": "Ubuntu 26.04 LTS", "image": "ubuntu:26.04"},
    "ubuntu-24.04": {"label": "Ubuntu 24.04 LTS", "image": "ubuntu:24.04"},
    "ubuntu-22.04": {"label": "Ubuntu 22.04 LTS", "image": "ubuntu:22.04"},
    "debian-13": {"label": "Debian 13", "image": "debian:13"},
    "debian-12": {"label": "Debian 12", "image": "debian:12"},
    "debian-11": {"label": "Debian 11", "image": "debian:11.11"},
}

LOCATION_ALIASES = {
    "sg": "SG",
    "singapore": "SG",
    "in": "IN",
    "india": "IN",
}

OS_ALIASES = {
    "ubuntu": "ubuntu-24.04", "ubuntu26": "ubuntu-26.04", "ubuntu26.04": "ubuntu-26.04", "ubuntu-26.04": "ubuntu-26.04",
    "ubuntu24": "ubuntu-24.04", "ubuntu24.04": "ubuntu-24.04", "ubuntu-24.04": "ubuntu-24.04",
    "ubuntu22": "ubuntu-22.04", "ubuntu22.04": "ubuntu-22.04", "ubuntu-22.04": "ubuntu-22.04",
    "debian": "debian-12", "debian13": "debian-13", "debian-13": "debian-13",
    "debian12": "debian-12", "debian-12": "debian-12",
    "debian11": "debian-11", "debian-11": "debian-11",
}

ACCESS_URL_RE = re.compile(r"https://sshx\.io/s/[A-Za-z0-9_-]+(?:#[A-Za-z0-9_=-]+)?", re.IGNORECASE)
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("rgnodes")

# Prevent multiple copies of the same bot from connecting with the same token.
# Two gateway sessions will both receive the same message and cause duplicate
# replies. This lock is held for the complete process lifetime.
SINGLETON_LOCK_FILE = Path(os.getenv("SINGLETON_LOCK_FILE", "/tmp/rgnodes-vm.lock")).expanduser()
_SINGLETON_HANDLE = None

def _same_script_process(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except (OSError, ValueError):
        return False
    return str(Path(__file__).resolve()) in raw

def acquire_singleton() -> None:
    """Acquire a process-wide lock and refuse a second bot instance.

    Never terminate another process from inside the application. A restart
    supervisor/systemd/pm2 should own process lifecycle; killing a peer here
    can create a short overlap where both gateway sessions receive events.
    """
    global _SINGLETON_HANDLE
    SINGLETON_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = SINGLETON_LOCK_FILE.open("a+")
    if fcntl is None:
        handle.close()
        raise SystemExit("fcntl is unavailable; refusing to start without duplicate-process protection.")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise SystemExit("Another RGNODES bot process is already running. Stop the existing copy before starting a new one.")
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _SINGLETON_HANDLE = handle
    logger.info("RGNODES singleton lock acquired (pid=%s).", os.getpid())

def release_singleton() -> None:
    global _SINGLETON_HANDLE
    handle = _SINGLETON_HANDLE
    _SINGLETON_HANDLE = None
    if handle is None:
        return
    with contextlib.suppress(Exception):
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    with contextlib.suppress(Exception):
        handle.close()


# ================================================================
# Lightweight 24/7 HTTP health/landing server
# ================================================================
# This server intentionally uses only Python's asyncio standard library so the
# existing dependency set does not need Flask/FastAPI/aiohttp. It runs in the
# same event loop as discord.py and therefore stays alive for the full process
# lifetime. Hosting platforms can probe the assigned PORT and receive HTTP 200.
WEB_SERVER: asyncio.AbstractServer | None = None
WEB_SERVER_TASK: asyncio.Task[None] | None = None
WEB_STARTED_AT = datetime.now(timezone.utc)


def _html_escape(value: Any) -> str:
    text = str(value)
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;"))


def _health_html() -> bytes:
    discord_online = bool(bot.is_ready()) if "bot" in globals() else False
    bot_name = str(bot.user) if discord_online and getattr(bot, "user", None) else "Starting…"
    state = "Online" if discord_online else "Starting"
    started = WEB_STARTED_AT.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    body = f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<meta http-equiv=\"refresh\" content=\"30\">
<title>RGNODES™ • Bot Status</title>
<style>
body{{margin:0;min-height:100vh;background:#111318;color:#f5f7fa;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;display:grid;place-items:center}}
.card{{width:min(680px,calc(100% - 40px));padding:34px;border:1px solid #2a2e38;border-radius:20px;background:#181b22;box-shadow:0 20px 70px rgba(0,0,0,.35)}}
.badge{{display:inline-flex;align-items:center;gap:8px;padding:7px 12px;border-radius:999px;background:#20252e;font-size:14px}}
.dot{{width:9px;height:9px;border-radius:50%;background:#48d597;box-shadow:0 0 14px #48d597}}
h1{{margin:18px 0 10px;font-size:34px}}
p{{color:#aeb6c4;line-height:1.6}}
.row{{display:flex;justify-content:space-between;gap:18px;margin-top:22px;padding-top:18px;border-top:1px solid #2a2e38}}
code{{color:#dfe5ee}}
</style>
</head>
<body>
<main class=\"card\">
<div class=\"badge\"><span class=\"dot\"></span>Bot is <strong>{_html_escape(state)}</strong></div>
<h1>RGNODES™ Bot is online…</h1>
<p>The 24/7 health endpoint is running and ready for hosting-platform health checks. Opening or pinging this port confirms the process is serving HTTP.</p>
<div class=\"row\"><span>Status</span><strong>{_html_escape(state)}</strong></div>
<div class=\"row\"><span>Discord</span><strong>{_html_escape(bot_name)}</strong></div>
<div class=\"row\"><span>Started</span><code>{_html_escape(started)}</code></div>
</main>
</body>
</html>
"""
    return body.encode("utf-8")


async def _http_health_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    try:
        # Read only the request headers, with a hard cap to avoid oversized or
        # slowloris-style requests taking resources from the Discord bot.
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            return
        if len(request) > 16 * 1024:
            return

        first_line = request.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = first_line.split()
        method = parts[0].upper() if parts else ""
        target = parts[1] if len(parts) >= 2 else "/"
        path = target.split("?", 1)[0].split("#", 1)[0]

        if method not in {"GET", "HEAD"}:
            payload = b"Method Not Allowed\n"
            headers = (
                b"HTTP/1.1 405 Method Not Allowed\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                + f"Content-Length: {len(payload)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
            )
        elif path in {"/", WEB_PATH, "/health", "/healthz", "/ping"}:
            if path in {"/health", "/healthz"}:
                discord_online = bool(bot.is_ready())
                payload = (
                    ("{\"status\":\"online\",\"discord_ready\":" + ("true" if discord_online else "false") + "}")
                    .encode("utf-8")
                )
                content_type = b"application/json; charset=utf-8"
            else:
                payload = _health_html()
                content_type = b"text/html; charset=utf-8"
            headers = (
                b"HTTP/1.1 200 OK\r\n"
                + b"Content-Type: " + content_type + b"\r\n"
                + f"Content-Length: {len(payload)}\r\n".encode()
                + b"Cache-Control: no-store, no-cache, must-revalidate\r\n"
                + b"Connection: close\r\n\r\n"
            )
        else:
            payload = b"Not Found\n"
            headers = (
                b"HTTP/1.1 404 Not Found\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                + f"Content-Length: {len(payload)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
            )

        writer.write(headers)
        if method != "HEAD":
            writer.write(payload)
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    except Exception as exc:
        logger.debug("Health client error from %s: %s", peer, safe_log(exc))
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def start_health_server() -> asyncio.AbstractServer | None:
    global WEB_SERVER, WEB_SERVER_TASK
    if WEB_SERVER is not None:
        return WEB_SERVER
    try:
        last_error = None
        candidates: list[int] = []
        for candidate in [WEB_PORT, *range(WEB_PORT + 1, min(WEB_PORT + 11, 65536))]:
            if candidate in WEB_RESERVED_PORTS or candidate in candidates:
                continue
            candidates.append(candidate)
        for candidate in candidates:
            try:
                WEB_SERVER = await asyncio.start_server(
                    _http_health_client,
                    host=WEB_HOST,
                    port=candidate,
                    limit=16 * 1024,
                    reuse_address=True,
                )
                if candidate != WEB_PORT:
                    logger.warning("Health port %s is busy; using fallback port %s.", WEB_PORT, candidate)
                break
            except OSError as exc:
                last_error = exc
        if WEB_SERVER is None:
            raise last_error or OSError("No health port available")
        WEB_SERVER_TASK = asyncio.create_task(
            WEB_SERVER.serve_forever(),
            name="rgnodes-http-health",
        )
        sockets = WEB_SERVER.sockets or []
        bound = ", ".join(str(sock.getsockname()) for sock in sockets) or f"{WEB_HOST}:{WEB_PORT}"
        logger.info("24/7 HTTP health server online at %s | GET / or /health", bound)
        return WEB_SERVER
    except (OSError, asyncio.CancelledError) as exc:
        WEB_SERVER = None
        WEB_SERVER_TASK = None
        if isinstance(exc, asyncio.CancelledError):
            raise
        logger.error("Could not start HTTP health server on %s:%s: %s", WEB_HOST, WEB_PORT, safe_log(exc))
        return None


async def stop_health_server() -> None:
    global WEB_SERVER, WEB_SERVER_TASK
    server, task = WEB_SERVER, WEB_SERVER_TASK
    WEB_SERVER = None
    WEB_SERVER_TASK = None
    if server is not None:
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def safe_log(value: Any, limit: int = 1800) -> str:
    text = str(value)
    # Preserve the useful SSHx session ID while redacting only the browser-side
    # E2E key fragment. Logging the full fragment would expose the private console.
    text = re.sub(
        r"(https://sshx\.io/s/[A-Za-z0-9_-]+)#([^\s<>\]\[\"']+)",
        r"\1#<e2e-key-redacted>",
        text,
        flags=re.I,
    )
    text = re.sub(r"Bearer\s+\S+", "Bearer <redacted>", text, flags=re.I)
    text = re.sub(r"ssh\s+\S+@\S+", "ssh <redacted>", text, flags=re.I)
    return text[:max(1, int(limit))]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_os(value: str | None) -> str | None:
    return OS_ALIASES.get((value or "").strip().lower())


def os_label(value: str | None) -> str:
    normalized = normalize_os(value) or (value or "Unknown")
    return OS_CONFIG.get(normalized, {"label": normalized})["label"]


def normalize_location(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if raw in LOCATION_ALIASES:
        return LOCATION_ALIASES[raw]
    key = raw.upper()
    return key if key in LOCATION_CONFIG else None


def location_label(value: str | None) -> str:
    return LOCATION_CONFIG[(normalize_location(value) or DEFAULT_LOCATION)]["label"]


def clean(value: Any, limit: int = 1024) -> str:
    return str(value if value is not None else "N/A").replace("`", "'")[:limit]


def parse_size_bytes(value: str) -> int:
    text = str(value or "").strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(b|kb|k|mb|m|gb|g|tb|t)?", text)
    if not match:
        raise ValueError(f"Invalid resource value: {value}")
    amount = float(match.group(1))
    unit = match.group(2) or "g"
    multiplier = {"b": 1, "k": 1024, "kb": 1024, "m": 1024**2, "mb": 1024**2, "g": 1024**3, "gb": 1024**3, "t": 1024**4, "tb": 1024**4}[unit]
    return int(amount * multiplier)


def format_bytes(value: int) -> str:
    """Human-readable binary byte formatting for Discord dashboard values."""
    try:
        value = max(0, int(value))
    except (TypeError, ValueError):
        return "N/A"
    if value < 1024:
        return f"{value} B"
    if value < 1024**2:
        return f"{value / 1024:.0f}KB"
    if value < 1024**3:
        return f"{value / 1024**2:.1f}MB"
    if value < 1024**4:
        return f"{value / 1024**3:.2f}GB"
    return f"{value / 1024**4:.2f}TB"


def normalize_dashboard_memory(raw: str | None) -> str:
    """Normalize Docker/cgroup memory text to a stable Discord-friendly form."""
    text = str(raw or "").strip()
    if not text:
        return "N/A"
    if "/" not in text:
        return text

    left, right = (part.strip() for part in text.split("/", 1))

    def to_bytes(part: str) -> int | None:
        m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]i?b)?", part, re.I)
        if not m:
            return None
        try:
            amount = float(m.group(1))
            unit = (m.group(2) or "b").lower()
            factors = {
                "b": 1, "kb": 1000, "kib": 1024,
                "mb": 1000**2, "mib": 1024**2,
                "gb": 1000**3, "gib": 1024**3,
                "tb": 1000**4, "tib": 1024**4,
                "pb": 1000**5, "pib": 1024**5,
                "eb": 1000**6, "eib": 1024**6,
            }
            return int(amount * factors[unit])
        except (KeyError, ValueError, OverflowError):
            return None

    used = to_bytes(left)
    limit = to_bytes(right)
    if used is not None and limit is not None:
        return f"{format_bytes(used)} / {format_bytes(limit)}"
    return text


def normalize_network_stats(raw: str | None) -> str:
    """Render Docker NetIO as download/upload without changing the measured values."""
    text = str(raw or "").strip()
    if not text:
        return "N/A"
    if " / " in text:
        left, right = text.split(" / ", 1)
        return f"{left.strip()} ↓ / {right.strip()} ↑"
    return text


def validate_resources(ram: str, cpu: str, disk: str) -> tuple[str, str, str]:
    try:
        ram_bytes = parse_size_bytes(ram)
        disk_bytes = parse_size_bytes(disk)
        cpu_value = float(str(cpu).strip())
    except (TypeError, ValueError):
        raise ValueError("RAM, CPU, or disk format is invalid. Example: `2g`, `2`, `10g`.")
    min_ram = runtime_size_bytes("min_ram") if "RUNTIME_CONFIG_SPEC" in globals() else 256 * 1024**2
    max_ram = runtime_size_bytes("max_ram") if "RUNTIME_CONFIG_SPEC" in globals() else 256 * 1024**3
    min_disk = runtime_size_bytes("min_disk") if "RUNTIME_CONFIG_SPEC" in globals() else 1 * 1024**3
    max_disk = runtime_size_bytes("max_disk") if "RUNTIME_CONFIG_SPEC" in globals() else 10 * 1024**4
    min_cpu = runtime_value("min_cpu") if "RUNTIME_CONFIG_SPEC" in globals() else 0.1
    max_cpu = runtime_value("max_cpu") if "RUNTIME_CONFIG_SPEC" in globals() else 64
    if not min_ram <= ram_bytes <= max_ram:
        raise ValueError(f"RAM must be between {format_bytes(min_ram)} and {format_bytes(max_ram)}.")
    if not min_disk <= disk_bytes <= max_disk:
        raise ValueError(f"Disk must be between {format_bytes(min_disk)} and {format_bytes(max_disk)}.")
    if not float(min_cpu) <= cpu_value <= float(max_cpu):
        raise ValueError(f"CPU must be between {min_cpu:g} and {max_cpu:g} cores.")
    ram = str(ram).strip().lower()
    cpu = f"{cpu_value:g}"
    disk = str(disk).strip().lower()
    return ram, cpu, disk


# ================================================================
# SQLite — serialized writes + resilient migration
# ================================================================

DB_WRITE_LOCK = asyncio.Lock()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_FILE, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db() -> None:
    conn = db_connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                container_id TEXT UNIQUE NOT NULL,
                container_name TEXT NOT NULL,
                os_type TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT 'SG',
                hostname TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'stopped',
                ssh_command TEXT,
                ram TEXT NOT NULL DEFAULT '2g',
                cpu TEXT NOT NULL DEFAULT '1',
                disk TEXT NOT NULL DEFAULT '10g',
                suspended INTEGER NOT NULL DEFAULT 0,
                sshx_url TEXT,
                sshx_pid TEXT,
                ssh_password TEXT,
                critical INTEGER NOT NULL DEFAULT 0,
                public_ipv4 TEXT,
                ipv4_verified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vps_shares (
                vps_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                shared_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                access_level TEXT NOT NULL DEFAULT 'manage',
                PRIMARY KEY (vps_id, user_id),
                FOREIGN KEY(vps_id) REFERENCES vps(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_slots (
                user_id INTEGER PRIMARY KEY,
                slots INTEGER NOT NULL DEFAULT 1 CHECK(slots > 0),
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS economy (
                user_id INTEGER PRIMARY KEY,
                wallet INTEGER NOT NULL DEFAULT 0 CHECK(wallet >= 0),
                bank INTEGER NOT NULL DEFAULT 0 CHECK(bank >= 0),
                invites INTEGER NOT NULL DEFAULT 0 CHECK(invites >= 0),
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS economy_cooldowns (
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                next_at TEXT NOT NULL,
                PRIMARY KEY(user_id, action)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS redeem_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                reward_coins INTEGER NOT NULL DEFAULT 0 CHECK(reward_coins >= 0),
                reward_slots INTEGER NOT NULL DEFAULT 0 CHECK(reward_slots >= 0),
                max_uses INTEGER NOT NULL DEFAULT 1 CHECK(max_uses >= 0),
                uses INTEGER NOT NULL DEFAULT 0 CHECK(uses >= 0),
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS redeem_claims (
                code_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                claimed_at TEXT NOT NULL,
                PRIMARY KEY(code_id, user_id),
                FOREIGN KEY(code_id) REFERENCES redeem_codes(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                price INTEGER NOT NULL CHECK(price >= 0),
                status TEXT NOT NULL DEFAULT 'active',
                ram TEXT NOT NULL DEFAULT '8g',
                cpu TEXT NOT NULL DEFAULT '2',
                disk TEXT NOT NULL DEFAULT '25g',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plan_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                location TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 22,
                status TEXT NOT NULL DEFAULT 'online',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_suspensions (
                user_id INTEGER PRIMARY KEY,
                until_at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT 'Administrative suspension'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS security_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("INSERT OR IGNORE INTO security_settings(key,value) VALUES('anti_hacking','0')")
        conn.execute("INSERT OR IGNORE INTO security_settings(key,value) VALUES('total_create_limit',?)", (str(TOTAL_CREATE_LIMIT_DEFAULT),))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_by INTEGER,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_processed_events_created_at ON processed_events(created_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vps_ports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vps_id INTEGER NOT NULL,
                container_port INTEGER NOT NULL,
                host_port INTEGER NOT NULL UNIQUE,
                protocol TEXT NOT NULL DEFAULT 'tcp',
                target_ip TEXT,
                pid INTEGER,
                status TEXT NOT NULL DEFAULT 'stopped',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(vps_id, container_port, protocol),
                FOREIGN KEY(vps_id) REFERENCES vps(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS vps_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vps_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                image_ref TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(vps_id, name),
                FOREIGN KEY(vps_id) REFERENCES vps(id) ON DELETE CASCADE
            )
        """)
        def cols(table: str) -> set[str]:
            return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        vps_cols = cols("vps")
        for name, ddl in {
            "location": "ALTER TABLE vps ADD COLUMN location TEXT NOT NULL DEFAULT 'SG'",
            "sshx_url": "ALTER TABLE vps ADD COLUMN sshx_url TEXT",
            "sshx_pid": "ALTER TABLE vps ADD COLUMN sshx_pid TEXT",
            "ssh_password": "ALTER TABLE vps ADD COLUMN ssh_password TEXT",
            "critical": "ALTER TABLE vps ADD COLUMN critical INTEGER NOT NULL DEFAULT 0",
            "public_ipv4": "ALTER TABLE vps ADD COLUMN public_ipv4 TEXT",
            "ipv4_verified_at": "ALTER TABLE vps ADD COLUMN ipv4_verified_at TEXT",
            "backend": "ALTER TABLE vps ADD COLUMN backend TEXT NOT NULL DEFAULT 'docker'",
            "ptero_server_id": "ALTER TABLE vps ADD COLUMN ptero_server_id INTEGER",
            "ptero_identifier": "ALTER TABLE vps ADD COLUMN ptero_identifier TEXT",
            "ptero_user_id": "ALTER TABLE vps ADD COLUMN ptero_user_id INTEGER",
        }.items():
            if name not in vps_cols:
                conn.execute(ddl)
        # Refresh the schema view after ALTER TABLE operations.
        vps_cols = cols("vps")
        required_vps = {
            "user_id", "container_id", "container_name", "os_type", "location",
            "hostname", "status", "ram", "cpu", "disk", "sshx_url", "sshx_pid",
            "public_ipv4", "ipv4_verified_at", "created_at", "updated_at", "ssh_password", "critical",
            "backend", "ptero_server_id", "ptero_identifier", "ptero_user_id",
        }
        missing_vps = sorted(required_vps - vps_cols)
        if missing_vps:
            raise RuntimeError(f"SQLite VPS schema is incomplete; missing columns: {', '.join(missing_vps)}")

        share_cols = cols("vps_shares")
        if "access_level" not in share_cols:
            conn.execute("ALTER TABLE vps_shares ADD COLUMN access_level TEXT NOT NULL DEFAULT 'manage'")
        share_cols = cols("vps_shares")
        if "shared_by" not in share_cols:
            conn.execute("ALTER TABLE vps_shares ADD COLUMN shared_by INTEGER")
        if "created_at" not in share_cols:
            conn.execute("ALTER TABLE vps_shares ADD COLUMN created_at TEXT")

        if "updated_at" not in cols("users"):
            conn.execute("ALTER TABLE users ADD COLUMN updated_at TEXT")
        if "created_at" not in cols("bans"):
            conn.execute("ALTER TABLE bans ADD COLUMN created_at TEXT")
        now = utc_now()
        conn.execute("UPDATE users SET updated_at=COALESCE(updated_at,created_at,?)", (now,))
        conn.execute("UPDATE vps SET location=COALESCE(location,'SG'),updated_at=COALESCE(updated_at,created_at,?)", (now,))
        conn.execute("UPDATE bans SET created_at=COALESCE(created_at,?)", (now,))
        conn.execute("UPDATE user_slots SET updated_at=COALESCE(updated_at,?)", (now,))
    finally:
        conn.close()


init_db()

# ---------------------------------------------------------------------------
# Runtime admin configuration
# ---------------------------------------------------------------------------
# Environment variables are safe bootstrap defaults. Admin overrides are
# persisted in SQLite and read at operation time, so pricing/limits/plans can
# be changed without editing the source or losing settings on restart.
RUNTIME_CONFIG_SPEC: dict[str, tuple[str, object, int | float | None, int | float | None]] = {
    "deploy_cost": ("int", DEPLOY_COST, 0, 1_000_000),
    "slot_price": ("int", SLOT_PURCHASE_COST, 1, 1_000_000),
    "max_user_slots": ("int", MAX_USER_SLOTS, 1, 1_000),
    "deploy_cooldown": ("int", DEPLOY_COOLDOWN_SECONDS, 0, 3600),
    "server_limit": ("int", SERVER_LIMIT, 1, 100),
    "total_running_limit": ("int", TOTAL_RUNNING_LIMIT, 1, 10_000),
    "global_create_limit": ("int", TOTAL_CREATE_LIMIT_DEFAULT, 1, 100_000),
    "max_ports_per_vps": ("int", MAX_PORTS_PER_VPS, 1, 50),
    "port_range_start": ("int", PORT_RANGE_START, 1024, 65534),
    "port_range_end": ("int", PORT_RANGE_END, 1025, 65535),
    "ssh_forward_port_start": ("int", SSH_FORWARD_PORT_START, 1024, 65530),
    "ssh_forward_port_end": ("int", SSH_FORWARD_PORT_END, 1025, 65535),
    "default_ram": ("size", DEFAULT_RAM, 256 * 1024**2, 256 * 1024**3),
    "default_cpu": ("float", DEFAULT_CPU, 0.1, 64),
    "default_disk": ("size", DEFAULT_DISK, 1 * 1024**3, 10 * 1024**4),
    "default_location": ("location", DEFAULT_LOCATION, None, None),
    "default_os": ("os", "ubuntu-24.04", None, None),
    "guest_bootstrap_timeout": ("int", GUEST_BOOTSTRAP_TIMEOUT, 120, 1800),
    "sshx_total_timeout": ("int", SSHX_TOTAL_TIMEOUT, 30, 240),
    "sshx_start_timeout": ("int", SSHX_START_TIMEOUT, 15, 120),
    "sshx_poll_seconds": ("int", SSHX_POLL_SECONDS, 5, 90),
    "reward_work_min": ("int", 25, 0, 1_000_000),
    "reward_work_max": ("int", 50, 0, 1_000_000),
    "reward_hour": ("int", 20, 0, 1_000_000),
    "reward_day": ("int", 100, 0, 1_000_000),
    "reward_week": ("int", 750, 0, 1_000_000),
    "reward_month": ("int", 3000, 0, 1_000_000),
    "reward_year": ("int", 15000, 0, 10_000_000),
    "invite_rate": ("int", 10, 0, 1_000_000),
    "quiz_reward": ("int", 50, 0, 1_000_000),
    "min_ram": ("size", "256m", 256 * 1024**2, 256 * 1024**3),
    "max_ram": ("size", "256g", 256 * 1024**2, 256 * 1024**3),
    "min_disk": ("size", "1g", 1 * 1024**3, 10 * 1024**4),
    "max_disk": ("size", "10t", 1 * 1024**3, 10 * 1024**4),
    "min_cpu": ("float", "0.1", 0.1, 64),
    "max_cpu": ("float", "64", 0.1, 64),
    "guest_systemd_enabled": ("bool", GUEST_SYSTEMD_ENABLED, None, None),
    "guest_require_nested_docker": ("bool", GUEST_REQUIRE_NESTED_DOCKER, None, None),
    "guest_persist_system_dirs": ("bool", GUEST_PERSIST_SYSTEM_DIRS, None, None),
    "guest_nested_docker": ("bool", GUEST_NESTED_DOCKER, None, None),
    "guest_install_wings": ("bool", GUEST_INSTALL_WINGS, None, None),
    "guest_install_kvm_libvirt": ("bool", GUEST_INSTALL_KVM_LIBVIRT, None, None),
    "guest_persistent_data": ("bool", GUEST_PERSISTENT_DATA, None, None),
    "auto_create_ssh_forward": ("bool", AUTO_CREATE_SSH_FORWARD, None, None),
}


def _runtime_raw(key: str) -> str | None:
    conn = db_connect()
    try:
        row = conn.execute("SELECT value FROM admin_settings WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])
    finally:
        conn.close()


def runtime_value(key: str):
    if key not in RUNTIME_CONFIG_SPEC:
        raise KeyError(key)
    kind, default, minimum, maximum = RUNTIME_CONFIG_SPEC[key]
    raw = _runtime_raw(key)
    value = default if raw is None else raw
    try:
        if kind == "int":
            value = int(str(value).strip())
        elif kind == "float":
            value = float(str(value).strip())
        elif kind == "size":
            value = str(value).strip().lower()
            parsed = parse_size_bytes(value)
            if minimum is not None and parsed < minimum:
                raise ValueError
            if maximum is not None and parsed > maximum:
                raise ValueError
        elif kind == "location":
            value = normalize_location(str(value))
            if not value:
                raise ValueError
        elif kind == "os":
            value = normalize_os(str(value))
            if not value:
                raise ValueError
        elif kind == "bool":
            low=str(value).strip().lower()
            if low not in {"true","false","1","0","yes","no","on","off"}:
                raise ValueError
            value=low in {"true","1","yes","on"}
    except Exception:
        logger.warning("Invalid persisted runtime setting %s; using fallback", key)
        return default
    if kind in {"int", "float"}:
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
    return value


def runtime_int(key: str) -> int:
    return int(runtime_value(key))


def runtime_size_bytes(key: str) -> int:
    return parse_size_bytes(str(runtime_value(key)))


def runtime_str(key: str) -> str:
    return str(runtime_value(key))


def runtime_bool(key: str) -> bool:
    return bool(runtime_value(key))


def set_runtime_value(key: str, raw_value: str, admin_id: int) -> str:
    if key not in RUNTIME_CONFIG_SPEC:
        raise KeyError(key)
    kind, _default, minimum, maximum = RUNTIME_CONFIG_SPEC[key]
    text = str(raw_value).strip()
    if not text:
        raise ValueError("Value cannot be empty.")
    if kind == "int":
        value = int(text)
        if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
            raise ValueError(f"Value must be between {minimum} and {maximum}.")
        normalized = str(value)
    elif kind == "float":
        value = float(text)
        if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
            raise ValueError(f"Value must be between {minimum} and {maximum}.")
        normalized = f"{value:g}"
    elif kind == "size":
        parsed = parse_size_bytes(text)
        if (minimum is not None and parsed < minimum) or (maximum is not None and parsed > maximum):
            raise ValueError(f"Size must be between {format_bytes(int(minimum or 0))} and {format_bytes(int(maximum or 0))}.")
        normalized = text.lower()
    elif kind == "location":
        normalized = normalize_location(text)
        if not normalized:
            raise ValueError("Location must be SG or IN.")
    elif kind == "os":
        normalized = normalize_os(text)
        if not normalized:
            raise ValueError("Unsupported OS.")
    elif kind == "bool":
        low=text.lower()
        if low not in {"true","false","1","0","yes","no","on","off"}:
            raise ValueError("Boolean value must be true/false.")
        normalized = "true" if low in {"true","1","yes","on"} else "false"
    else:
        normalized = text
    # Prevent administrators from creating impossible ranges.
    if key in {"min_ram", "max_ram"}:
        candidate=parse_size_bytes(normalized); other=runtime_size_bytes("max_ram" if key == "min_ram" else "min_ram")
        if key == "min_ram" and candidate > other: raise ValueError("min_ram cannot exceed max_ram.")
        if key == "max_ram" and candidate < other: raise ValueError("max_ram cannot be below min_ram.")
    if key in {"min_disk", "max_disk"}:
        candidate=parse_size_bytes(normalized); other=runtime_size_bytes("max_disk" if key == "min_disk" else "min_disk")
        if key == "min_disk" and candidate > other: raise ValueError("min_disk cannot exceed max_disk.")
        if key == "max_disk" and candidate < other: raise ValueError("max_disk cannot be below min_disk.")
    if key in {"min_cpu", "max_cpu"}:
        candidate=float(normalized); other=float(runtime_value("max_cpu" if key == "min_cpu" else "min_cpu"))
        if key == "min_cpu" and candidate > other: raise ValueError("min_cpu cannot exceed max_cpu.")
        if key == "max_cpu" and candidate < other: raise ValueError("max_cpu cannot be below min_cpu.")
    conn = db_connect()
    try:
        conn.execute(
            "INSERT INTO admin_settings(key,value,updated_by,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
            (key, normalized, int(admin_id), utc_now()),
        )
    finally:
        conn.close()
    return normalized


def reset_runtime_value(key: str) -> None:
    if key not in RUNTIME_CONFIG_SPEC:
        raise KeyError(key)
    conn = db_connect()
    try:
        conn.execute("DELETE FROM admin_settings WHERE key=?", (key,))
    finally:
        conn.close()


def claim_processed_event(event_id: str, kind: str, *, ttl_seconds: int = 900) -> bool:
    """Atomically claim a Discord event across all bot processes using SQLite.

    This is a second layer of duplicate protection in addition to the OS
    singleton lock. If two bot processes briefly overlap during restart, only
    the first process that inserts the event ID is allowed to handle it.
    """
    event_id = str(event_id or "").strip()
    if not event_id:
        return True
    now_dt = datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(seconds=max(60, int(ttl_seconds)))).isoformat()
    now = now_dt.isoformat()
    conn = db_connect()
    try:
        conn.execute("DELETE FROM processed_events WHERE created_at < ?", (cutoff,))
        cur = conn.execute(
            "INSERT OR IGNORE INTO processed_events(event_id,kind,created_at) VALUES(?,?,?)",
            (event_id, kind[:32], now),
        )
        return cur.rowcount == 1
    except sqlite3.Error as exc:
        # Fail closed: allowing the event through when the cross-process guard
        # is unavailable can create the exact duplicate side effect this table
        # is designed to prevent.
        logger.error("Event de-duplication check failed; blocking event: %s", safe_log(exc))
        return False
    finally:
        conn.close()


def db_upsert_user(user_id: int, username: str) -> None:
    now = utc_now()
    conn = db_connect()
    try:
        conn.execute("""
            INSERT INTO users(user_id,username,created_at,updated_at) VALUES(?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,updated_at=excluded.updated_at
        """, (user_id, username[:200], now, now))
    finally:
        conn.close()


def db_economy(user_id: int) -> sqlite3.Row:
    uid = int(user_id)
    now = utc_now()
    conn = db_connect()
    try:
        conn.execute("INSERT OR IGNORE INTO users(user_id,username,created_at,updated_at) VALUES(?,?,?,?)", (uid, f"User {uid}", now, now))
        conn.execute("INSERT OR IGNORE INTO economy(user_id,wallet,bank,invites,updated_at) VALUES(?,?,?,?,?)", (uid,0,0,0,now))
        return conn.execute("SELECT * FROM economy WHERE user_id=?", (uid,)).fetchone()
    finally:
        conn.close()


def db_balance(user_id: int) -> tuple[int,int]:
    row = db_economy(user_id)
    return int(row["wallet"]), int(row["bank"])


def db_add_coins(user_id: int, amount: int, *, wallet: bool = True) -> int:
    amount = int(amount)
    if amount < 0:
        raise ValueError("Coin amount cannot be negative.")
    db_economy(user_id)
    field = "wallet" if wallet else "bank"
    conn = db_connect()
    try:
        conn.execute(f"UPDATE economy SET {field}={field}+?,updated_at=? WHERE user_id=?", (amount, utc_now(), int(user_id)))
        return int(conn.execute("SELECT wallet+bank FROM economy WHERE user_id=?", (int(user_id),)).fetchone()[0])
    finally:
        conn.close()


def db_take_coins(user_id: int, amount: int, *, wallet: bool = True) -> bool:
    amount = int(amount)
    if amount < 0:
        return False
    db_economy(user_id)
    field = "wallet" if wallet else "bank"
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(f"SELECT {field} FROM economy WHERE user_id=?", (int(user_id),)).fetchone()
        if not row or int(row[0]) < amount:
            conn.rollback()
            return False
        conn.execute(f"UPDATE economy SET {field}={field}-?,updated_at=? WHERE user_id=?", (amount, utc_now(), int(user_id)))
        conn.commit()
        return True
    except Exception:
        with contextlib.suppress(Exception): conn.rollback()
        raise
    finally:
        conn.close()


def db_set_cooldown(user_id: int, action: str, seconds: int) -> datetime:
    when = datetime.now(timezone.utc) + timedelta(seconds=max(0,int(seconds)))
    conn = db_connect()
    try:
        conn.execute("INSERT INTO economy_cooldowns(user_id,action,next_at) VALUES(?,?,?) ON CONFLICT(user_id,action) DO UPDATE SET next_at=excluded.next_at", (int(user_id), action, when.isoformat()))
    finally:
        conn.close()
    return when


def db_cooldown_remaining(user_id: int, action: str) -> int:
    conn = db_connect()
    try:
        row = conn.execute("SELECT next_at FROM economy_cooldowns WHERE user_id=? AND action=?", (int(user_id), action)).fetchone()
    finally:
        conn.close()
    if not row:
        return 0
    try:
        return max(0, int((datetime.fromisoformat(row[0]) - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return 0


def db_total_create_limit() -> int:
    conn = db_connect()
    try:
        row = conn.execute("SELECT value FROM security_settings WHERE key='total_create_limit'").fetchone()
        return max(1, int(row[0])) if row else TOTAL_CREATE_LIMIT_DEFAULT
    finally:
        conn.close()


def db_set_total_create_limit(value: int) -> int:
    value=min(1000,max(1,int(value)))
    conn=db_connect()
    try:
        conn.execute("INSERT INTO security_settings(key,value) VALUES('total_create_limit',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(value),))
    finally:
        conn.close()
    return value


def db_is_user_suspended(user_id: int) -> tuple[bool,str]:
    conn=db_connect()
    try:
        row=conn.execute("SELECT until_at,reason FROM user_suspensions WHERE user_id=?",(int(user_id),)).fetchone()
    finally: conn.close()
    if not row: return False, ""
    try:
        until=datetime.fromisoformat(row[0])
        if until <= datetime.now(timezone.utc):
            conn=db_connect(); conn.execute("DELETE FROM user_suspensions WHERE user_id=?",(int(user_id),)); conn.close()
            return False, ""
        return True, f"until {until.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    except (TypeError,ValueError):
        return False, ""


def db_set_user_suspension(user_id: int, until: datetime, reason: str='Administrative suspension') -> None:
    conn=db_connect()
    try:
        conn.execute("INSERT INTO user_suspensions(user_id,until_at,reason) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET until_at=excluded.until_at,reason=excluded.reason",(int(user_id),until.astimezone(timezone.utc).isoformat(),reason[:200]))
    finally: conn.close()


def db_clear_user_suspension(user_id: int) -> None:
    conn=db_connect()
    try: conn.execute("DELETE FROM user_suspensions WHERE user_id=?",(int(user_id),))
    finally: conn.close()


def db_is_banned(user_id: int) -> bool:
    conn = db_connect()
    try:
        return conn.execute("SELECT 1 FROM bans WHERE user_id=?", (user_id,)).fetchone() is not None
    finally:
        conn.close()


def db_set_ban(user_id: int, banned: bool) -> None:
    conn = db_connect()
    try:
        if banned:
            conn.execute("INSERT OR IGNORE INTO bans(user_id,created_at) VALUES(?,?)", (user_id, utc_now()))
        else:
            conn.execute("DELETE FROM bans WHERE user_id=?", (user_id,))
    finally:
        conn.close()


def db_insert_vps(**data: Any) -> int:
    """Insert a VPS row using generated placeholders to prevent column/value drift.

    Returns the inserted SQLite row id.  The previous implementation used a
    hand-written VALUES list that was easy to break during schema evolution.
    """
    now = utc_now()
    fields = {
        "user_id": data["user_id"],
        "container_id": data["container_id"],
        "container_name": data["container_name"],
        "os_type": data["os_type"],
        "location": data.get("location", DEFAULT_LOCATION),
        "hostname": data["hostname"],
        "status": data.get("status", "running"),
        "ram": data["ram"],
        "cpu": data["cpu"],
        "disk": data["disk"],
        "sshx_url": data.get("sshx_url"),
        "sshx_pid": data.get("sshx_pid"),
        "ssh_password": data.get("ssh_password"),
        "critical": int(data.get("critical", 0) or 0),
        "public_ipv4": data.get("public_ipv4"),
        "ipv4_verified_at": data.get("ipv4_verified_at"),
        "created_at": data.get("created_at", now),
        "updated_at": data.get("updated_at", now),
        "backend": data.get("backend", "docker"),
        "ptero_server_id": data.get("ptero_server_id"),
        "ptero_identifier": data.get("ptero_identifier"),
        "ptero_user_id": data.get("ptero_user_id"),
    }
    columns = list(fields.keys())
    placeholders = ",".join("?" for _ in columns)
    sql = f"INSERT INTO vps ({','.join(columns)}) VALUES ({placeholders})"
    conn = db_connect()
    try:
        cur = conn.execute(sql, tuple(fields[col] for col in columns))
        return int(cur.lastrowid)
    finally:
        conn.close()


def db_get_vps(vps_id: int) -> sqlite3.Row | None:
    conn = db_connect()
    try:
        return conn.execute("SELECT * FROM vps WHERE id=?", (vps_id,)).fetchone()
    finally:
        conn.close()


def db_get_user_vps(user_id: int) -> list[sqlite3.Row]:
    conn = db_connect()
    try:
        return conn.execute("SELECT * FROM vps WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
    finally:
        conn.close()


def db_get_all_vps() -> list[sqlite3.Row]:
    conn = db_connect()
    try:
        return conn.execute("SELECT * FROM vps ORDER BY id DESC").fetchall()
    finally:
        conn.close()


def db_vps_count(user_id: int) -> int:
    conn = db_connect()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM vps WHERE user_id=?", (user_id,)).fetchone()[0])
    finally:
        conn.close()


def db_running_count() -> int:
    conn = db_connect()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM vps WHERE status='running' AND suspended=0").fetchone()[0])
    finally:
        conn.close()


def db_allocated_resources() -> tuple[int, float, int]:
    """Sum requested resources of all managed VPS records for capacity checks."""
    conn = db_connect()
    try:
        rows = conn.execute("SELECT ram,cpu,disk FROM vps").fetchall()
    finally:
        conn.close()
    ram = cpu = disk = 0.0
    for row in rows:
        try:
            ram += parse_size_bytes(row["ram"])
            disk += parse_size_bytes(row["disk"])
            cpu += float(row["cpu"])
        except (TypeError, ValueError):
            logger.warning("Ignoring malformed resource allocation while checking host capacity")
    return int(ram), cpu, int(disk)


def resource_capacity_error(ram: str, cpu: str, disk: str) -> str | None:
    try:
        request_ram = parse_size_bytes(ram)
        request_cpu = float(cpu)
        request_disk = parse_size_bytes(disk)
        total_ram = parse_size_bytes(HOST_TOTAL_RAM)
        total_disk = parse_size_bytes(HOST_TOTAL_DISK)
    except (TypeError, ValueError):
        return "Host resource capacity configuration is invalid."
    used_ram, used_cpu, used_disk = db_allocated_resources()
    if used_ram + request_ram > total_ram:
        return f"RAM capacity exceeded: requested `{ram}`, allocated `{format_bytes(used_ram)}`, host capacity `{format_bytes(total_ram)}`."
    if used_cpu + request_cpu > HOST_TOTAL_CPU:
        return f"CPU capacity exceeded: requested `{cpu}` core(s), allocated `{used_cpu:g}`, host capacity `{HOST_TOTAL_CPU}`."
    if used_disk + request_disk > total_disk:
        return f"Disk allocation exceeded: requested `{disk}`, allocated `{format_bytes(used_disk)}`, allocation capacity `{format_bytes(total_disk)}`."
    return None


def db_effective_slots(user_id: int) -> int:
    conn = db_connect()
    try:
        row = conn.execute("SELECT slots FROM user_slots WHERE user_id=?", (int(user_id),)).fetchone()
        return max(1, int(row[0])) if row else max(1, runtime_int("server_limit"))
    finally:
        conn.close()


def db_add_slots(user_id: int, amount: int) -> int:
    if amount <= 0:
        raise ValueError("Slot amount must be positive.")
    now = utc_now()
    conn = db_connect()
    try:
        row = conn.execute("SELECT slots FROM user_slots WHERE user_id=?", (int(user_id),)).fetchone()
        current = int(row[0]) if row else max(1, runtime_int("server_limit"))
        new_total = current + int(amount)
        conn.execute("INSERT INTO user_slots(user_id,slots,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET slots=excluded.slots,updated_at=excluded.updated_at", (int(user_id), new_total, now))
        return new_total
    finally:
        conn.close()


def db_purchase_slot(user_id: int, *, price: int | None = None) -> tuple[bool, str, int, int]:
    """Atomically buy exactly one VPS slot with wallet coins.

    Returns (ok, message, new_balance, new_slots). No partial purchase is
    possible: either both the coin deduction and slot increment commit, or
    neither does.
    """
    uid = int(user_id)
    price = runtime_int("slot_price") if price is None else int(price)
    if price <= 0:
        wallet, bank = db_balance(uid)
        return False, "Slot price is not configured correctly.", wallet + bank, db_effective_slots(uid)
    db_economy(uid)
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        econ = conn.execute("SELECT wallet,bank FROM economy WHERE user_id=?", (uid,)).fetchone()
        slots_row = conn.execute("SELECT slots FROM user_slots WHERE user_id=?", (uid,)).fetchone()
        wallet = int(econ[0]) if econ else 0
        bank = int(econ[1]) if econ else 0
        current = int(slots_row[0]) if slots_row else max(1, runtime_int("server_limit"))
        max_slots = runtime_int("max_user_slots")
        if current >= max_slots:
            conn.rollback()
            return False, f"You already have the maximum `{max_slots}` VPS slots.", wallet + bank, current
        if wallet < price:
            conn.rollback()
            return False, f"You need `{price:,}` wallet coins, but only have `{wallet:,}`.", wallet + bank, current
        now = utc_now()
        conn.execute("UPDATE economy SET wallet=wallet-?,updated_at=? WHERE user_id=?", (price, now, uid))
        new_slots = current + 1
        conn.execute("INSERT INTO user_slots(user_id,slots,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET slots=excluded.slots,updated_at=excluded.updated_at", (uid, new_slots, now))
        conn.commit()
        return True, f"Purchased slot #{new_slots} for `{price:,}` coins.", wallet - price + bank, new_slots
    except Exception:
        with contextlib.suppress(Exception): conn.rollback()
        raise
    finally:
        conn.close()


def db_list_ports(vps_id: int) -> list[sqlite3.Row]:
    conn = db_connect()
    try:
        return conn.execute("SELECT * FROM vps_ports WHERE vps_id=? ORDER BY host_port", (int(vps_id),)).fetchall()
    finally:
        conn.close()


def db_get_port(port_id: int) -> sqlite3.Row | None:
    conn = db_connect()
    try:
        return conn.execute("SELECT * FROM vps_ports WHERE id=?", (int(port_id),)).fetchone()
    finally:
        conn.close()


def db_find_port(vps_id: int, container_port: int, protocol: str = "tcp") -> sqlite3.Row | None:
    conn = db_connect()
    try:
        return conn.execute("SELECT * FROM vps_ports WHERE vps_id=? AND container_port=? AND protocol=?", (int(vps_id), int(container_port), protocol.lower())).fetchone()
    finally:
        conn.close()


def db_insert_port(vps_id: int, container_port: int, host_port: int, protocol: str = "tcp") -> int:
    now = utc_now()
    conn = db_connect()
    try:
        cur = conn.execute("INSERT INTO vps_ports(vps_id,container_port,host_port,protocol,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (int(vps_id), int(container_port), int(host_port), protocol.lower(), "stopped", now, now))
        return int(cur.lastrowid)
    finally:
        conn.close()


def db_update_port(port_id: int, **fields: Any) -> None:
    allowed = {"target_ip", "pid", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    assignments = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [utc_now(), int(port_id)]
    conn = db_connect()
    try:
        conn.execute(f"UPDATE vps_ports SET {assignments},updated_at=? WHERE id=?", values)
    finally:
        conn.close()


def db_delete_port(port_id: int) -> None:
    conn = db_connect()
    try:
        conn.execute("DELETE FROM vps_ports WHERE id=?", (int(port_id),))
    finally:
        conn.close()


def db_delete_all_vps() -> None:
    conn = db_connect()
    try:
        conn.execute("DELETE FROM vps")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='vps'")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='vps_ports'")
    finally:
        conn.close()


def db_find_vps(user_id: int, identifier: str | None, admin: bool = False) -> sqlite3.Row | None:
    rows = db_get_all_vps() if admin else db_get_user_vps(user_id)
    needle = (identifier or "").strip().lower()
    if not needle:
        return rows[0] if rows else None
    exact = [r for r in rows if needle in {str(r["id"]).lower(), str(r["container_id"]).lower(), str(r["container_name"]).lower()}]
    if exact:
        return exact[0]
    partial = [r for r in rows if needle in str(r["container_id"]).lower() or needle in str(r["container_name"]).lower()]
    return partial[0] if len(partial) == 1 else None


def db_share_vps(vps_id: int, user_id: int, shared_by: int, access_level: str = "manage") -> tuple[bool, str]:
    if int(user_id) == int(shared_by):
        return False, "You cannot share a VPS with yourself."
    conn = db_connect()
    try:
        if conn.execute("SELECT 1 FROM vps WHERE id=?", (vps_id,)).fetchone() is None:
            return False, "VPS not found."
        cur = conn.execute(
            "INSERT OR IGNORE INTO vps_shares(vps_id,user_id,shared_by,created_at,access_level) VALUES(?,?,?,?,?)",
            (vps_id, user_id, shared_by, utc_now(), "full" if str(access_level).lower() == "full" else "manage"),
        )
        if cur.rowcount == 0:
            return False, "That user already has access to this VPS."
        return True, "VPS access granted."
    finally:
        conn.close()


def db_unshare_vps(vps_id: int, user_id: int) -> tuple[bool, str]:
    conn = db_connect()
    try:
        cur = conn.execute("DELETE FROM vps_shares WHERE vps_id=? AND user_id=?", (vps_id, user_id))
        return (cur.rowcount > 0, "VPS access removed." if cur.rowcount else "That user does not have shared access.")
    finally:
        conn.close()


def db_find_accessible_vps(user_id: int, identifier: str | None) -> sqlite3.Row | None:
    owner = db_find_vps(user_id, identifier, admin=False)
    if owner:
        return owner
    needle = (identifier or "").strip().lower()
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT v.* FROM vps v INNER JOIN vps_shares s ON s.vps_id=v.id WHERE s.user_id=? ORDER BY v.id DESC",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    if not needle:
        return rows[0] if rows else None
    exact = [r for r in rows if needle in {str(r["id"]).lower(), str(r["container_id"]).lower(), str(r["container_name"]).lower()}]
    if exact:
        return exact[0]
    partial = [r for r in rows if needle in str(r["container_id"]).lower() or needle in str(r["container_name"]).lower()]
    return partial[0] if len(partial) == 1 else None


def db_share_access_level(vps_id: int, user_id: int) -> str | None:
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT access_level FROM vps_shares WHERE vps_id=? AND user_id=?",
            (int(vps_id), int(user_id)),
        ).fetchone()
        if not row:
            return None
        return "full" if str(row[0]).lower() == "full" else "manage"
    finally:
        conn.close()


def db_is_owner_or_admin(user_id: int, vps: sqlite3.Row) -> bool:
    return int(user_id) == int(vps["user_id"]) or (ADMIN_ID > 0 and int(user_id) == int(ADMIN_ID))


def db_update_vps(container_id: str, **fields: Any) -> None:
    allowed = {"status", "suspended", "ssh_command", "sshx_url", "sshx_pid", "ssh_password", "critical", "os_type", "location", "hostname", "public_ipv4", "ipv4_verified_at", "backend", "ptero_server_id", "ptero_identifier", "ptero_user_id"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    assignments = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [utc_now(), container_id]
    conn = db_connect()
    try:
        conn.execute(f"UPDATE vps SET {assignments},updated_at=? WHERE container_id=?", values)
    finally:
        conn.close()


def db_set_vps_ipv4(container_id: str, ipv4: str | None) -> None:
    if ipv4 is not None and not valid_public_ipv4(ipv4):
        raise ValueError("Refusing to store a non-public IPv4 address.")
    db_update_vps(container_id, public_ipv4=ipv4, ipv4_verified_at=utc_now() if ipv4 else None)



def db_list_shared(vps_id: int) -> list[sqlite3.Row]:
    conn = db_connect()
    try:
        return conn.execute(
            """SELECT s.vps_id, s.user_id, s.shared_by, s.created_at,
                      COALESCE(u.username, CAST(s.user_id AS TEXT)) AS username
               FROM vps_shares s LEFT JOIN users u ON u.user_id=s.user_id
               WHERE s.vps_id=? ORDER BY s.created_at""",
            (int(vps_id),),
        ).fetchall()
    finally:
        conn.close()


def db_find_vps_by_ptero_identifier(identifier: str) -> sqlite3.Row | None:
    conn = db_connect()
    try:
        return conn.execute(
            "SELECT * FROM vps WHERE ptero_identifier=? OR container_id=? LIMIT 1",
            (identifier, identifier),
        ).fetchone()
    finally:
        conn.close()


def db_insert_snapshot(vps_id: int, name: str, image_ref: str) -> int:
    conn = db_connect()
    try:
        cur = conn.execute(
            "INSERT INTO vps_snapshots(vps_id,name,image_ref,created_at) VALUES(?,?,?,?)",
            (int(vps_id), name, image_ref, utc_now()),
        )
        return int(cur.lastrowid)
    finally:
        conn.close()


def db_list_snapshots(vps_id: int) -> list[sqlite3.Row]:
    conn = db_connect()
    try:
        return conn.execute(
            "SELECT * FROM vps_snapshots WHERE vps_id=? ORDER BY id DESC",
            (int(vps_id),),
        ).fetchall()
    finally:
        conn.close()


def db_get_snapshot(vps_id: int, name: str) -> sqlite3.Row | None:
    conn = db_connect()
    try:
        return conn.execute(
            "SELECT * FROM vps_snapshots WHERE vps_id=? AND lower(name)=lower(?)",
            (int(vps_id), name),
        ).fetchone()
    finally:
        conn.close()


def db_delete_snapshot(vps_id: int, name: str) -> None:
    conn = db_connect()
    try:
        conn.execute("DELETE FROM vps_snapshots WHERE vps_id=? AND lower(name)=lower(?)", (int(vps_id), name))
    finally:
        conn.close()


def db_delete_vps(container_id: str) -> None:
    conn = db_connect()
    try:
        conn.execute("DELETE FROM vps WHERE container_id=?", (container_id,))
    finally:
        conn.close()


# ================================================================
# Process/Docker execution
# ================================================================

async def run_process(*args: str, timeout: float = 60, stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        return 127, b"", str(exc).encode()
    except OSError as exc:
        return 126, b"", str(exc).encode()

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(stdin), timeout=timeout)
        return process.returncode if process.returncode is not None else -1, stdout, stderr
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=5)
        return -1, b"", b"process timeout"
    except asyncio.CancelledError:
        # A cancelled Docker operation must not leave a live child process behind.
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=5)
        raise


def docker_binary() -> str | None:
    """Resolve Docker without depending on the caller/supervisor PATH.

    systemd, cron, PM2, Pterodactyl and custom supervisors may provide a
    reduced PATH. Prefer an absolute Docker binary path and fall back to PATH.
    """
    candidates = [
        os.getenv("DOCKER_BIN", "").strip(),
        "/usr/bin/docker",
        "/usr/local/bin/docker",
        "/snap/bin/docker",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("docker")


async def docker_cli(*args: str, timeout: float = DOCKER_TIMEOUT, retries: int = 0) -> tuple[int, bytes, bytes]:
    last: tuple[int, bytes, bytes] = (126, b"", b"docker command failed")
    binary = docker_binary()
    if not binary:
        return 127, b"", b"Docker CLI is not installed or is not available in PATH."
    for attempt in range(max(0, retries) + 1):
        last = await run_process(binary, *args, timeout=timeout)
        if last[0] == 0:
            return last
        text = last[2].decode("utf-8", "replace").lower()
        transient = any(x in text for x in ("connection reset", "temporarily unavailable", "i/o timeout", "context deadline exceeded", "connection refused", "tls handshake timeout", "unexpected eof", "eof"))
        if not transient or attempt >= retries:
            break
        await asyncio.sleep(1.5 * (attempt + 1))
    return last


async def spawn_detached(*args: str) -> tuple[int | None, str]:
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        await asyncio.sleep(0.15)
        if proc.returncode is not None:
            return None, f"process exited with code {proc.returncode}"
        return proc.pid, ""
    except FileNotFoundError:
        return None, f"{args[0]} is not installed."
    except OSError as exc:
        return None, str(exc)


async def docker_exists(container: str) -> bool:
    rc, _, _ = await docker_cli("inspect", container, timeout=20, retries=1)
    return rc == 0


async def docker_state(container: str) -> str | None:
    rc, out, _ = await docker_cli("inspect", "-f", "{{.State.Status}}", container, timeout=20, retries=1)
    if rc != 0:
        return None
    return out.decode("utf-8", "replace").strip().lower() or None


async def _wait_for_docker_ready(timeout: float = 30.0) -> tuple[bool, str]:
    deadline = asyncio.get_running_loop().time() + max(5.0, timeout)
    last_detail = "Docker daemon is not ready."
    while asyncio.get_running_loop().time() < deadline:
        ok, detail = await _probe_docker_info()
        if ok:
            return True, detail
        last_detail = detail
        await asyncio.sleep(1.0)
    return False, last_detail


async def _start_docker_daemon() -> tuple[bool, str]:
    """Start an existing Docker daemon without assuming systemd is PID 1."""
    if not docker_binary():
        return False, "Docker CLI is not installed."

    ok, detail = await _probe_docker_info()
    if ok:
        return True, "Docker daemon is already reachable."

    attempts: list[str] = []

    # systemd hosts
    if command_available("systemctl"):
        rc, out, err = await system_command("systemctl", "start", "docker.service", timeout=60)
        attempts.append(f"systemctl start docker.service: {('ok' if rc == 0 else safe_log(err.strip() or out.strip() or 'failed'))}")
        ok, detail = await _wait_for_docker_ready(15)
        if ok:
            if command_available("systemctl"):
                await system_command("systemctl", "enable", "docker.service", timeout=30)
            return True, "Docker daemon started with systemd."

    # SysV/service-managed hosts
    if command_available("service"):
        rc, out, err = await system_command("service", "docker", "start", timeout=60)
        attempts.append(f"service docker start: {('ok' if rc == 0 else safe_log(err.strip() or out.strip() or 'failed'))}")
        ok, detail = await _wait_for_docker_ready(15)
        if ok:
            return True, "Docker daemon started with service management."

    # Alpine/OpenRC hosts
    if command_available("rc-service"):
        rc, out, err = await system_command("rc-service", "docker", "start", timeout=60)
        attempts.append(f"rc-service docker start: {('ok' if rc == 0 else safe_log(err.strip() or out.strip() or 'failed'))}")
        ok, detail = await _wait_for_docker_ready(15)
        if ok:
            return True, "Docker daemon started with OpenRC."

    # Last resort for a real Linux host where dockerd exists but no init system
    # is available (common in minimal rescue images). Only attempt this as root.
    dockerd = shutil.which("dockerd")
    if dockerd and hasattr(os, "geteuid") and os.geteuid() == 0:
        socket_path = "/var/run/docker.sock"
        Path(socket_path).parent.mkdir(parents=True, exist_ok=True)
        # Avoid starting a second daemon if another dockerd is already alive.
        existing = False
        try:
            rc, out, _ = await system_command("pgrep", "-x", "dockerd", timeout=10)
            existing = rc == 0 and bool(out.strip())
        except Exception:
            existing = False
        if not existing:
            pid, error = await spawn_detached(
                dockerd,
                "--host=unix:///var/run/docker.sock",
                "--host=fd://",
            )
            # Some dockerd builds reject fd:// when no socket activation exists;
            # if that happens, retry with the explicit Unix socket only.
            if not pid:
                pid, error = await spawn_detached(dockerd, "--host=unix:///var/run/docker.sock")
            attempts.append(f"dockerd direct start: {('started' if pid else error or 'failed')}")
        else:
            attempts.append("dockerd process already exists")
        ok, detail = await _wait_for_docker_ready(20)
        if ok:
            return True, "Docker daemon started directly."

    return False, (
        "Docker CLI is installed, but the Docker daemon is not reachable. "
        + safe_log(detail)
        + (" Attempts: " + " | ".join(attempts) if attempts else "")
        + " This host must provide a running Docker daemon/socket."
    )


async def docker_runtime_preflight(image: str) -> tuple[bool, str]:
    """Verify that this host can actually launch the same privileged guest profile.

    A responsive Docker daemon is not enough for nested/containerized hosts. In
    LXC/LXD-style environments the outer AppArmor/cgroup policy can reject a
    child container even though `docker info` succeeds. Probe the runtime with
    the requested image before a real deployment, so the user gets the exact
    host-boundary error before wasting time on bootstrap.
    """
    if not DOCKER_RUNTIME_PREFLIGHT or not runtime_bool("guest_systemd_enabled"):
        return True, "runtime sandbox preflight disabled/not required"
    features = await docker_run_features()
    required = {"--privileged", "--security-opt"}
    if not required.issubset(features):
        return False, "Docker runtime does not expose the security options required for systemd guest mode."
    if not image:
        return False, "Runtime preflight image is empty."
    probe_name = f"rgnodes-preflight-{os.getpid()}-{random.randint(1000, 9999)}"
    command = [
        "run", "--rm", "--name", probe_name, "--privileged",
        "--security-opt", "seccomp=unconfined",
        "--security-opt", "apparmor=unconfined",
    ]
    if "--cgroupns" in features and GUEST_CGROUPNS_HOST:
        command += ["--cgroupns", "host"]
    if "--tmpfs" in features:
        command += ["--tmpfs", "/run", "--tmpfs", "/run/lock"]
    command += [image, "/bin/sh", "-lc", "printf 'RGNODES-RUNTIME-PREFLIGHT-OK\\n'"]
    rc, out, err = await docker_cli(*command, timeout=90, retries=0)
    detail = safe_log((err or out).decode("utf-8", "replace").strip())
    if rc == 0 and "RGNODES-RUNTIME-PREFLIGHT-OK" in out.decode("utf-8", "replace"):
        return True, "Docker privileged/AppArmor runtime preflight passed."
    if "apparmor" in detail.lower() and "permission denied" in detail.lower():
        return False, (
            "The Docker daemon is reachable, but the outer host denied the child container's "
            "AppArmor transition. This is an LXC/LXD/container-host policy problem, not a Discord or Python error. "
            "The outer container/host must allow Docker nesting and AppArmor changes before systemd VPS guests can run. "
            f"Runtime error: {detail}"
        )
    return False, f"Docker runtime preflight failed: {detail or f'exit code {rc}'}"


async def docker_host_preflight() -> tuple[bool, str]:
    """Validate the Docker features required by the selected guest mode.

    This is deliberately read-only. It never mutates the host daemon and gives
    deployment a deterministic explanation before downloading a large image.
    """
    ok, detail = await docker_info()
    if not ok:
        return False, detail
    features = await docker_run_features()
    if runtime_bool("guest_systemd_enabled") and GUEST_SYSTEMD_PRIVILEGED and "--privileged" not in features:
        return False, "Docker does not advertise --privileged, which is required for systemd guest mode."
    if "--name" not in features:
        return False, "Docker does not support named containers; the VPS lifecycle cannot be managed safely."
    return True, "Docker preflight passed."

async def docker_info() -> tuple[bool, str]:
    """Return Docker readiness and safely self-heal a missing daemon/CLI.

    Deployment should not fail merely because Docker was never bootstrapped on
    a supported Debian/Ubuntu host. We first probe, optionally install/repair
    Docker as root, then probe again. We never fabricate readiness.
    """
    if docker_binary():
        ok, detail = await _probe_docker_info()
        if ok:
            return True, detail
    elif not AUTO_INSTALL_DOCKER:
        return False, (
            "Docker CLI is not installed. Enable AUTO_INSTALL_DOCKER=true or run "
            "`/install-system confirm:true` as an administrator on a supported host."
        )

    # Automatic bootstrap is deliberately restricted to root + apt hosts.
    if AUTO_INSTALL_DOCKER and hasattr(os, "geteuid") and os.geteuid() == 0 and shutil.which("apt"):
        try:
            installed, detail = await asyncio.wait_for(install_system_dependencies(), timeout=720)
        except asyncio.TimeoutError:
            installed, detail = False, "Automatic Docker bootstrap timed out."
        except Exception as exc:
            installed, detail = False, f"Automatic Docker bootstrap failed: {safe_log(exc)}"
        if installed and docker_binary():
            ok, probe_detail = await _probe_docker_info()
            if ok:
                return True, "Docker was automatically installed/repaired and is ready." + "\n" + safe_log(probe_detail, 1200)
        # Keep the real installer diagnostic instead of replacing it with a
        # generic missing-CLI message.
        if detail:
            logger.warning("Automatic Docker bootstrap did not produce a ready daemon: %s", safe_log(detail))

    if AUTO_REPAIR_DOCKER and MANAGE_DOCKER_DAEMON and docker_binary():
        started, start_detail = await _start_docker_daemon()
        if started:
            return True, start_detail
        return False, start_detail

    if not docker_binary():
        return False, (
            "Docker CLI is not installed and automatic installation is unavailable on this host. "
            "Use a supported Debian/Ubuntu host, install Docker, or enable `/install-system confirm:true`."
        )

    ok, detail = await _probe_docker_info()
    if ok:
        return True, detail
    if MANAGE_DOCKER_DAEMON:
        started, start_detail = await _start_docker_daemon()
        if started:
            return True, start_detail
        return False, start_detail or detail
    return False, (
        "Docker CLI is installed, but the Docker daemon is not reachable. "
        "The host must provide a running Docker daemon/socket. "
        + safe_log(detail)
    )


async def docker_running_count() -> tuple[bool, int]:
    # Do not depend only on labels: older Docker clients may not advertise --label.
    # The RGNODES namespace is the canonical container-name prefix as well.
    rc, out, _ = await docker_cli("ps", "--format", "{{.ID}}\t{{.Names}}", timeout=20, retries=1)
    if rc != 0:
        return False, db_running_count()
    count = 0
    for line in out.decode("utf-8", "replace").splitlines():
        parts = line.strip().split("\t", 1)
        if len(parts) != 2:
            continue
        container_name = parts[1].strip().lower()
        # Current managed names are rgnodes-<id>. Accept the managed namespace
        # label/name family without counting unrelated containers.
        if re.fullmatch(r"rgnodes-[a-z0-9][a-z0-9_.-]*", container_name):
            count += 1
    return True, count


async def docker_pull(image: str) -> tuple[bool, str]:
    rc, _, err = await docker_cli("pull", image, timeout=IMAGE_PULL_TIMEOUT, retries=2)
    if rc == 0:
        return True, ""
    return False, safe_log(err.decode("utf-8", "replace").strip() or "Docker image pull failed.")


def quota_error(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("storage-opt", "disk quota", "quota", "btrfs", "overlay2", "storage driver"))


DOCKER_RUN_FEATURES: set[str] | None = None
DOCKER_FEATURE_LOCK = asyncio.Lock()


async def docker_run_features() -> set[str]:
    global DOCKER_RUN_FEATURES
    if DOCKER_RUN_FEATURES is not None:
        return DOCKER_RUN_FEATURES
    async with DOCKER_FEATURE_LOCK:
        if DOCKER_RUN_FEATURES is not None:
            return DOCKER_RUN_FEATURES
        features: set[str] = set()
        rc, out, _ = await docker_cli("run", "--help", timeout=20, retries=0)
        if rc == 0:
            text = out.decode("utf-8", "replace")
            for flag in ("--init", "--pids-limit", "--storage-opt", "--cpus", "--memory", "--memory-reservation", "--memory-swap", "--memory-swappiness", "--restart", "--hostname", "--name", "--label", "--log-driver", "--log-opt", "--privileged", "--tmpfs", "--mount", "--security-opt", "--cgroupns", "--stop-signal"):
                if flag in text:
                    features.add(flag)
        DOCKER_RUN_FEATURES = features
        logger.info("Docker run capabilities detected: %s", ", ".join(sorted(features)) or "basic-only")
        return features


def feature_error(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in (
        "unknown flag", "unknown option", "invalid option", "not supported",
        "not supported by this daemon", "no such option", "unrecognized option",
    ))


GUEST_BOOTSTRAP_SCRIPT = r"""#!/bin/bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
# Prevent apt helper calls from trying to control services before PID 1 is
# systemd. This variable must never be inherited by the final systemd process.
export SYSTEMD_OFFLINE=1

MARKER=/etc/rgnodes/.system-ready
BOOTSTRAP_MARKER=/etc/rgnodes/.bootstrap-installed
# Persistent VPS data lives in named Docker volumes. docker rm (without -v)
# does not delete these volumes, allowing reinstall/recreate to reattach them.
PERSISTENCE_POLICY=/var/lib/rgnodes/persistence-policy
mkdir -p /var/lib/rgnodes /etc/rgnodes /etc/ssh /etc/systemd/system /var/log/rgnodes
printf 'named-volumes=enabled\ncontainer-delete=preserve-volumes\n' >"$PERSISTENCE_POLICY"
BOOTSTRAP_LOG=/var/log/rgnodes/bootstrap.log
BOOTSTRAP_FAILURE=/etc/rgnodes/bootstrap.failed
exec > >(tee -a "$BOOTSTRAP_LOG") 2>&1
trap 'rc=$?; printf "exit=%s line=%s cmd=%s\n" "$rc" "$LINENO" "${BASH_COMMAND:-unknown}" >"$BOOTSTRAP_FAILURE"; printf "[RGNODES guest] bootstrap failed: rc=%s line=%s cmd=%s\n" "$rc" "$LINENO" "${BASH_COMMAND:-unknown}" >&2' ERR

log() { printf '[RGNODES guest] %s\\n' "$*"; }

prepare_os_repositories() {
    if [ -f /etc/debian_version ] && [ -f /etc/os-release ]; then
        . /etc/os-release
        case "${VERSION_CODENAME:-}" in
            bullseye)
                # Debian 11 is EOL. The archived security suite no longer
                # publishes a Release file, so keeping debian-security in the
                # guest makes a normal `apt update` exit 100 and kills the
                # entire first-boot transaction. Build a known-good archive
                # sources.list instead and deliberately omit the obsolete
                # security suite. The guest is isolated and this avoids a
                # misleading bootstrap failure.
                find /etc/apt/sources.list.d -maxdepth 1 -type f \( -name '*.list' -o -name '*.sources' \) -print -delete 2>/dev/null || true
                cat >/etc/apt/sources.list <<'EOF_BULLSEYE'
deb [check-valid-until=no] http://archive.debian.org/debian bullseye main
deb [check-valid-until=no] http://archive.debian.org/debian bullseye-updates main
EOF_BULLSEYE
                printf '%s\n' 'Acquire::Check-Valid-Until "false";' >/etc/apt/apt.conf.d/99rgnodes-bullseye
                printf '%s\n' 'Acquire::Retries "3";' >/etc/apt/apt.conf.d/80rgnodes-retries
                printf '%s\n' 'APT::Get::Assume-Yes "true";' >/etc/apt/apt.conf.d/80rgnodes-noninteractive
                ;;
            buster)
                # Keep the same EOL/archive safety for Debian 10 images.
                find /etc/apt/sources.list.d -maxdepth 1 -type f \( -name '*.list' -o -name '*.sources' \) -print -delete 2>/dev/null || true
                cat >/etc/apt/sources.list <<'EOF_BUSTER'
deb [check-valid-until=no] http://archive.debian.org/debian buster main
deb [check-valid-until=no] http://archive.debian.org/debian buster-updates main
EOF_BUSTER
                printf '%s\n' 'Acquire::Check-Valid-Until "false";' >/etc/apt/apt.conf.d/99rgnodes-buster
                printf '%s\n' 'Acquire::Retries "3";' >/etc/apt/apt.conf.d/80rgnodes-retries
                printf '%s\n' 'APT::Get::Assume-Yes "true";' >/etc/apt/apt.conf.d/80rgnodes-noninteractive
                ;;
        esac
    fi
}


# Packages are installed with service auto-start disabled. Services are started
# later by systemd after PID 1 has actually become systemd.
install_dpkg_policy() {
    mkdir -p /etc/apt/apt.conf.d
    cat >/etc/apt/apt.conf.d/99rgnodes-noninteractive <<'EOF_APT'
Dpkg::Options {
  "--force-confdef";
  "--force-confold";
};
APT::Get::Assume-Yes "true";
Acquire::Retries "3";
EOF_APT
}

install_policy() {
    cat >/usr/sbin/policy-rc.d <<'EOF'
#!/bin/sh
exit 101
EOF
    chmod 0755 /usr/sbin/policy-rc.d
}
remove_policy() { rm -f /usr/sbin/policy-rc.d; }

apt_update_safe() {
    export DEBIAN_FRONTEND=noninteractive
    if apt update -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold; then
        return 0
    fi

    # Last-resort recovery for archived Debian images whose inherited source
    # files still contain the retired debian-security suite. Only apply this
    # fallback to known EOL Debian codenames; modern Ubuntu/Debian repositories
    # must not be rewritten behind the admin's back.
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        case "${VERSION_CODENAME:-}" in
            bullseye|buster)
                log "APT update failed on archived Debian ${VERSION_CODENAME}; rebuilding archive sources and retrying."
                prepare_os_repositories
                apt clean || true
                rm -rf /var/lib/apt/lists/*
                apt update -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold
                return $?
                ;;
        esac
    fi
    return 1
}

apt_install_base() {
    export DEBIAN_FRONTEND=noninteractive
    export DEBCONF_NONINTERACTIVE_SEEN=true

    # Recover an interrupted dpkg transaction without allowing stale package
    # configuration to poison every later install attempt.
    dpkg --audit >/var/log/rgnodes-dpkg-audit.log 2>&1 || true
    dpkg --configure -a --force-confdef --force-confold >/var/log/rgnodes-dpkg-configure.log 2>&1 || true
    apt -f install -y \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold >/var/log/rgnodes-apt-fix.log 2>&1 || true

    if ! apt_update_safe; then
        log 'APT package indexes could not be refreshed; refusing bootstrap rather than installing from stale metadata.'
        return 70
    fi

    # HARD REQUIREMENTS ONLY.
    # A guest is considered healthy when systemd + SSH can start. Optional
    # utilities are deliberately isolated below so one missing/conflicting
    # package (for example ufw, systemd-resolved, or software-properties-common)
    # cannot make VPS creation fail after the image itself booted correctly.
    local core_packages=(
        systemd
        systemd-sysv
        dbus
        dbus-user-session
        init-system-helpers
        ca-certificates
        curl
        wget
        bash
        coreutils
        procps
        psmisc
        iproute2
        iputils-ping
        util-linux
        openssl
        openssh-client
        openssh-server
    )

    local pkg
    for pkg in "${core_packages[@]}"; do
        if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
            continue
        fi
        if ! apt install -y --no-install-recommends \
            -o Dpkg::Options::=--force-confdef \
            -o Dpkg::Options::=--force-confold \
            "$pkg"; then
            log "CORE package installation failed: $pkg"
            dpkg --configure -a --force-confdef --force-confold >/dev/null 2>&1 || true
            apt -f install -y \
                -o Dpkg::Options::=--force-confdef \
                -o Dpkg::Options::=--force-confold >/dev/null 2>&1 || true
            if ! apt install -y --no-install-recommends \
                -o Dpkg::Options::=--force-confdef \
                -o Dpkg::Options::=--force-confold \
                "$pkg"; then
                log "CORE package still unavailable after recovery: $pkg"
                return 71
            fi
        fi
    done

    # SOFT REQUIREMENTS. Install one package at a time so a single unavailable
    # package never aborts the whole VPS. These tools are useful but not part of
    # the systemd/SSH readiness contract.
    local optional_packages=(
        gnupg lsb-release software-properties-common
        iptables nftables net-tools netcat-openbsd socat sudo jq git
        tar gzip bzip2 unzip xz-utils zip rsync acl make gcc g++
        python3 python3-pip python3-venv systemd-container dbus-x11
        systemd-timesyncd systemd-resolved locales logrotate ufw
    )
    for pkg in "${optional_packages[@]}"; do
        if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
            continue
        fi
        apt install -y --no-install-recommends \
            -o Dpkg::Options::=--force-confdef \
            -o Dpkg::Options::=--force-confold \
            "$pkg" >/var/log/rgnodes-apt-optional.log 2>&1 || \
            log "Optional package unavailable/skipped: $pkg"
    done

    # Ensure the two actual readiness primitives exist before leaving the
    # bootstrap function. This avoids a false-success where apt exited cleanly
    # but sshd/systemd were not installed due to a package-manager edge case.
    command -v systemctl >/dev/null 2>&1 || { log 'systemctl is missing after core package installation'; return 72; }
    command -v sshd >/dev/null 2>&1 || { log 'sshd is missing after core package installation'; return 73; }
    return 0
}

install_kvm_libvirt_stack() {
    [ "__KVM_LIBVIRT__" = "1" ] || return 0

    # Install the requested KVM/libvirt toolchain on every selectable Debian/Ubuntu
    # guest. This is deliberately best-effort: a Docker guest may not expose
    # /dev/kvm or virtualization extensions even when the packages install cleanly.
    # Package/service absence therefore never aborts the VPS bootstrap.
    local id pkgs installed=0
    id="$(. /etc/os-release && printf '%s' "${ID:-}")"
    case "$id" in
        ubuntu|debian|linuxmint|pop|raspbian)
            pkgs=(qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst)
            if apt install -y --no-install-recommends \
                -o Dpkg::Options::=--force-confdef \
                -o Dpkg::Options::=--force-confold \
                "${pkgs[@]}"; then
                installed=1
            else
                log 'KVM/libvirt package install was not fully available; continuing without failing VPS creation.'
                # Retry the core headless stack without virt-manager, which is GUI-oriented
                # and can be unavailable on some minimal repositories.
                if apt install -y --no-install-recommends \
                    -o Dpkg::Options::=--force-confdef \
                    -o Dpkg::Options::=--force-confold \
                    qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst; then
                    installed=1
                    log 'Core KVM/libvirt stack installed; virt-manager was skipped.'
                fi
            fi

            # Service names vary across modern libvirt releases. Never fail the guest
            # bootstrap just because libvirt uses socket activation or split daemons.
            if command -v systemctl >/dev/null 2>&1; then
                for unit in libvirtd.service virtqemud.service virtqemud.socket; do
                    systemctl enable "$unit" >/dev/null 2>&1 || true
                    systemctl start "$unit" >/dev/null 2>&1 || true
                done
            fi

            mkdir -p /etc/libvirt /var/lib/libvirt /var/log/libvirt
            printf 'enabled=1\n' >/etc/rgnodes/kvm-libvirt.conf
            if [ -e /dev/kvm ]; then
                printf 'kvm_device=available\n' >>/etc/rgnodes/kvm-libvirt.conf
                log 'KVM device /dev/kvm is available in the guest.'
            else
                printf 'kvm_device=unavailable\n' >>/etc/rgnodes/kvm-libvirt.conf
                log 'KVM device /dev/kvm is not exposed by the outer host; libvirt tools remain installed but hardware acceleration is unavailable.'
            fi
            printf 'installed=%s\n' "$installed" >>/etc/rgnodes/kvm-libvirt.conf
            return 0
            ;;
        *)
            log "KVM/libvirt guest package install skipped on unsupported guest OS: $id"
            return 0
            ;;
    esac
}

install_web_and_database_stack() {
    [ "__WEB_STACK__" = "1" ] || return 0

    local id codename
    id="$(. /etc/os-release && printf '%s' "${ID:-}")"
    codename="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-}")"

    # Pterodactyl Panel currently requires PHP 8.2 or 8.3. Ubuntu 22.04
    # needs an additional PHP repository; Debian 11/12 use packages.sury.org.
    if [ "$id" = "ubuntu" ]; then
        # Pterodactyl 1.12+ requires PHP 8.2 or 8.3. Prefer Ondrej's PHP
        # packages on Ubuntu so supported PHP versions are available even on
        # newer Ubuntu releases such as 26.04 when the PPA provides them.
        LC_ALL=C.UTF-8 add-apt-repository -y ppa:ondrej/php || true
    elif [ "$id" = "debian" ]; then
        install -m 0755 -d /etc/apt/keyrings
        if curl -fsSL https://packages.sury.org/php/apt.gpg \
            -o /etc/apt/keyrings/sury-php.gpg; then
            chmod 0644 /etc/apt/keyrings/sury-php.gpg
            printf 'deb [signed-by=/etc/apt/keyrings/sury-php.gpg] https://packages.sury.org/php/ %s main\n' \
                "$codename" >/etc/apt/sources.list.d/php-sury.list
        fi
    fi

    # Redis repository for Debian 11/12; Debian 13 has a suitable distro
    # package according to the current Pterodactyl dependency guide.
    if [ "$id" = "debian" ] && { [ "$codename" = "bullseye" ] || [ "$codename" = "bookworm" ]; }; then
        if curl -fsSL https://packages.redis.io/gpg |
            gpg --dearmor --yes -o /etc/apt/keyrings/redis-archive-keyring.gpg; then
            chmod 0644 /etc/apt/keyrings/redis-archive-keyring.gpg
            printf 'deb [signed-by=/etc/apt/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb %s main\n' \
                "$codename" >/etc/apt/sources.list.d/redis.list
        fi
    fi

    # MariaDB repo for Debian 11/12. If the external setup is unavailable,
    # keep the distro package as a safe fallback and fail later only if its
    # resulting version is genuinely incompatible.
    if [ "$id" = "debian" ] && { [ "$codename" = "bullseye" ] || [ "$codename" = "bookworm" ]; }; then
        if curl -fsSL https://r.mariadb.com/downloads/mariadb_repo_setup -o /tmp/mariadb_repo_setup; then
            chmod 0755 /tmp/mariadb_repo_setup
            /tmp/mariadb_repo_setup --skip-maxscale --skip-tools || true
            rm -f /tmp/mariadb_repo_setup
        fi
    fi

    apt update -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold
    apt install -y --no-install-recommends \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
        nginx certbot python3-certbot-nginx tar unzip git \
        mariadb-server mariadb-client redis-server

    if ! apt install -y --no-install-recommends \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
        php8.3 php8.3-common php8.3-cli php8.3-gd php8.3-mysql \
        php8.3-mbstring php8.3-bcmath php8.3-xml php8.3-tokenizer \
        php8.3-fpm php8.3-curl php8.3-zip; then
        if ! apt install -y --no-install-recommends \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
            php8.2 php8.2-common php8.2-cli php8.2-gd php8.2-mysql \
            php8.2-mbstring php8.2-bcmath php8.2-xml php8.2-tokenizer \
            php8.2-fpm php8.2-curl php8.2-zip; then
            apt install -y --no-install-recommends \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
                php php-common php-cli php-gd php-mysql php-mbstring \
                php-bcmath php-xml php-fpm php-curl php-zip
        fi
    fi
}

install_docker_debian_ubuntu() {
    local id codename arch repo_url compose_arch
    id="$(. /etc/os-release && printf '%s' "${ID:-}")"
    codename="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-}")"
    ubuntu_codename="$(. /etc/os-release && printf '%s' "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}")"
    arch="$(dpkg --print-architecture)"
    case "$id" in
        ubuntu) repo_url='https://download.docker.com/linux/ubuntu' ;;
        debian) repo_url='https://download.docker.com/linux/debian' ;;
        *) repo_url='' ;;
    esac

    # Remove only conflicting package names. Never remove Docker data.
    apt remove -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
        docker.io docker-compose docker-compose-v2 docker-doc docker-buildx \
        podman-docker containerd runc >/dev/null 2>&1 || true

    if [ -n "$repo_url" ] && [ -n "$codename" ]; then
        install -m 0755 -d /etc/apt/keyrings
        repo_suite="$codename"
        [ "$id" = "ubuntu" ] && repo_suite="$ubuntu_codename"
        if curl -fsSL "https://download.docker.com/linux/$id/gpg" \
            -o /etc/apt/keyrings/docker.asc; then
            chmod a+r /etc/apt/keyrings/docker.asc
            cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: $repo_url
Suites: $repo_suite
Components: stable
Architectures: $arch
Signed-By: /etc/apt/keyrings/docker.asc
EOF
            if apt update -y && apt install -y --no-install-recommends \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
                docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
                return 0
            fi
        fi
        rm -f /etc/apt/sources.list.d/docker.sources
    fi

    apt update -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold
    apt install -y --no-install-recommends \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
        docker.io containerd runc

    if ! docker compose version >/dev/null 2>&1; then
        apt install -y --no-install-recommends \
            -o Dpkg::Options::=--force-confdef \
            -o Dpkg::Options::=--force-confold \
            docker-compose-v2 docker-compose-plugin || true
    fi
    if ! docker compose version >/dev/null 2>&1; then
        apt install -y --no-install-recommends \
            -o Dpkg::Options::=--force-confdef \
            -o Dpkg::Options::=--force-confold \
            docker-compose-plugin || true
    fi
    if ! docker compose version >/dev/null 2>&1; then
        install -m 0755 -d /usr/local/lib/docker/cli-plugins
        compose_arch="$(uname -m)"
        case "$compose_arch" in
            x86_64|amd64) compose_arch='x86_64' ;;
            aarch64|arm64) compose_arch='aarch64' ;;
            *) compose_arch='' ;;
        esac
        if [ -n "$compose_arch" ]; then
            curl -fsSL \
                "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${compose_arch}" \
                -o /usr/local/lib/docker/cli-plugins/docker-compose || true
            chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose 2>/dev/null || true
        fi
    fi
    docker compose version >/dev/null 2>&1
}

install_node_pm2_yarn() {
    local arch node_arch version tarball tmpdir
    arch="$(dpkg --print-architecture)"
    install -m 0755 -d /etc/apt/keyrings
    case "$arch" in
        amd64) node_arch='x64' ;;
        arm64) node_arch='arm64' ;;
        armhf) node_arch='armv7l' ;;
        ppc64el) node_arch='ppc64le' ;;
        s390x) node_arch='s390x' ;;
        *) node_arch='' ;;
    esac

    if [ -n "$node_arch" ] && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg; then
        chmod 0644 /etc/apt/keyrings/nodesource.gpg
        cat >/etc/apt/sources.list.d/nodesource.sources <<EOF
Types: deb
URIs: https://deb.nodesource.com/node_20.x
Suites: nodistro
Components: main
Architectures: $arch
Signed-By: /etc/apt/keyrings/nodesource.gpg
EOF
        apt update -y || true
    fi

    if ! apt install -y --no-install-recommends \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
        nodejs; then
        true
    fi

    if ! command -v node >/dev/null 2>&1 || ! node -e 'process.exit(process.versions.node.startsWith("20.") ? 0 : 1)'; then
        [ -n "$node_arch" ] || { echo "Unsupported Node.js architecture: $arch" >&2; return 1; }
        version="$(curl -fsSL https://nodejs.org/dist/index.tab | awk -v want='^v20\\.' '$1 ~ want && $0 !~ /-rc|-nightly|-test/ {print $1; exit}')"
        [ -n "$version" ] || { echo 'Could not resolve a stable Node.js 20 release.' >&2; return 1; }
        tarball="node-${version}-linux-${node_arch}.tar.xz"
        tmpdir="/tmp/rgnodes-node20"
        rm -rf "$tmpdir"
        mkdir -p "$tmpdir"
        curl -fsSL "https://nodejs.org/dist/${version}/${tarball}" -o "$tmpdir/$tarball"
        tar -xJf "$tmpdir/$tarball" -C "$tmpdir"
        rm -rf /opt/nodejs-20
        mv "$tmpdir/node-${version}-linux-${node_arch}" /opt/nodejs-20
        ln -sf /opt/nodejs-20/bin/node /usr/local/bin/node
        ln -sf /opt/nodejs-20/bin/npm /usr/local/bin/npm
        ln -sf /opt/nodejs-20/bin/npx /usr/local/bin/npx
        ln -sf /opt/nodejs-20/bin/corepack /usr/local/bin/corepack 2>/dev/null || true
        rm -rf "$tmpdir"
    fi

    node -e 'process.exit(process.versions.node.startsWith("20.") ? 0 : 1)'
    npm --version >/dev/null 2>&1
    npm install -g --no-audit --no-fund yarn@1.22.22 pm2
    command -v yarn >/dev/null 2>&1
    command -v pm2 >/dev/null 2>&1
}



install_composer() {
    local installer="/tmp/composer-setup.php" expected actual
    expected="$(curl -fsSL https://composer.github.io/installer.sig)"
    curl -fsSL https://getcomposer.org/installer -o "$installer"
    actual="$(php -r "echo hash_file('sha384', '$installer');")"
    [ -n "$expected" ] && [ "$actual" = "$expected" ] || {
        rm -f "$installer"
        echo 'Composer installer checksum verification failed.' >&2
        return 1
    }
    php "$installer" --install-dir=/usr/local/bin --filename=composer
    rm -f "$installer"
    chmod 0755 /usr/local/bin/composer
    composer --version --no-ansi | grep -Eq 'Composer version 2\.'
}

install_wings() {
    [ "__INSTALL_WINGS__" = "1" ] || return 0
    local arch suffix url
    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64) suffix='amd64' ;;
        aarch64|arm64) suffix='arm64' ;;
        *) log "Wings binary skipped: unsupported architecture $arch"; return 0 ;;
    esac
    mkdir -p /etc/pterodactyl /var/lib/pterodactyl /var/log/pterodactyl
    url="https://github.com/pterodactyl/wings/releases/latest/download/wings_linux_${suffix}"
    if curl -fsSL "$url" -o /usr/local/bin/wings; then
        chmod 0755 /usr/local/bin/wings
        cat >/etc/systemd/system/wings.service <<'EOF'
[Unit]
Description=Pterodactyl Wings Daemon
Documentation=https://pterodactyl.io/wings/
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service
PartOf=docker.service

[Service]
User=root
WorkingDirectory=/etc/pterodactyl
LimitNOFILE=4096
PIDFile=/var/run/wings/daemon.pid
ExecStart=/usr/local/bin/wings
Restart=on-failure
StartLimitInterval=180
StartLimitBurst=30
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload >/dev/null 2>&1 || true
        # Do not start Wings until a real Panel-generated config.yml exists.
        systemctl disable wings.service >/dev/null 2>&1 || true
    else
        log 'Wings download failed; keeping the guest otherwise healthy'
    fi
}

repair_ssh() {
    mkdir -p /etc/ssh /var/run/sshd
    touch /etc/ssh/sshd_config
    chmod 0644 /etc/ssh/sshd_config
    grep -Eq '^[[:space:]]*Port[[:space:]]+' /etc/ssh/sshd_config \
        || printf '\nPort 22\n' >>/etc/ssh/sshd_config
    grep -Eq '^[[:space:]]*PermitRootLogin[[:space:]]+' /etc/ssh/sshd_config \
        || printf 'PermitRootLogin yes\n' >>/etc/ssh/sshd_config
    grep -Eq '^[[:space:]]*PasswordAuthentication[[:space:]]+' /etc/ssh/sshd_config \
        || printf 'PasswordAuthentication yes\n' >>/etc/ssh/sshd_config
    mkdir -p /etc/ssh/sshd_config.d
    cat >/etc/ssh/sshd_config.d/99-rgnodes.conf <<'EOF_SSH'
PermitRootLogin yes
PasswordAuthentication yes
PubkeyAuthentication yes
UseDNS no
EOF_SSH
    if command -v ufw >/dev/null 2>&1; then
        ufw allow 22/tcp >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
        ufw allow 8080/tcp >/dev/null 2>&1 || true
        ufw allow 8443/tcp >/dev/null 2>&1 || true
        ufw --force disable >/dev/null 2>&1 || true
    fi
    if [ -n "__SSH_PASSWORD__" ]; then
        printf 'root:%s\n' '__SSH_PASSWORD__' | chpasswd || true
        chmod 600 /etc/shadow 2>/dev/null || true
        printf '%s\n' '__SSH_PASSWORD__' >/etc/rgnodes/ssh-password
        chmod 600 /etc/rgnodes/ssh-password
    fi
    ssh-keygen -A >/dev/null 2>&1 || true
    sshd -t
}

install_firstboot_unit() {
    cat >/usr/local/sbin/rgnodes-firstboot-verify <<'EOF'
#!/bin/bash
set -Eeuo pipefail
READY=/etc/rgnodes/.system-ready
rm -f "$READY"
systemctl daemon-reload

wait_active() {
    local unit="$1" tries="${2:-30}" i
    for ((i=1; i<=tries; i++)); do
        if systemctl is-active --quiet "$unit"; then return 0; fi
        systemctl start "$unit" >/dev/null 2>&1 || true
        sleep 1
    done
    systemctl status "$unit" --no-pager -l || true
    return 1
}

if [ "__REQUIRE_NESTED_DOCKER__" = "1" ]; then
    systemctl enable docker.service >/dev/null 2>&1 || true
    wait_active docker.service 60
fi
systemctl enable ssh.service >/dev/null 2>&1 || systemctl enable sshd.service >/dev/null 2>&1 || true
if systemctl cat ssh.service >/dev/null 2>&1; then
    wait_active ssh.service 30
elif systemctl cat sshd.service >/dev/null 2>&1; then
    wait_active sshd.service 30
else
    echo 'OpenSSH service unit not found.' >&2
    exit 25
fi

# Optional web/database services remain installed for Pterodactyl/hosting
# workloads, but are not started by default to keep the guest lightweight.
for unit in nginx.service redis-server.service mariadb.service; do
    if systemctl cat "$unit" >/dev/null 2>&1; then
        systemctl disable "$unit" >/dev/null 2>&1 || true
    fi
done

command -v systemctl >/dev/null
sshd -t
# Nested Docker, PHP, Node.js, Composer, database and web components are
# optional guest features. Their availability is recorded separately and does
# not invalidate a healthy systemd/SSH VPS.
{
    if command -v docker >/dev/null 2>&1; then
        docker info >/dev/null 2>&1 && echo 'optional: guest docker ready' || echo 'optional: guest docker installed but daemon unavailable'
    else
        echo 'optional: guest docker unavailable'
    fi
    command -v node >/dev/null 2>&1 && node -v || echo 'optional: node unavailable'
    command -v npm >/dev/null 2>&1 && npm -v || echo 'optional: npm unavailable'
    command -v yarn >/dev/null 2>&1 && yarn --version || echo 'optional: yarn unavailable'
    command -v pm2 >/dev/null 2>&1 && pm2 -v || echo 'optional: pm2 unavailable'
    command -v composer >/dev/null 2>&1 && composer --version --no-ansi | head -n1 || echo 'optional: composer unavailable'
    command -v php >/dev/null 2>&1 && php -v | head -n1 || echo 'optional: php unavailable'
} >/var/log/rgnodes/optional-tools.log 2>&1 || true
printf 'ready=1\n' >"$READY"
EOF
    chmod 0755 /usr/local/sbin/rgnodes-firstboot-verify

    cat >/etc/systemd/system/rgnodes-firstboot.service <<'EOF'
[Unit]
Description=RGNODES Guest First Boot Verification
Wants=docker.service ssh.service network-online.target
After=docker.service ssh.service network-online.target
ConditionPathExists=!/etc/rgnodes/.system-ready

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/rgnodes-firstboot-verify
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    ln -sf ../rgnodes-firstboot.service \
        /etc/systemd/system/multi-user.target.wants/rgnodes-firstboot.service
}

if command -v apt >/dev/null 2>&1; then
    install_dpkg_policy
    prepare_os_repositories
    if [ ! -f "$BOOTSTRAP_MARKER" ]; then
        install_policy
        trap remove_policy EXIT
        log 'Installing base Linux/systemd/SSH dependencies'
        apt_install_base
        if [ "__NESTED_DOCKER__" = "1" ]; then
            if ! install_docker_debian_ubuntu; then
                if [ "__REQUIRE_NESTED_DOCKER__" = "1" ]; then
                    log 'Nested Docker is required but could not be installed.'
                    exit 42
                fi
                log 'Guest Docker setup failed; nested Docker remains unavailable.'
            fi
        fi
        install_kvm_libvirt_stack || log 'KVM/libvirt setup failed; continuing.'
        install_node_pm2_yarn || log 'Node.js/PM2/Yarn setup failed; continuing.'
        install_web_and_database_stack || log 'Web/database stack setup failed; continuing.'
        install_composer || log 'Composer setup failed; continuing.'
        repair_ssh
        install_wings || log 'Wings setup failed; continuing.'
        install_firstboot_unit
        touch "$BOOTSTRAP_MARKER"
        remove_policy
        trap - EXIT
    else
        # Re-run the optional KVM/libvirt toolchain for existing guests too.
        # All package operations are idempotent and the function is non-fatal.
        install_kvm_libvirt_stack || log 'KVM/libvirt refresh failed; continuing.'
        install_node_pm2_yarn || log 'Node.js/PM2/Yarn refresh failed; continuing.'
        install_web_and_database_stack || log 'Web/database stack refresh failed; continuing.'
        install_composer || log 'Composer refresh failed; continuing.'
        repair_ssh
        install_wings || log 'Wings refresh failed; continuing.'
        install_firstboot_unit
    fi
elif command -v apk >/dev/null 2>&1; then
    # The selectable bot OSes are Debian/Ubuntu. This branch remains safe for
    # an externally supplied Alpine image without pretending systemd is native.
    apk add --no-cache bash curl wget ca-certificates coreutils procps iproute2 \
        iputils iptables nftables util-linux net-tools tar gzip unzip xz socat \
        sudo jq git openssh openssh-client openrc
    if [ "__NESTED_DOCKER__" = "1" ]; then
        apk add --no-cache docker docker-cli-compose || true
    fi
    repair_ssh
else
    echo 'Unsupported guest package manager; cannot bootstrap Linux services.' >&2
    exit 40
fi

# Keep the final runtime process as systemd. The first-boot unit will run on the
# actual systemd boot and only then publish .system-ready. SYSTEMD_OFFLINE must
# not leak into PID 1, otherwise later `systemctl` calls may operate offline.
unset SYSTEMD_OFFLINE 2>/dev/null || true
if [ -x /sbin/init ]; then
    exec /sbin/init
fi
if [ -x /lib/systemd/systemd ]; then
    exec /lib/systemd/systemd
fi
if [ -x /usr/lib/systemd/systemd ]; then
    exec /usr/lib/systemd/systemd
fi

echo 'systemd binary was not installed correctly.' >&2
exit 41
"""

async def docker_run(*, image: str, hostname: str, ram: str, cpu: str, disk: str, container_name: str, location: str, persistent_key: str | None = None, ssh_password: str = "") -> tuple[str | None, str]:
    # Build the command from the flags this particular Docker CLI actually
    # advertises. This avoids noisy failed variants on older/lightweight Docker
    # clients where --init and --pids-limit are unavailable.
    features = await docker_run_features()
    command = ["run", "--detach"]

    if "--restart" in features:
        command += ["--restart", "unless-stopped"]
    if "--memory" in features:
        command += ["--memory", ram]
        try:
            ram_bytes = parse_size_bytes(ram)
            if "--memory-reservation" in features and MEMORY_RESERVATION_PERCENT:
                reservation_bytes = max(6 * 1024**2, int(ram_bytes * MEMORY_RESERVATION_PERCENT / 100))
                command += ["--memory-reservation", str(reservation_bytes)]
            if DISABLE_CONTAINER_SWAP and "--memory-swap" in features:
                command += ["--memory-swap", ram]
            if DISABLE_CONTAINER_SWAP and "--memory-swappiness" in features:
                command += ["--memory-swappiness", "0"]
        except ValueError:
            pass
    if "--cpus" in features:
        command += ["--cpus", cpu]
    if "--hostname" in features:
        command += ["--hostname", hostname]
    if "--name" in features:
        command += ["--name", container_name]
    if "--label" in features:
        command += ["--label", "com.rgnodes.managed=true", "--label", f"com.rgnodes.location={location}"]
    if "--log-driver" in features and "--log-opt" in features:
        command += ["--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=3"]

    if "--init" in features and not runtime_bool("guest_systemd_enabled"):
        command.append("--init")
    if "--pids-limit" in features and not runtime_bool("guest_systemd_enabled"):
        command += ["--pids-limit", "1024"]

    if runtime_bool("guest_persistent_data") and "--mount" in features:
        volume_key = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(persistent_key or container_name)).strip("-._") or "vps"
        volume_key = volume_key[:48]
        persistent_mounts = {
            "root": "/root",
            "home": "/home",
            "srv": "/srv",
            "www": "/var/www",
            "ptero": "/etc/pterodactyl",
            "ptero-data": "/var/lib/pterodactyl",
            "rgnodes": "/var/lib/rgnodes",
        }
        if runtime_bool("guest_persist_system_dirs"):
            persistent_mounts.update({
                "mysql": "/var/lib/mysql",
                "redis": "/var/lib/redis",
                "docker": "/var/lib/docker",
                "containerd": "/var/lib/containerd",
            })
        for suffix, target in persistent_mounts.items():
            volume_name = f"rgnodes-{volume_key}-{suffix}"[:120]
            command += ["--mount", f"type=volume,src={volume_name},dst={target}"]

    guest_command = [image, "tail", "-f", "/dev/null"]
    if runtime_bool("guest_systemd_enabled"):
        if GUEST_SYSTEMD_PRIVILEGED:
            if "--privileged" not in features:
                return None, "This Docker daemon does not support --privileged; a systemd VPS cannot be created safely."
            command.append("--privileged")
        if GUEST_CGROUPNS_HOST and "--cgroupns" in features:
            command += ["--cgroupns", "host"]
        if "--tmpfs" in features:
            command += ["--tmpfs", "/run", "--tmpfs", "/run/lock"]
        if "--stop-signal" in features:
            command += ["--stop-signal", "SIGRTMIN+3"]
        if "--security-opt" in features:
            command += ["--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined"]
        bootstrap = GUEST_BOOTSTRAP_SCRIPT.replace("__NESTED_DOCKER__", "1" if runtime_bool("guest_nested_docker") else "0")
        bootstrap = bootstrap.replace("__REQUIRE_NESTED_DOCKER__", "1" if runtime_bool("guest_require_nested_docker") else "0")
        bootstrap = bootstrap.replace("__DOCKER_PACKAGE__", GUEST_DOCKER_PACKAGE)
        bootstrap = bootstrap.replace("__SSH_PASSWORD__", ssh_password)
        bootstrap = bootstrap.replace("__INSTALL_WINGS__", "1" if runtime_bool("guest_install_wings") else "0")
        bootstrap = bootstrap.replace("__WEB_STACK__", "1" if GUEST_INSTALL_WEB_STACK else "0")
        bootstrap = bootstrap.replace("__DB_STACK__", "1" if GUEST_INSTALL_DATABASE_STACK else "0")
        bootstrap = bootstrap.replace("__KVM_LIBVIRT__", "1" if runtime_bool("guest_install_kvm_libvirt") else "0")
        guest_command = [image, "/bin/bash", "-lc", bootstrap]

    # Docker syntax requires IMAGE after all options. Never execute an
    # options-only `docker run`, because Docker rejects that with:
    # "docker run requires at least 1 argument".
    image = str(image or "").strip()
    if not image:
        return None, "Docker image is empty; deployment configuration is invalid."

    run_command = command + guest_command
    if len(run_command) < 3 or not run_command[2]:
        return None, "Docker run command construction failed before execution."

    quota_requested = ENABLE_HARD_DISK_QUOTA and "--storage-opt" in features
    quota_attempt = (
        command + ["--storage-opt", f"size={disk}"] + guest_command
        if quota_requested
        else run_command
    )
    attempts = [quota_attempt]
    if quota_requested and QUOTA_FALLBACK:
        attempts.append(run_command)

    last_error = "Docker container creation failed."
    for index, attempt in enumerate(attempts):
        if len(attempt) < 3 or not attempt[2]:
            last_error = "Docker run command was incomplete; refusing to execute it."
            continue
        attempt_timeout = runtime_int("guest_bootstrap_timeout") if runtime_bool("guest_systemd_enabled") else 120
        rc, out, err = await docker_cli(*attempt, timeout=attempt_timeout, retries=0)
        if rc == 0:
            container_id = out.decode("utf-8", "replace").strip().splitlines()[0] if out else ""
            if container_id:
                return container_id, ""
            last_error = "Docker returned no container ID."
            continue
        last_error = safe_log(err.decode("utf-8", "replace").strip() or "unknown Docker error")
        if index + 1 < len(attempts) and (quota_error(last_error) or feature_error(last_error)):
            logger.warning("Docker hard-quota create failed; retrying without storage quota: %s", last_error)
            continue
        break
    return None, last_error


async def docker_start(container: str) -> tuple[bool, str]:
    rc, _, err = await docker_cli("start", container, timeout=60, retries=1)
    return rc == 0, safe_log(err.decode("utf-8", "replace").strip())


async def ensure_docker_running(container: str) -> tuple[bool, str]:
    """Treat docker run --detach as already started and only start when needed."""
    state = await docker_state(container)
    if state == "running":
        return True, ""
    ok, error = await docker_start(container)
    if not ok:
        # A concurrent supervisor may have started it between inspect and start.
        if await docker_state(container) == "running":
            return True, ""
        return False, error or "Container could not be started."
    return True, ""


async def guest_system_ready(container: str) -> tuple[bool, str]:
    """Verify that a native-Docker guest booted systemd and all core tooling."""
    if not runtime_bool("guest_systemd_enabled"):
        return True, "systemd guest mode is disabled."
    nested = "1" if (runtime_bool("guest_nested_docker") and runtime_bool("guest_require_nested_docker")) else "0"
    wings = "1" if (runtime_bool("guest_install_wings") and GUEST_REQUIRE_WINGS) else "0"
    script = f"""
set -e
pid1="$(cat /proc/1/comm 2>/dev/null || true)"
[ "$pid1" = "systemd" ] || exit 11
[ -f /etc/rgnodes/.system-ready ] || exit 12
command -v systemctl >/dev/null 2>&1 || exit 13
command -v curl >/dev/null 2>&1 || exit 14
command -v sshd >/dev/null 2>&1 || exit 15
sshd -t >/dev/null 2>&1 || exit 16
if [ "{nested}" = "1" ]; then
    command -v docker >/dev/null 2>&1 || exit 17
    docker info >/dev/null 2>&1 || exit 19
    docker compose version >/dev/null 2>&1 || exit 20
    systemctl is-active --quiet docker.service || exit 18
fi
if [ "{wings}" = "1" ]; then
    command -v wings >/dev/null 2>&1 || exit 31
    wings --version >/dev/null 2>&1 || true
fi
"""
    rc, _, err = await docker_exec_shell(container, script, timeout=40)
    if rc == 0:
        return True, "systemd and SSH guest core are ready; optional tooling is reported separately."
    detail = err.decode("utf-8", "replace").strip()
    return False, detail or f"guest readiness check exited with code {rc}"


async def wait_for_guest_ready(
    container: str, timeout: float | None = None
) -> tuple[bool, str]:
    """Wait for first-boot provisioning without blocking forever.

    ``timeout`` is optional by design; when omitted, the live admin/runtime
    configuration is used.  Never pass ``None`` to ``float()`` because this
    path is part of every systemd VPS deployment.
    """
    if timeout is None:
        timeout_value = runtime_int("guest_bootstrap_timeout")
    else:
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError):
            timeout_value = runtime_int("guest_bootstrap_timeout")
    timeout_value = max(30.0, timeout_value)
    deadline = asyncio.get_running_loop().time() + timeout_value
    last = "guest bootstrap is still running"
    while asyncio.get_running_loop().time() < deadline:
        if await docker_state(container) != "running":
            detail = await docker_logs(container, 120)
            rc, out, err = await docker_cli("inspect", "-f", "{{.State.ExitCode}}|{{.State.OOMKilled}}|{{.State.Error}}|{{.State.FinishedAt}}", container, timeout=20, retries=0)
            inspect_detail = out.decode("utf-8", "replace").strip() if rc == 0 else "inspect unavailable"
            rc_b, out_b, _ = await docker_cli("exec", container, "bash", "-lc", "cat /etc/rgnodes/bootstrap.failed 2>/dev/null || true", timeout=15, retries=0)
            bootstrap_detail = out_b.decode("utf-8", "replace").strip() if rc_b == 0 else ""
            extra = f" Bootstrap failure: {bootstrap_detail}." if bootstrap_detail else ""
            return False, (
                "The guest container stopped during system bootstrap. "
                f"State={inspect_detail}.{extra} Recent guest log:\n{safe_log(detail, 3000)}"
            )
        ok, detail = await guest_system_ready(container)
        if ok:
            return True, detail
        last = detail
        await asyncio.sleep(2)
    return False, f"Guest system bootstrap timed out: {last}"


async def docker_stop(container: str) -> bool:
    rc, _, _ = await docker_cli("stop", "--time", "20", container, timeout=40, retries=1)
    if rc == 0:
        return True
    rc, _, _ = await docker_cli("kill", container, timeout=25, retries=1)
    return rc == 0


async def docker_restart(container: str) -> tuple[bool, str]:
    rc, _, err = await docker_cli("restart", "--time", "20", container, timeout=60, retries=1)
    return rc == 0, safe_log(err.decode("utf-8", "replace").strip())


async def docker_remove(container: str) -> bool:
    rc, _, _ = await docker_cli("rm", "--force", container, timeout=60, retries=1)
    if rc == 0:
        return True
    return not await docker_exists(container)


async def docker_exec(
    container: str,
    *command: str,
    timeout: float = 120,
    retries: int = 1,
) -> tuple[int, bytes, bytes]:
    return await docker_cli("exec", container, *command, timeout=timeout, retries=max(0, int(retries)))


async def docker_exec_shell(container: str, script: str, timeout: float = ACCESS_TIMEOUT) -> tuple[int, bytes, bytes]:
    # Shell scripts may have side effects. Never let the generic transient-error
    # retry mechanism execute the same script a second time.
    return await docker_exec(container, "sh", "-c", script, timeout=timeout, retries=0)


async def docker_stats(container: str) -> dict[str, str]:
    """Live CPU/network plus a cache-adjusted working-set estimate. Never hides a live 0.00%% CPU reading."""
    memory_text = "N/A"
    cgroup_script = r'''set -u
if [ -r /sys/fs/cgroup/memory.current ]; then
  current=$(cat /sys/fs/cgroup/memory.current 2>/dev/null || echo 0)
  inactive=$(awk '$1=="inactive_file"{print $2; found=1} END{if(!found) print 0}' /sys/fs/cgroup/memory.stat 2>/dev/null)
  max=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo max)
  case "$current" in ''|*[!0-9]*) current=0;; esac
  case "$inactive" in ''|*[!0-9]*) inactive=0;; esac
  usage=$(( current > inactive ? current-inactive : 0 ))
  printf 'v2\t%s\t%s\n' "$usage" "$max"
  exit 0
fi
if [ -r /sys/fs/cgroup/memory/memory.usage_in_bytes ]; then
  current=$(cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null || echo 0)
  inactive=$(awk '$1=="total_inactive_file"{print $2; found=1} END{if(!found) print 0}' /sys/fs/cgroup/memory/memory.stat 2>/dev/null)
  limit=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null || echo 0)
  case "$current" in ''|*[!0-9]*) current=0;; esac
  case "$inactive" in ''|*[!0-9]*) inactive=0;; esac
  case "$limit" in ''|*[!0-9]*) limit=0;; esac
  usage=$(( current > inactive ? current-inactive : 0 ))
  printf 'v1\t%s\t%s\n' "$usage" "$limit"
  exit 0
fi
exit 1
'''
    rc_mem, out_mem, _ = await docker_exec_shell(container, cgroup_script, timeout=10)
    if rc_mem == 0:
        parts = out_mem.decode("utf-8", "replace").strip().split("\t")
        if len(parts) == 3:
            try:
                used = int(parts[1])
                raw_limit = parts[2].strip()
                if raw_limit.isdigit():
                    limit = int(raw_limit)
                    if 0 < limit < (1 << 50):
                        memory_text = f"{format_bytes(max(0, used))} / {format_bytes(limit)}"
            except (ValueError, TypeError):
                pass

    rc, out, err = await docker_cli(
        "stats", "--no-stream", "--format", "{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}",
        container, timeout=30, retries=1,
    )
    if rc == 0:
        parts = out.decode("utf-8", "replace").strip().split("\t")
        if len(parts) == 3:
            cpu = parts[0].strip() or "0.00%"
            mem = memory_text if memory_text != "N/A" else normalize_dashboard_memory(parts[1].strip())
            network = normalize_network_stats(parts[2].strip() or "0 B / 0 B")
            return {"cpu": cpu, "memory": mem or "0 B", "network": network}
    # Fallback: old/minimal Docker clients can return a plain row. Keep CPU visible instead of N/A when possible.
    plain = out.decode("utf-8", "replace").strip()
    if plain:
        match = re.search(r"(\d+(?:\.\d+)?%)", plain)
        if match:
            return {"cpu": match.group(1), "memory": memory_text, "network": "N/A"}
    logger.debug("docker stats failed for %s: %s", clean(container, 32), safe_log(err.decode("utf-8", "replace")))
    return {"cpu": "N/A", "memory": memory_text, "network": "N/A"}


async def docker_uptime(container: str) -> str:
    rc, out, _ = await docker_cli("inspect", "-f", "{{.State.StartedAt}}", container, timeout=20, retries=1)
    if rc != 0:
        return "N/A"
    raw = out.decode("utf-8", "replace").strip()
    try:
        started = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        return f"{days}d {hours}h {minutes}m"
    except (ValueError, TypeError):
        return "N/A"


async def docker_disk_usage(container: str) -> dict[str, str]:
    """Return container disk usage while always using the VPS allocation as the dashboard limit.

    Hard disk quotas are optional in RGNODES, so Docker may not expose a real per-container
    quota. In that case we still report the measured writable usage (when available) and
    let the dashboard use the configured VPS disk allocation as the visible limit.
    """
    rc, out, _ = await docker_cli(
        "inspect", "--size", "-f", "{{.SizeRw}}", container,
        timeout=25, retries=1,
    )
    if rc == 0:
        raw = out.decode("utf-8", "replace").strip()
        # Docker normally returns an integer byte count. Treat an empty/<no value> response
        # as unavailable rather than raising and breaking the dashboard.
        if raw.isdigit():
            return {"used": format_bytes(max(0, int(raw))), "total": "configured", "percent": "allocation"}

    # Reliable in-container fallback. Prefer / because it represents the container's root
    # filesystem rather than the host filesystem path.
    script = "df -B1 / 2>/dev/null | awk 'NR==2 {print $2, $3, $4, $5}'"
    rc, out, _ = await docker_exec_shell(container, script, timeout=20)
    parts = out.decode("utf-8", "replace").strip().split()
    if rc == 0 and len(parts) >= 4:
        try:
            total = int(parts[0])
            used = int(parts[1])
            return {
                "used": format_bytes(max(0, used)),
                "total": format_bytes(max(0, total)),
                "percent": parts[3],
            }
        except (TypeError, ValueError):
            pass

    # Do not surface an avoidable N/A for a running VPS: when Docker cannot expose the
    # writable-layer metric, zero is a safe baseline until the next successful refresh.
    return {"used": "0 B", "total": "configured", "percent": "allocation"}


async def docker_logs(container: str, lines: int = 50) -> str:
    safe_lines = max(1, min(int(lines), 200))
    rc, out, err = await docker_cli("logs", "--tail", str(safe_lines), container, timeout=30, retries=1)
    if rc != 0:
        return "Unable to fetch container logs."
    text = out.decode("utf-8", "replace") or err.decode("utf-8", "replace")
    return text.replace("\x00", "")[-3800:] or "No recent logs."


# ================================================================
# SSHx — verified current installer flow, no fragile line continuations
# ================================================================

SSHX_INSTALL_SCRIPT = r"""
set +e
export NO_COLOR=1

D='/tmp/sshx-rgnodes'
LOG="$D/sshx.log"
PID="$D/sshx.pid"
URL="$D/sshx.url"
STATE_DIR='/var/lib/rgnodes/sshx'
REMOTE_SCRIPT="$D/custom-sshx.sh"
CUSTOM_LOG="$D/custom-run.log"

mkdir -p "$D" "$STATE_DIR" 2>/dev/null || exit 10
chmod 700 "$D" "$STATE_DIR" 2>/dev/null || true

clean_ansi() { sed -E 's/\x1B\[[0-9;?]*[ -\/]*[@-~]//g'; }
extract_url() {
    [ -s "$1" ] || return 1
    clean_ansi <"$1" | tr -d '\r' | grep -Eao 'https://sshx\.io/s/[A-Za-z0-9_-]+#[^[:space:]<>\[\]"'"'"']+' | tail -n1
}
find_sshx_pid() {
    if command -v pgrep >/dev/null 2>&1; then pgrep -x sshx 2>/dev/null | tail -n1; return 0; fi
    if command -v ps >/dev/null 2>&1; then ps -eo pid=,comm= 2>/dev/null | awk '$2 == "sshx" {p=$1} END {if (p) print p}'; fi
}
pid_alive() {
    case "${1:-}" in ''|*[!0-9]*) return 1;; esac
    kill -0 "$1" 2>/dev/null
}
save_state() {
    [ -s "$URL" ] && cp -f "$URL" "$STATE_DIR/sshx.url" 2>/dev/null || true
    [ -s "$PID" ] && cp -f "$PID" "$STATE_DIR/sshx.pid" 2>/dev/null || true
    [ -s "$LOG" ] && cp -f "$LOG" "$STATE_DIR/sshx.log" 2>/dev/null || true
    [ -s "$CUSTOM_LOG" ] && cp -f "$CUSTOM_LOG" "$STATE_DIR/custom-run.log" 2>/dev/null || true
    chmod 600 "$STATE_DIR/sshx.url" "$STATE_DIR/sshx.pid" 2>/dev/null || true
}

OLD_PID=''; [ -s "$PID" ] && OLD_PID="$(cat "$PID" 2>/dev/null || true)"
if pid_alive "$OLD_PID"; then
    [ -s "$URL" ] || extract_url "$LOG" >"$URL" 2>/dev/null || true
    save_state
    printf '%s\n' "[SSHX] Existing RGNODES session reused • PID $OLD_PID"
    [ -s "$URL" ] && cat "$URL"
    exit 0
fi

FOUND_PID="$(find_sshx_pid | head -n1)"
if pid_alive "$FOUND_PID"; then
    printf '%s\n' "$FOUND_PID" >"$PID"
    if [ ! -s "$URL" ]; then
        CANDIDATE="$(extract_url "$LOG" 2>/dev/null | tail -n1)"; [ -n "$CANDIDATE" ] && printf '%s\n' "$CANDIDATE" >"$URL"
        CANDIDATE="$(extract_url "$CUSTOM_LOG" 2>/dev/null | tail -n1)"; [ -n "$CANDIDATE" ] && printf '%s\n' "$CANDIDATE" >"$URL"
    fi
    save_state
    printf '%s\n' "[SSHX] Existing RGNODES process recovered • PID $FOUND_PID"
    [ -s "$URL" ] && cat "$URL"
    exit 0
fi

if ! command -v curl >/dev/null 2>&1 && [ "$(id -u 2>/dev/null)" = "0" ] && command -v apt >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt update -y >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt install -y curl ca-certificates >/dev/null 2>&1 || true
fi
command -v curl >/dev/null 2>&1 || { printf '%s\n' '[SSHX] ERROR: curl unavailable'; exit 20; }

rm -f "$REMOTE_SCRIPT" "$CUSTOM_LOG" "$LOG" "$URL" 2>/dev/null || true
: >"$LOG"; : >"$CUSTOM_LOG"
printf '%s\n' '[RGNODES™] Fetching custom RGNODES SSHx launcher...'
if ! curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 15 --max-time 60 -o "$REMOTE_SCRIPT" '__SSHX_CUSTOM_SCRIPT_URL__' >"$D/download.log" 2>&1; then
    printf '%s\n' '[SSHX] ERROR: RGNODES SSHx launcher download failed'
    tail -n 80 "$D/download.log" 2>/dev/null || true
    exit 21
fi
[ -s "$REMOTE_SCRIPT" ] || { printf '%s\n' '[SSHX] ERROR: empty RGNODES launcher'; exit 22; }
chmod 700 "$REMOTE_SCRIPT"
# Branding-only compatibility for an older copy of the user's script; do not
# normalize or otherwise rewrite shell whitespace/commands.
OLD_LABEL='IamGunpoint'; NEW_LABEL='RGNODES™'; sed -i "s/${OLD_LABEL}/${NEW_LABEL}/g" "$REMOTE_SCRIPT" 2>/dev/null || true

nohup bash "$REMOTE_SCRIPT" >"$CUSTOM_LOG" 2>&1 </dev/null &
LAUNCHER_PID=$!
printf '%s\n' "$LAUNCHER_PID" >"$PID"

for i in $(seq 1 70); do
    CANDIDATE="$(extract_url "$CUSTOM_LOG" 2>/dev/null | tail -n1)"; [ -n "$CANDIDATE" ] && printf '%s\n' "$CANDIDATE" >"$URL"
    CANDIDATE="$(extract_url "$LOG" 2>/dev/null | tail -n1)"; [ -n "$CANDIDATE" ] && printf '%s\n' "$CANDIDATE" >"$URL"
    REAL_PID="$(find_sshx_pid | head -n1)"
    if pid_alive "$REAL_PID"; then printf '%s\n' "$REAL_PID" >"$PID"; fi
    [ -s "$URL" ] && break
    if ! pid_alive "$LAUNCHER_PID" && ! pid_alive "$REAL_PID"; then break; fi
    sleep 1
done

save_state
printf '%s\n' '================ SSHX BY RGNODES™ ================'
printf '%s\n' "Launcher PID: $LAUNCHER_PID"
printf '%s' 'SSHx PID: '; cat "$PID" 2>/dev/null || true
printf '%s' 'URL: '; cat "$URL" 2>/dev/null || true
printf '%s\n' ''
printf '%s\n' '===================================================='

if [ -s "$URL" ]; then printf '%s\n' '[SSHX] ONLINE • RGNODES session READY'; exit 0; fi
REAL_PID="$(find_sshx_pid | head -n1)"
if pid_alive "$REAL_PID"; then printf '%s\n' "$REAL_PID" >"$PID"; save_state; printf '%s\n' '[SSHX] process is running; URL not emitted yet.'; tail -n 80 "$CUSTOM_LOG" 2>/dev/null || true; exit 24; fi
printf '%s\n' '[SSHX] RGNODES launcher failed to produce a live session.'
tail -n 120 "$CUSTOM_LOG" 2>/dev/null || true
exit 23
"""

def normalize_sshx_url(raw: str | None) -> str | None:
    """Validate an SSHx share URL and preserve its browser key fragment exactly."""
    text = str(raw or "").replace("\r", " ").replace("\n", " ")
    # SSHx emits https://sshx.io/s/<session>[#<browser-key>].  Never
    # percent-decode, quote, or otherwise rewrite the fragment.
    match = re.search(
        r"https://sshx\.io/s/[A-Za-z0-9_-]+#[^\s<>\[\]\"']+",
        text,
        flags=re.I,
    )
    if not match:
        return None
    url = match.group(0).rstrip(".,;:)]}'\"")
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or parsed.netloc.lower() != "sshx.io":
        return None
    if not re.fullmatch(r"/s/[A-Za-z0-9_-]+", parsed.path):
        return None
    # Do not accept the known-broken form without its E2E fragment.
    if not parsed.fragment or len(parsed.fragment) < 8:
        return None
    if any(ord(ch) < 0x21 or ch in ' <>\"\'[]' for ch in parsed.fragment):
        return None
    return urlunsplit(("https", "sshx.io", parsed.path, parsed.query, parsed.fragment))



async def sshx_process_alive(container: str, pid: str | None) -> bool:
    if not pid or not str(pid).isdigit():
        return False
    script = (
        'PID="' + str(pid) + '"; '
        'if [ -r "/proc/$PID/cmdline" ]; then '
        'CMD="$(tr "\\000" " " < "/proc/$PID/cmdline" 2>/dev/null || true)"; '
        'case "$CMD" in *sshx*) exit 0;; esac; '
        'fi; '
        'if command -v ps >/dev/null 2>&1; then '
        'ps -p "$PID" -o args= 2>/dev/null | grep -qi "sshx" && exit 0; '
        'fi; exit 1'
    )
    rc, _, _ = await docker_exec_shell(container, script, timeout=10)
    return rc == 0


async def _read_sshx_state(container: str) -> tuple[str | None, str | None]:
    rc, out, _ = await docker_exec_shell(
        container,
        "printf '%s\\n' 'URL:'; cat /var/lib/rgnodes/sshx/sshx.url 2>/dev/null || true; printf '%s\\n' 'PID:'; cat /var/lib/rgnodes/sshx/sshx.pid 2>/dev/null || true",
        timeout=10,
    )
    if rc != 0:
        return None, None
    lines = out.decode("utf-8", "replace").splitlines()
    url = None
    pid = None
    try:
        if "URL:" in lines:
            i = lines.index("URL:") + 1
            if i < len(lines):
                url = normalize_sshx_url(lines[i])
        if "PID:" in lines:
            i = lines.index("PID:") + 1
            if i < len(lines):
                candidate = lines[i].strip()
                if candidate.isdigit():
                    pid = candidate
    except (ValueError, IndexError):
        pass
    return url, pid


async def install_and_start_sshx(container: str) -> dict[str, str] | None:
    """Create or reuse one SSHx session for a running Docker VPS.

    The container-side launcher follows the user's exact SSHx commands.  A
    per-VPS asyncio lock prevents two Discord button presses from racing into
    two SSHx processes, while durable PID/URL state allows later requests to
    reuse the same encrypted session.
    """
    if await docker_state(container) != "running":
        logger.warning("SSHx skipped: container %s is not running.", clean(container, 32))
        return None

    lock = VPS_SSHX_LOCKS.setdefault(str(container), asyncio.Lock())
    async with lock:
        if await docker_state(container) != "running":
            return None

        saved_url, saved_pid = await _read_sshx_state(container)
        if saved_pid and await sshx_process_alive(container, saved_pid):
            if saved_url:
                logger.info("Reusing SSHx session for %s (PID %s).", clean(container, 32), saved_pid)
                return {"url": saved_url, "pid": saved_pid}

        timeout = max(90.0, min(float(runtime_int("sshx_total_timeout")), 140.0))
        try:
            rc, out, err = await docker_exec(
                container,
                "bash",
                "-lc",
                SSHX_INSTALL_SCRIPT.replace("__SSHX_CUSTOM_SCRIPT_URL__", SSHX_CUSTOM_SCRIPT_URL),
                timeout=timeout,
                retries=0,
            )
        except asyncio.TimeoutError:
            logger.warning("SSHx launcher timeout for %s; recovering durable state.", clean(container, 32))
            saved_url, saved_pid = await _read_sshx_state(container)
            if saved_pid and await sshx_process_alive(container, saved_pid):
                return {"url": saved_url or "", "pid": saved_pid}
            return None
        except Exception as exc:
            logger.warning("SSHx launcher error for %s: %s", clean(container, 32), safe_log(exc))
            return None

        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")
        url = normalize_sshx_url(stdout + "\n" + stderr)
        state_url, state_pid = await _read_sshx_state(container)
        final_url = state_url or url
        final_pid = state_pid

        if final_pid and await sshx_process_alive(container, final_pid):
            if final_url:
                logger.info("SSHx ready for %s (PID %s).", clean(container, 32), final_pid)
            else:
                logger.info("SSHx running for %s (PID %s), but encrypted URL is not ready.", clean(container, 32), final_pid)
            return {"url": final_url or "", "pid": final_pid}

        detail = safe_log((stderr or stdout).strip() or f"exit={rc}", 2400)
        logger.warning("SSHx launch failed for %s: %s", clean(container, 32), detail)
        return None


async def stop_sshx(container: str) -> None:
    script = r"""
set +e
PID_FILE=/var/lib/rgnodes/sshx/sshx.pid
if [ -s "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE" 2>/dev/null)"
  case "$PID" in
    ''|*[!0-9]*) ;;
    *)
      if [ -r "/proc/$PID/cmdline" ]; then
        CMD="$(tr '\000' ' ' < "/proc/$PID/cmdline" 2>/dev/null || true)"
        case "$CMD" in *sshx*) kill "$PID" 2>/dev/null || true; sleep 1; kill -9 "$PID" 2>/dev/null || true;; esac
      fi
      ;;
  esac
fi
rm -f /var/lib/rgnodes/sshx/sshx.pid /var/lib/rgnodes/sshx/sshx.url
"""
    await docker_exec_shell(container, script, timeout=15)


# ================================================================
# Discord UI helpers — text/layout retained
# ================================================================

FOOTER = "⚡ RGNODES™ • VPS Management • Credit: MrZetrix & Zynox2"
RGNODES_BUILD = "2026.09.14-rgnodes-vm-v1-pro-deepfix-sshx"


def make_embed(title: str, description: str | None = None) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, timestamp=discord.utils.utcnow())
    embed.set_footer(text=FOOTER)
    return embed


def slot_status_text(user_id: int) -> str:
    """Return the real persisted slot allocation and current usage."""
    try:
        used = max(0, int(db_vps_count(int(user_id))))
        limit = max(1, int(db_effective_slots(int(user_id))))
    except Exception:
        used, limit = 0, max(1, runtime_int("server_limit"))
    remaining = max(0, limit - used)
    if remaining == 0:
        return f"`{used}/{limit}` used • **SLOTS FULL**"
    return f"`{used}/{limit}` used • `{remaining}` available"


def status_text(status: str, suspended: bool = False) -> str:
    if suspended:
        return "⛔ SUSPENDED"
    return {"running": "🟢 RUNNING", "stopped": "🔴 STOPPED", "created": "🟡 CREATED", "starting": "🟡 STARTING", "restarting": "🟡 RESTARTING"}.get(status, "⚪ " + clean(status).upper())


def is_unknown_interaction(exc: BaseException) -> bool:
    return isinstance(exc, discord.NotFound) and getattr(exc, "code", None) == 10062


async def safe_defer(interaction: discord.Interaction, ephemeral: bool = False, *, claim: bool = True) -> bool:
    """Defer the original interaction response, optionally claiming it for de-duplication."""
    if claim and not await claim_interaction_once(interaction):
        return False
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        return True
    except discord.InteractionResponded:
        return True
    except discord.NotFound as exc:
        if is_unknown_interaction(exc):
            logger.debug("Ignoring expired interaction during defer (10062).")
            return False
        logger.warning("Interaction defer failed: %s", safe_log(exc))
        return False
    except discord.HTTPException as exc:
        logger.warning("Interaction defer failed: %s", safe_log(exc))
        return False


async def safe_edit_original(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View | None = None,
) -> bool:
    try:
        await asyncio.wait_for(
            interaction.edit_original_response(embed=embed, view=view),
            timeout=DISCORD_API_TIMEOUT,
        )
        return True
    except discord.NotFound as exc:
        if is_unknown_interaction(exc):
            if not INTERACTION_LOG_UNKNOWN_AS_DEBUG:
                return False
            logger.debug("Ignoring expired interaction while editing original response (10062).")
            return False
        logger.warning("Interaction edit failed: %s", safe_log(exc))
        return False
    except (asyncio.TimeoutError, discord.HTTPException, TypeError) as exc:
        logger.warning("Interaction edit failed: %s", safe_log(exc))
        return False


async def safe_followup(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
) -> bool:
    """Finish an interaction without ever creating a second response message."""
    if interaction.response.is_done():
        return await safe_edit_original(interaction, embed=embed, view=view)
    if not await claim_interaction_once(interaction):
        return False
    try:
        await asyncio.wait_for(
            interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral),
            timeout=DISCORD_API_TIMEOUT,
        )
        return True
    except discord.NotFound as exc:
        if is_unknown_interaction(exc):
            logger.debug("Ignoring expired interaction while responding (10062).")
            return False
        logger.warning("Interaction response failed: %s", safe_log(exc))
        return False
    except (asyncio.TimeoutError, discord.HTTPException) as exc:
        logger.warning("Interaction response failed: %s", safe_log(exc))
        return False


async def safe_respond(interaction: discord.Interaction, *, embed: discord.Embed, ephemeral: bool = True, view: discord.ui.View | None = None) -> bool:
    """Send/edit the single response owned by this interaction."""
    if not interaction.response.is_done():
        if not await claim_interaction_once(interaction):
            return False
        try:
            kwargs: dict[str, Any] = {"embed": embed, "ephemeral": ephemeral}
            if view is not None:
                kwargs["view"] = view
            await asyncio.wait_for(
                interaction.response.send_message(**kwargs),
                timeout=DISCORD_API_TIMEOUT,
            )
            return True
        except discord.NotFound as exc:
            if is_unknown_interaction(exc):
                logger.debug("Ignoring expired interaction response (10062).")
                return False
            logger.warning("Response failed: %s", safe_log(exc))
            return False
        except (asyncio.TimeoutError, discord.HTTPException) as exc:
            logger.warning("Response failed: %s", safe_log(exc))
            return False
    return await safe_edit_original(interaction, embed=embed, view=view)


async def safe_component_edit(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View | None = None,
) -> bool:
    """Edit the source component message exactly once for this interaction."""
    if not interaction.response.is_done():
        if not await claim_interaction_once(interaction):
            return False
        try:
            await asyncio.wait_for(
                interaction.response.edit_message(embed=embed, view=view),
                timeout=DISCORD_API_TIMEOUT,
            )
            return True
        except discord.NotFound as exc:
            if is_unknown_interaction(exc):
                logger.debug("Ignoring expired component interaction (10062).")
                return False
            logger.warning("Component edit failed: %s", safe_log(exc))
            return False
        except (asyncio.TimeoutError, discord.HTTPException) as exc:
            logger.warning("Component edit failed: %s", safe_log(exc))
            return False
    return await safe_edit_original(interaction, embed=embed, view=view)


async def safe_dm(user: discord.User | discord.Member, embed: discord.Embed, view: discord.ui.View | None = None) -> bool:
    try:
        kwargs: dict[str, Any] = {"embed": embed}
        if view is not None:
            kwargs["view"] = view
        await asyncio.wait_for(user.send(**kwargs), timeout=DISCORD_API_TIMEOUT)
        return True
    except discord.Forbidden:
        return False
    except (asyncio.TimeoutError, discord.HTTPException) as exc:
        logger.warning("DM failed: %s", safe_log(exc))
        return False


async def safe_dm_file(user: discord.User | discord.Member, embed: discord.Embed, path: str) -> bool:
    try:
        await asyncio.wait_for(user.send(embed=embed, file=discord.File(path)), timeout=DISCORD_API_TIMEOUT)
        return True
    except discord.Forbidden:
        return False
    except (asyncio.TimeoutError, discord.HTTPException, OSError) as exc:
        logger.warning("DM file send failed: %s", safe_log(exc))
        return False


def sshx_view(url: str) -> discord.ui.View:
    view = discord.ui.View(timeout=900)
    view.add_item(discord.ui.Button(label="Click to Open", emoji="🌐", style=discord.ButtonStyle.link, url=url))
    return view



def ssh_access_embed(vps: sqlite3.Row, ipv4: str | None, ssh_port: int | None) -> discord.Embed:
    embed = make_embed("🔐 RGNODES™ • SSH Access", "Your VPS SSH credentials are shown below. Keep this message private.")
    embed.add_field(name="🖥️ VPS", value=f"`{clean(vps['container_name'])}` • VMID `{vps['id']}`", inline=False)
    embed.add_field(name="🌐 Address", value=f"`{clean(ipv4 or 'Unavailable')}`", inline=True)
    embed.add_field(name="🔌 SSH Port", value=f"`{ssh_port or 'Unavailable'}`", inline=True)
    command = f"ssh root@{ipv4} -p {ssh_port}" if valid_public_ipv4(ipv4) and ssh_port else "SSH command will appear after public IPv4/port is verified."
    embed.add_field(name="💻 Command", value=f"`{command}`", inline=False)
    password = str(vps["ssh_password"] or "") if "ssh_password" in vps.keys() else ""
    embed.add_field(name="🔑 Root Password", value=f"`{clean(password, 128)}`" if password else "Unavailable — use the VPS password reset flow.", inline=False)
    embed.set_footer(text=FOOTER)
    return embed

def console_embed(vps_name: str, url: str, ipv4: str | None = None, location: str | None = None) -> discord.Embed:
    embed = make_embed("✨ RGNODES™ • 🌐 SSHx Access", "Your private web SSH console is ready.")
    embed.add_field(name="🖥️ VPS", value=f"`{clean(vps_name)}`", inline=False)
    if valid_public_ipv4(ipv4):
        embed.add_field(name="🌐 Verified IPv4", value=f"`{ipv4}`", inline=True)
    if location:
        embed.add_field(name="🌍 Node", value=clean(location, 80), inline=True)
    embed.add_field(name="🔗 Link", value="Click **Open Console** below.", inline=False)
    embed.add_field(name="⚠️ Security", value="This link grants direct root access. Do not share it. Generate a new link if it is exposed.", inline=False)
    return embed


def ipv4_dm_embed(vps: sqlite3.Row, network: dict[str, str]) -> discord.Embed:
    ip = network.get("ip")
    embed = make_embed("🔐 RGNODES™ • Private Network Details", "Your verified public IPv4 is provided privately in this DM.")
    embed.add_field(name="🖥️ VPS", value=f"`{clean(vps['container_name'])}` • ID `{vps['id']}`", inline=False)
    embed.add_field(name="🌐 Verified Public IPv4", value=f"`{clean(ip, 64)}`", inline=True)
    embed.add_field(name="🌍 Detected Location", value=clean(actual_location_label(network), 80), inline=True)
    embed.add_field(name="🔒 Privacy", value="This IPv4 is intentionally hidden from public/channel embeds.", inline=False)
    return embed


async def send_private_ipv4(user: discord.User | discord.Member, vps: sqlite3.Row) -> bool:
    network = await detect_public_network(force=True)
    ip = network.get("ip")
    if not valid_public_ipv4(ip):
        return False
    db_set_vps_ipv4(vps["container_id"], ip)
    return await safe_dm(user, ipv4_dm_embed(vps, network))


async def host_uptime() -> str:
    try:
        raw = Path("/proc/uptime").read_text(encoding="utf-8", errors="replace").split()[0]
        seconds = max(0, int(float(raw)))
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)
        return f"{days}d {hours}h {minutes}m {secs}s"
    except (OSError, ValueError, IndexError):
        return "N/A"


async def backend_state(vps: sqlite3.Row) -> str | None:
    if str(vps["backend"] or "docker").lower() == "pterodactyl":
        return await ptero_status(vps)
    return await docker_state(vps["container_id"])


async def backend_stats(vps: sqlite3.Row) -> dict[str, str]:
    if str(vps["backend"] or "docker").lower() == "pterodactyl":
        return await ptero_utilization(vps)
    return await docker_stats(vps["container_id"])


def format_duration_ms(ms: int | float | str | None) -> str:
    try:
        seconds = max(0, int(float(ms or 0) / 1000))
    except (TypeError, ValueError):
        return "N/A"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


async def backend_uptime(vps: sqlite3.Row) -> str:
    if str(vps["backend"] or "docker").lower() == "pterodactyl":
        if not ptero_client_configured():
            return "N/A"
        identifier = vps["ptero_identifier"] or vps["container_id"]
        status, body = await ptero_request(
            "GET", f"client/servers/{quote(str(identifier), safe='')}/resources",
            api_key=PTERO_CLIENT_API_KEY,
        )
        if status == 200 and isinstance(body, dict):
            attrs = _ptero_attr(body) or {}
            resources = attrs.get("resources") or {}
            return format_duration_ms(resources.get("uptime"))
        return "N/A"
    return await docker_uptime(vps["container_id"])


async def backend_disk(vps: sqlite3.Row) -> dict[str, str]:
    if str(vps["backend"] or "docker").lower() == "pterodactyl":
        stats = await ptero_utilization(vps)
        return {"used": str(stats.get("disk", "N/A")), "total": clean(vps["disk"]), "percent": "panel limit"}
    return await docker_disk_usage(vps)


async def backend_panel_or_console(vps: sqlite3.Row) -> str | None:
    if str(vps["backend"] or "docker").lower() == "pterodactyl":
        return await ptero_panel_link(vps)
    return normalize_sshx_url(vps["sshx_url"])


async def refresh_vps_record_state(vps: sqlite3.Row) -> sqlite3.Row:
    """Best-effort live state refresh; never let a backend probe break the dashboard."""
    backend = str(vps["backend"] or "docker").lower()
    try:
        state = await asyncio.wait_for(backend_state(vps), timeout=20)
    except Exception as exc:
        logger.warning("VPS #%s state probe failed: %s", vps["id"], safe_log(exc))
        state = None
    try:
        if state == "running":
            db_update_vps(
                vps["container_id"],
                status="running",
                sshx_pid=None if backend == "pterodactyl" else vps["sshx_pid"],
            )
        elif state in {"stopped", "off"}:
            db_update_vps(
                vps["container_id"],
                status="stopped",
                sshx_url=None if backend == "docker" else vps["sshx_url"],
                sshx_pid=None,
            )
    except Exception as exc:
        logger.warning("VPS #%s state persistence failed: %s", vps["id"], safe_log(exc))
    return db_get_vps(vps["id"]) or vps


def dashboard_embed(vps: sqlite3.Row, stats: dict[str, str], uptime: str, disk: dict[str, str], network: dict[str, str] | None = None, ports: list[sqlite3.Row] | None = None) -> discord.Embed:
    """Render one stable dashboard layout used by both prefix and slash commands."""
    _ = network  # Kept for API compatibility; detected node is intentionally not displayed.
    ports = ports if ports is not None else db_list_ports(vps["id"])
    port_summary = "None configured" if not ports else " • ".join(
        f"`{p['host_port']}→{p['container_port']}/{str(p['protocol']).upper()}`" for p in ports[:10]
    )
    live = status_text(vps["status"], bool(vps["suspended"]))
    backend = str(vps["backend"] or "docker").lower()
    embed = make_embed(
        f"🖥️ VPS #{vps['id']} • VMID `{vps['id']}`",
        f"**{live}** • `{clean(vps['container_name'])}`",
    )

    embed.add_field(
        name="📦 Resources",
        value=(
            f"╭ **RAM:** {clean(vps['ram'])}\n"
            f"├ **CPU Limit:** {clean(vps['cpu'])} Core(s)\n"
            f"├ **Storage:** {clean(vps['disk'])}\n"
            f"├ **OS:** {os_label(vps['os_type'])}\n"
            f"╰ **Node:** {location_label(vps['location'])}"
        ),
        inline=True,
    )

    runtime_label = "Docker: **:whale:** Ready" if backend == "docker" else "Pterodactyl: Ready"
    embed.add_field(
        name="⚙️ Configuration",
        value=(
            f"╭ **Slots:** {slot_status_text(vps['user_id'])}\n"
            f"├ **Uptime:** {clean(uptime)}\n"
            f"├ **Hostname:** `{clean(vps['hostname'])}`\n"
            f"├ **IPv4:** 🔒 Sent privately in DM\n"
            f"╰ **{runtime_label}**"
        ),
        inline=True,
    )

    cpu_value = clean(stats.get("cpu"))
    memory_value = normalize_dashboard_memory(stats.get("memory"))
    disk_used = clean(disk.get("used"))
    # The configured disk is an allocation even when hard quota enforcement is off.
    if disk_used in {"", "N/A", "None", "null"}:
        disk_used = "0 B (baseline)" if str(vps["status"]).lower() == "running" else "N/A"
    network_value = normalize_network_stats(stats.get("network"))
    embed.add_field(
        name="📈 Live Stats",
        value=(
            f"💻 **CPU:** {cpu_value} used / {clean(vps['cpu'])} limit\n"
            f"🧠 **Memory:** {memory_value}\n"
            f"💾 **Disk:** {disk_used} / {clean(vps['disk'])}\n"
            f"🌐 **Network:** {network_value}"
        ),
        inline=False,
    )

    if backend == "pterodactyl":
        embed.add_field(
            name="🦖 Pterodactyl",
            value=f"Server ID: `{clean(vps['ptero_server_id'])}` • Identifier: `{clean(vps['ptero_identifier'])}`",
            inline=False,
        )
        embed.add_field(name="🌐 Allocations", value="Managed by Pterodactyl Panel/Wings.", inline=False)
    else:
        embed.add_field(
            name=f"🌐 Port Forwarding • {len(ports)}/{runtime_int('max_ports_per_vps')}",
            value=port_summary,
            inline=False,
        )

    embed.add_field(
        name="🎮 Action",
        value="Use the buttons below to control your VPS.",
        inline=False,
    )
    return embed



def progress_embed(stage: int, title: str, os_type: str, location: str, ram: str, cpu: str, disk: str, name: str) -> discord.Embed:
    total = 10
    filled = max(0, min(stage, total))
    bar = "▰" * filled + "▱" * (total - filled)
    embed = make_embed("✨ RGNODES™ VPS Deployment", f"**{title}**\n`{bar}` **{filled * 10}%**")
    embed.add_field(name="🖥️ OS", value=os_label(os_type), inline=True)
    embed.add_field(name="🌍 Location", value=location_label(location), inline=True)
    embed.add_field(name="📦 VPS", value=f"`{clean(name)}`", inline=True)
    embed.add_field(name="⚙️ Resources", value=f"`{ram}` RAM • `{cpu}` CPU • `{disk}` Disk", inline=False)
    return embed



# ================================================================
# Pterodactyl Application/Client API integration
# ================================================================
PTERO_API_LOCK = asyncio.Lock()

def _ptero_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "Application/vnd.pterodactyl.v1+json",
        "User-Agent": "RGNODES-VPS-Manager/2.0",
    }


def _ptero_request_sync(method: str, url: str, api_key: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any] | str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=_ptero_headers(api_key))
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", "replace")
            if not raw:
                return resp.status, {}
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", "replace")
            try:
                body: dict[str, Any] | str = json.loads(raw)
            except json.JSONDecodeError:
                body = raw
        except Exception:
            body = str(exc)
        return int(exc.code), body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 599, str(exc)


async def ptero_request(method: str, path: str, *, payload: dict[str, Any] | None = None, api_key: str | None = None) -> tuple[int, dict[str, Any] | str]:
    key = api_key or PTERO_API_KEY
    if not PTERO_URL or not key:
        return 503, "Pterodactyl is not configured."
    url = f"{PTERO_URL}/api/{path.lstrip('/')}"
    async with PTERO_API_LOCK:
        return await asyncio.to_thread(_ptero_request_sync, method, url, key, payload)


def ptero_error_message(status: int, body: dict[str, Any] | str) -> str:
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list):
            msgs: list[str] = []
            for item in errors[:4]:
                if isinstance(item, dict):
                    detail = item.get("detail") or item.get("code") or item.get("title")
                    if detail:
                        msgs.append(str(detail))
            if msgs:
                return "; ".join(msgs)
        if body.get("message"):
            return str(body["message"])
    if status == 599:
        return "Pterodactyl panel is unreachable or timed out."
    return safe_log(str(body or f"HTTP {status}"), 1200)


def _ptero_attr(body: dict[str, Any] | str) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    attrs = body.get("attributes")
    return attrs if isinstance(attrs, dict) else None


def _ptero_limit_mb(value: str) -> int:
    return max(256, int(parse_size_bytes(value) / 1024**2))


async def ptero_get_server(server_id: int | str) -> dict[str, Any] | None:
    status, body = await ptero_request("GET", f"application/servers/{quote(str(server_id), safe='')}?include=allocations,node,user")
    if status != 200:
        logger.warning("Pterodactyl server lookup failed (%s): %s", status, ptero_error_message(status, body))
        return None
    return _ptero_attr(body)


async def ptero_create_server(*, name: str, ram: str, cpu: str, disk: str) -> tuple[bool, str, dict[str, Any] | None]:
    if not ptero_application_configured():
        return False, (
            "Pterodactyl Application API is not fully configured. Set "
            "PTERO_URL, PTERO_API_KEY, PTERO_DEFAULT_USER_ID, PTERO_NODE_ID, "
            "PTERO_NEST_ID, PTERO_EGG_ID and PTERO_ALLOCATION_ID."
        ), None
    owner_id = PTERO_DEFAULT_USER_ID

    environment = dict(PTERO_ENVIRONMENT)
    payload: dict[str, Any] = {
        "name": name,
        "user": owner_id,
        "node": PTERO_NODE_ID,
        "nest": PTERO_NEST_ID,
        "egg": PTERO_EGG_ID,
        "docker_image": PTERO_DOCKER_IMAGE or "ghcr.io/pterodactyl/yolks:debian",
        "startup": PTERO_STARTUP or "bash",
        "environment": environment,
        "limits": {
            "memory": _ptero_limit_mb(ram),
            "swap": PTERO_MEMORY_SWAP,
            "disk": int(parse_size_bytes(disk) / 1024**2),
            "io": PTERO_IO,
            "cpu": max(1, int(float(cpu) * 100)),
        },
        "feature_limits": {
            "databases": PTERO_DATABASES,
            "allocations": PTERO_ALLOCATIONS,
            "backups": PTERO_BACKUPS,
        },
        "allocation": {"default": PTERO_ALLOCATION_ID},
        "deploy": {
            "locations": [],
            "port_range": [],
            "dedicated_ip": False,
        },
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    status, body = await ptero_request("POST", "application/servers", payload=payload)
    if status not in {200, 201}:
        return False, ptero_error_message(status, body), None
    attrs = _ptero_attr(body)
    if not attrs:
        return False, "Pterodactyl created the server but returned no server attributes.", None
    return True, "Pterodactyl server created successfully.", attrs


async def ptero_power(vps: sqlite3.Row, action: str) -> tuple[bool, str]:
    identifier = vps["ptero_identifier"] or vps["container_id"]
    if not ptero_client_configured():
        return False, "PTERO_CLIENT_API_KEY is not configured; Pterodactyl power and live-resource commands require a Client API key."
    signal_name = {"start": "start", "stop": "stop", "restart": "restart", "kill": "kill"}.get(action)
    if not signal_name:
        return False, "Unsupported Pterodactyl power action."
    status, body = await ptero_request("POST", f"client/servers/{quote(str(identifier), safe='')}/power", payload={"signal": signal_name}, api_key=PTERO_CLIENT_API_KEY)
    if status not in {200, 204}:
        return False, ptero_error_message(status, body)
    return True, f"Pterodactyl power action `{signal_name}` completed."


async def ptero_utilization(vps: sqlite3.Row) -> dict[str, str]:
    identifier = vps["ptero_identifier"] or vps["container_id"]
    if PTERO_CLIENT_API_KEY:
        status, body = await ptero_request(
            "GET", f"client/servers/{quote(str(identifier), safe='')}/resources",
            api_key=PTERO_CLIENT_API_KEY,
        )
        if status == 200 and isinstance(body, dict):
            attrs = _ptero_attr(body) or {}
            current = attrs.get("current_state", "offline")
            res = attrs.get("resources") or {}
            memory = int(res.get("memory_bytes") or 0)
            cpu_ns = float(res.get("cpu_absolute") or 0.0)
            disk = int(res.get("disk_bytes") or 0)
            return {
                "cpu": f"{cpu_ns:.2f}%",
                "memory": format_bytes(memory),
                "network": f"{format_bytes(int(res.get('network_rx_bytes') or 0))} ↓ / {format_bytes(int(res.get('network_tx_bytes') or 0))} ↑",
                "state": str(current),
                "disk": format_bytes(disk),
            }
    app = await ptero_get_server(vps["ptero_server_id"])
    if app:
        suspended = bool(app.get("suspended", False))
        installed = bool(app.get("installed", True))
        return {
            "cpu": "N/A", "memory": "N/A", "network": "N/A", "disk": "N/A",
            "state": "suspended" if suspended else ("installing" if not installed else "unknown"),
        }
    return {"cpu": "N/A", "memory": "N/A", "network": "N/A", "disk": "N/A", "state": "unknown"}


async def ptero_status(vps: sqlite3.Row) -> str:
    data = await ptero_utilization(vps)
    state = str(data.get("state", "unknown")).lower()
    if state in {"running", "on"}:
        return "running"
    if state in {"starting", "restarting", "installing"}:
        return state
    if state in {"stopped", "offline", "off"}:
        return "stopped"
    return "unknown"


async def ptero_panel_link(vps: sqlite3.Row) -> str:
    identifier = str(vps["ptero_identifier"] or vps["container_id"])
    return f"{PTERO_PANEL_PUBLIC_URL}/server/{identifier}" if PTERO_PANEL_PUBLIC_URL else ""


# ================================================================
# Lifecycle / deployment
# ================================================================

OperationCallback = Callable[[discord.Embed], Awaitable[None]]
CREATE_LOCK = asyncio.Lock()
CAPACITY_LOCK = asyncio.Lock()
VPS_LOCKS: dict[str, asyncio.Lock] = {}
VPS_SSHX_LOCKS: dict[str, asyncio.Lock] = {}
DEPLOY_USER_LOCKS: dict[int, asyncio.Lock] = {}


def vps_lock(vps_id: int) -> asyncio.Lock:
    key = str(vps_id)
    return VPS_LOCKS.setdefault(key, asyncio.Lock())


async def next_container_name() -> str:
    rc, out, _ = await docker_cli("ps", "--all", "--format", "{{.Names}}", timeout=20, retries=1)
    used: set[int] = set()
    if rc == 0:
        for raw in out.decode("utf-8", "replace").splitlines():
            m = re.fullmatch(r"rgnodes-(\d+)", raw.strip(), flags=re.I)
            if m:
                used.add(int(m.group(1)))
    for row in db_get_all_vps():
        m = re.fullmatch(r"rgnodes-(\d+)", str(row["container_name"]), flags=re.I)
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"rgnodes-{n}"


async def update_progress(callback: OperationCallback | None, stage: int, title: str, *, os_type: str, location: str, ram: str, cpu: str, disk: str, name: str) -> None:
    if not callback:
        return
    try:
        await asyncio.wait_for(
            callback(progress_embed(stage, title, os_type, location, ram, cpu, disk, name)),
            timeout=PROGRESS_UPDATE_TIMEOUT,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # UI/network failure must never turn a healthy VPS deployment into a rollback.
        logger.debug("Progress update skipped at stage %s: %s", stage, safe_log(exc))


async def create_vps(
    user: discord.User | discord.Member,
    *,
    os_type: str,
    location: str,
    ram: str,
    cpu: str,
    disk: str,
    progress: OperationCallback | None = None,
    backend_override: str | None = None,
) -> tuple[bool, str, sqlite3.Row | None]:
    normalized_os = normalize_os(os_type)
    normalized_location = normalize_location(location)
    if not normalized_os:
        return False, "Unsupported operating system.", None
    if not normalized_location:
        return False, "Unsupported location. Choose Singapore (SG) or India (IN).", None
    try:
        ram, cpu, disk = validate_resources(ram, cpu, disk)
    except ValueError as exc:
        return False, str(exc), None
    # New VPS creation is always local. Pterodactyl is guest software, not the
    # creation backend. Keep legacy Pterodactyl records controllable elsewhere,
    # but never create a new VPS through the Pterodactyl API.
    backend = "docker"
    if db_is_banned(user.id):
        return False, "You are not allowed to create VPS instances.", None

    # Public IPv4 detection is intentionally deferred until after the VPS is
    # durable. External IP providers can be slow/unreachable and must never
    # delay or abort the actual provisioning transaction.
    verified_ipv4 = None

    async with CREATE_LOCK:
        is_admin_user = ADMIN_BYPASS_LIMITS and ADMIN_ID > 0 and int(user.id) == int(ADMIN_ID)
        slot_limit = db_effective_slots(user.id)
        slot_used = db_vps_count(user.id)
        if not is_admin_user and slot_used >= slot_limit:
            return False, f"SLOTS FULL — you are using `{slot_used}/{slot_limit}` VPS slots. Additional slots will be available soon. Ask an administrator to add slots.", None

        async with CAPACITY_LOCK:
            if backend == "docker":
                live_ok, live_running = await docker_running_count()
                if not live_ok:
                    live_running = db_running_count()
            else:
                live_running = sum(
                    1 for row in db_get_all_vps()
                    if str(row["backend"] or "docker").lower() == "pterodactyl"
                    and str(row["status"]).lower() == "running"
                    and not row["suspended"]
                )
            if not is_admin_user and live_running >= runtime_int('total_running_limit'):
                return False, f"Global running VPS limit reached ({runtime_int('total_running_limit')}).", None

        # Resource allocation must be checked inside CREATE_LOCK. Checking
        # before the transaction lets two concurrent deploys both observe the
        # same free capacity and over-allocate the host.
        if backend == "docker":
            capacity_error = resource_capacity_error(ram, cpu, disk)
            if capacity_error:
                return False, capacity_error, None

        total_limit = db_total_create_limit()
        if not is_admin_user and len(db_get_all_vps()) >= total_limit:
            return False, f"Global VPS creation limit reached ({total_limit}).", None

        suspended, suspend_detail = db_is_user_suspended(user.id)
        if suspended and not is_admin_user:
            return False, f"Your VPS creation is temporarily suspended ({suspend_detail}).", None

        name = await next_container_name()
        hostname = os.getenv("GUEST_HOSTNAME", VPS_HOSTNAME_PREFIX).strip()[:63] or "rgnodes-vps"
        image = OS_CONFIG[normalized_os]["image"]
        ssh_password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(SSH_PASSWORD_LENGTH))
        resource_id: str | None = None
        ptero_server_id: int | None = None
        ptero_identifier: str | None = None
        persisted_vps_id: int | None = None

        try:
            await update_progress(progress, 1, f"Validating {backend} backend", os_type=normalized_os, location=normalized_location, ram=ram, cpu=cpu, disk=disk, name=name)

            # Pterodactyl API creation is intentionally disabled for new VPSes.
            await update_progress(progress, 2, "Preparing Docker", os_type=normalized_os, location=normalized_location, ram=ram, cpu=cpu, disk=disk, name=name)
            ok, docker_error = await docker_host_preflight()
            if not ok:
                logger.warning("Docker preflight failed: %s", safe_log(docker_error))
                retry_install = (
                    hasattr(os, "geteuid") and os.geteuid() == 0
                    and shutil.which("apt") is not None
                    and "outer host denied" not in str(docker_error).lower()
                )
                if retry_install:
                    try:
                        installed, install_detail = await asyncio.wait_for(install_system_dependencies(), timeout=720)
                    except asyncio.TimeoutError:
                        installed, install_detail = False, "automatic host bootstrap timed out"
                    except Exception as exc:
                        installed, install_detail = False, safe_log(exc)
                    logger.info("Automatic host bootstrap during deployment: ok=%s detail=%s", installed, safe_log(install_detail, 1600))
                    if installed:
                        ok, docker_error = await docker_host_preflight()
                if not ok:
                    raise RuntimeError(
                        "Docker is required to create this systemd VPS but the daemon is not ready. "
                        + safe_log(docker_error)
                    )
            await update_progress(progress, 3, "Pulling official image", os_type=normalized_os, location=normalized_location, ram=ram, cpu=cpu, disk=disk, name=name)
            pulled, pull_error = await docker_pull(image)
            if not pulled:
                return False, f"Could not pull `{image}`. {pull_error}", None
            runtime_ok, runtime_error = await docker_runtime_preflight(image)
            if not runtime_ok:
                logger.error("Docker runtime preflight failed for %s: %s", image, safe_log(runtime_error))
                return False, safe_log(runtime_error), None
            await update_progress(progress, 4, "Creating isolated VPS", os_type=normalized_os, location=normalized_location, ram=ram, cpu=cpu, disk=disk, name=name)
            resource_id, create_error = await docker_run(image=image, hostname=hostname, ram=ram, cpu=cpu, disk=disk, container_name=name, location=normalized_location, ssh_password=ssh_password)
            if not resource_id:
                return False, f"Docker container creation failed: {create_error}", None
            await update_progress(progress, 5, "Starting VPS", os_type=normalized_os, location=normalized_location, ram=ram, cpu=cpu, disk=disk, name=name)
            # `docker run --detach` already starts the container. Only call
            # `docker start` when the runtime reports that it is not running;
            # this avoids the common "container is already running" failure.
            running, start_error = await ensure_docker_running(resource_id)
            if not running:
                raise RuntimeError(start_error or "Container could not be started.")
            ready = False
            for _ in range(20):
                if await docker_state(resource_id) == "running":
                    ready = True
                    break
                await asyncio.sleep(0.5)
            if not ready:
                raise RuntimeError("Container started but did not reach running state.")
            if runtime_bool("guest_systemd_enabled"):
                await update_progress(progress, 6, "Initializing Linux services", os_type=normalized_os, location=normalized_location, ram=ram, cpu=cpu, disk=disk, name=name)
                guest_ready, guest_error = await wait_for_guest_ready(resource_id)
                if not guest_ready:
                    raise RuntimeError(guest_error or "Guest Linux services failed to initialize.")
            # Persist the VPS immediately after Docker reports it as running.
            # Console access is strictly optional and must NEVER be allowed to
            # turn a successful VPS creation into a failure/rollback.
            await update_progress(progress, 7, "Saving VPS record", os_type=normalized_os, location=normalized_location, ram=ram, cpu=cpu, disk=disk, name=name)
            db_upsert_user(user.id, str(user))
            persisted_vps_id = db_insert_vps(
                user_id=user.id,
                container_id=resource_id,
                container_name=name,
                os_type=normalized_os,
                location=normalized_location,
                hostname=hostname,
                ram=ram,
                cpu=cpu,
                disk=disk,
                sshx_url=None,
                sshx_pid=None,
                ssh_password=ssh_password,
                public_ipv4=verified_ipv4 if valid_public_ipv4(verified_ipv4) else None,
                ipv4_verified_at=utc_now() if valid_public_ipv4(verified_ipv4) else None,
                backend="docker",
                ptero_server_id=None,
                ptero_identifier=None,
                ptero_user_id=None,
            )
            row = db_find_vps(user.id, resource_id)
            if not row:
                raise RuntimeError("VPS was created but could not be saved to SQLite.")
            ssh_forward_ok, ssh_forward_message, ssh_host_port = await ensure_ssh_forward(row)
            if not ssh_forward_ok:
                logger.warning("Automatic SSH forwarding failed for VPS #%s: %s", row["id"], safe_log(ssh_forward_message))
            row = db_get_vps(row["id"]) or row

            # Best-effort background-style console setup. Every exception is
            # contained here; SSHx availability is NOT part of VPS readiness.
            console = None
            await update_progress(progress, 8, "Preparing optional console access", os_type=normalized_os, location=normalized_location, ram=ram, cpu=cpu, disk=disk, name=name)
            try:
                console = await asyncio.wait_for(install_and_start_sshx(resource_id), timeout=max(20, runtime_int("sshx_total_timeout") + 5))
            except Exception as exc:
                logger.warning("SSHx setup failed for %s; VPS remains healthy and will be retried: %s", clean(resource_id, 32), safe_log(exc))
                console = None
            if not console:
                asyncio.create_task(_retry_sshx_until_ready(resource_id), name=f"sshx-retry-{str(resource_id)[:12]}")

            if console and console.get("pid"):
                db_update_vps(resource_id, sshx_url=normalize_sshx_url(console.get("url")) if console.get("url") else None, sshx_pid=console.get("pid"))

            # Port supervision is useful but is also non-fatal during first boot.
            try:
                await supervise_vps_ports(db_get_vps(row["id"]) or row)
            except Exception as exc:
                logger.warning("Initial VPS port supervision failed for %s: %s", clean(resource_id, 32), safe_log(exc))

            final_row = db_get_vps(row["id"]) or row
            ssh_port_row = next((p for p in db_list_ports(final_row["id"]) if int(p["container_port"]) == 22 and str(p["protocol"]).lower() == "tcp"), None)
            if ssh_port_row:
                db_update_vps(final_row["container_id"], ssh_command=f"ssh root@<PUBLIC_IP> -p {int(ssh_port_row['host_port'])}")
                final_row = db_get_vps(final_row["id"]) or final_row
            await update_progress(progress, 10, "VPS Ready", os_type=normalized_os, location=normalized_location, ram=ram, cpu=cpu, disk=disk, name=name)
            return True, (
                "RGNODES VPS created successfully."
                + (" Console is ready." if console else " Console is temporarily unavailable; the VPS is online. Use Console/sshx to retry.")
            ), final_row

        except asyncio.CancelledError:
            logger.error("VPS creation cancelled for user %s", user.id)
            if persisted_vps_id and resource_id:
                with contextlib.suppress(Exception):
                    db_delete_vps(resource_id)
            if resource_id and backend == "docker":
                with contextlib.suppress(Exception):
                    await stop_sshx(resource_id)
                with contextlib.suppress(Exception):
                    await docker_remove(resource_id)
            elif ptero_server_id:
                with contextlib.suppress(Exception):
                    await ptero_delete_server(ptero_server_id, force=True)
            raise
        except Exception as exc:
            logger.error("VPS creation failed: %s", safe_log(exc))
            if persisted_vps_id and resource_id:
                with contextlib.suppress(Exception):
                    db_delete_vps(resource_id)
            if resource_id and backend == "docker":
                with contextlib.suppress(Exception):
                    await stop_sshx(resource_id)
                with contextlib.suppress(Exception):
                    await docker_remove(resource_id)
            elif ptero_server_id:
                with contextlib.suppress(Exception):
                    await ptero_delete_server(ptero_server_id, force=True)
            return False, f"VPS creation failed safely: {safe_log(exc)}", None


async def ptero_delete_server(server_id: int, force: bool = False) -> tuple[bool, str]:
    suffix = "?force=true" if force else ""
    status, body = await ptero_request("DELETE", f"application/servers/{int(server_id)}{suffix}")
    if status not in {200, 204}:
        return False, ptero_error_message(status, body)
    return True, "Pterodactyl server deleted."


async def ptero_suspend_server(server_id: int, suspended: bool) -> tuple[bool, str]:
    action = "suspend" if suspended else "unsuspend"
    status, body = await ptero_request("POST", f"application/servers/{int(server_id)}/{action}")
    if status not in {200, 204}:
        return False, ptero_error_message(status, body)
    return True, f"Pterodactyl server {action}ed."


async def docker_reinstall_vps(vps: sqlite3.Row, os_type: str) -> tuple[bool, str]:
    """Replace a Docker VPS container with a clean container using the selected OS.

    The VPS database row, ID, allocation and user ownership are preserved. The
    container itself is recreated from the selected image.
    """
    normalized = normalize_os(os_type)
    if not normalized:
        return False, "Unsupported operating system."
    old_container = str(vps["container_id"])
    image = str(OS_CONFIG[normalized]["image"])
    new_container = f"{str(vps['container_name'])[:48]}-reinstall-{int(time.time()) % 100000}"[:63]
    old_exists = await docker_exists(old_container)
    new_exists = await docker_exists(new_container)
    if new_exists:
        with contextlib.suppress(Exception):
            await docker_remove(new_container)
    if old_exists:
        await stop_sshx(old_container)
        for p_row in db_list_ports(vps["id"]):
            await stop_port_forward(p_row)
        if not await docker_stop(old_container):
            state_now = await docker_state(old_container)
            if state_now not in {"exited", "stopped", None}:
                return False, "Could not stop the current VPS before reinstall."
    reinstall_ssh_password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(SSH_PASSWORD_LENGTH))
    try:
        if DOCKER_RUNTIME_PREFLIGHT and runtime_bool("guest_systemd_enabled"):
            runtime_ok, runtime_error = await docker_runtime_preflight(image)
            if not runtime_ok:
                if old_exists:
                    with contextlib.suppress(Exception):
                        await docker_start(old_container)
                return False, safe_log(runtime_error)
        created_id, err = await docker_run(
            image=image,
            container_name=new_container,
            ram=str(vps["ram"]),
            cpu=str(vps["cpu"]),
            disk=str(vps["disk"]),
            hostname=str(vps["hostname"]),
            location=str(vps["location"]),
            persistent_key=str(vps["container_name"]),
            ssh_password=reinstall_ssh_password,
        )
        if not created_id:
            # Roll the old VPS back to running state so a failed reinstall does
            # not unnecessarily leave the user's service offline.
            if old_exists:
                with contextlib.suppress(Exception):
                    await docker_start(old_container)
            return False, f"Reinstall failed while creating the new container: {err or 'Docker run failed.'}"
        # Keep the old container until the replacement passes the full
        # readiness gate. It remains stopped, so persistent volumes are not
        # written by two containers at the same time.
        container_ref = str(created_id)
        for _ in range(30):
            if await docker_state(container_ref) == "running":
                break
            await asyncio.sleep(0.5)
        else:
            with contextlib.suppress(Exception):
                await docker_remove(container_ref)
            if old_exists:
                with contextlib.suppress(Exception):
                    await docker_start(old_container)
            return False, "Reinstall container did not reach running state; the previous VPS was restored."
        if runtime_bool("guest_systemd_enabled"):
            guest_ready, guest_error = await wait_for_guest_ready(container_ref)
            if not guest_ready:
                with contextlib.suppress(Exception):
                    await docker_remove(container_ref)
                if old_exists:
                    with contextlib.suppress(Exception):
                        await docker_start(old_container)
                return False, f"Reinstall guest bootstrap failed: {guest_error}"
        console = await install_and_start_sshx(container_ref)
        if old_exists:
            if not await docker_remove(old_container):
                logger.warning(
                    "Old reinstall container %s could not be removed after successful readiness; keeping it stopped.",
                    clean(old_container, 48),
                )
        conn = db_connect()
        try:
            conn.execute(
                "UPDATE vps SET container_id=?, container_name=?, os_type=?, status='running', suspended=0, sshx_url=?, sshx_pid=?, ssh_password=?, updated_at=? WHERE id=?",
                (container_ref, new_container, normalized, console.get("url") if console else None, console.get("pid") if console else None, reinstall_ssh_password, utc_now(), int(vps["id"])),
            )
        finally:
            conn.close()
        latest = db_get_vps(vps["id"])
        if latest:
            await supervise_vps_ports(latest)
        return True, f"VPS reinstalled successfully with **{os_label(normalized)}**."
    except Exception as exc:
        logger.exception("Docker reinstall failed for VPS #%s", vps["id"])
        with contextlib.suppress(Exception):
            await docker_remove(locals().get("container_ref", new_container))
        return False, f"Reinstall failed safely: {safe_log(exc)}"


async def lifecycle_action(vps: sqlite3.Row, action: str) -> tuple[bool, str]:
    async with vps_lock(vps["id"]):
        backend = str(vps["backend"] or "docker").lower()

        if backend == "pterodactyl":
            server_id = int(vps["ptero_server_id"] or 0)
            if not server_id:
                return False, "Pterodactyl server ID is missing from this VPS record."

            if action in {"start", "stop", "restart"}:
                if action == "start" and vps["suspended"]:
                    return False, "This VPS is suspended by an administrator."
                if action == "start" and not vps["suspended"]:
                    current_state = await ptero_status(vps)
                    if current_state != "running":
                        running_count = sum(
                            1 for row in db_get_all_vps()
                            if str(row["backend"] or "docker").lower() == "pterodactyl"
                            and str(row["status"]).lower() == "running"
                            and not row["suspended"]
                        )
                        if running_count >= runtime_int('total_running_limit') and not (ADMIN_BYPASS_LIMITS and int(vps["user_id"]) == int(ADMIN_ID)):
                            return False, f"Global running VPS limit reached ({runtime_int('total_running_limit')})."
                ok, message = await ptero_power(vps, action)
                if ok:
                    await asyncio.sleep(1)
                    status_now = await ptero_status(vps)
                    db_update_vps(vps["container_id"], status=status_now, sshx_url=await ptero_panel_link(vps))
                    return True, message
                return False, message

            if action == "suspend":
                ok, message = await ptero_suspend_server(server_id, True)
                if ok:
                    db_update_vps(vps["container_id"], status="stopped", suspended=1)
                return ok, message

            if action == "unsuspend":
                ok, message = await ptero_suspend_server(server_id, False)
                if ok:
                    db_update_vps(vps["container_id"], suspended=0)
                return ok, message

            if action == "delete":
                for p_row in db_list_ports(vps["id"]):
                    await stop_port_forward(p_row)
                ok, message = await ptero_delete_server(server_id, force=False)
                if not ok and "not found" in message.lower():
                    ok, message = await ptero_delete_server(server_id, force=True)
                if ok:
                    db_delete_vps(vps["container_id"])
                return ok, message

            if action == "reinstall":
                status, body = await ptero_request("POST", f"application/servers/{server_id}/reinstall")
                if status not in {200, 204}:
                    return False, ptero_error_message(status, body)
                return True, "Pterodactyl reinstall requested."

            if action == "rebuild":
                status, body = await ptero_request("POST", f"application/servers/{server_id}/rebuild")
                if status not in {200, 204}:
                    return False, ptero_error_message(status, body)
                return True, "Pterodactyl rebuild requested."

            return False, "Unsupported Pterodactyl VPS action."

        container = vps["container_id"]
        exists = await docker_exists(container)
        if action == "start":
            if vps["suspended"]:
                return False, "This VPS is suspended by an administrator."
            if not exists:
                return False, "The Docker container no longer exists. Ask an administrator to recreate this VPS."
            async with CAPACITY_LOCK:
                _, current = await docker_running_count()
                already_running = (await docker_state(container)) == "running"
                if not already_running and current >= runtime_int('total_running_limit'):
                    return False, f"Global running VPS limit reached ({runtime_int('total_running_limit')})."
                ok, error = await docker_start(container)
            if not ok:
                return False, error or "Failed to start the VPS."
            for _ in range(20):
                if await docker_state(container) == "running":
                    break
                await asyncio.sleep(0.5)
            else:
                return False, "The VPS start command returned, but the container is not running."
            if runtime_bool("guest_systemd_enabled"):
                ready, detail = await wait_for_guest_ready(container)
                if not ready:
                    return False, f"VPS started but guest services are not ready: {safe_log(detail)}"
            console = await install_and_start_sshx(container)
            existing = db_get_vps(vps["id"]) or vps
            db_update_vps(container, status="running", sshx_url=console["url"] if console else existing["sshx_url"], sshx_pid=console.get("pid") if console else existing["sshx_pid"])
            await supervise_vps_ports(vps)
            return True, "VPS started. Console refreshed." if console else "VPS started; press Console to retry SSHx."

        if action == "stop":
            if exists:
                await stop_sshx(container)
                for p_row in db_list_ports(vps["id"]):
                    await stop_port_forward(p_row)
                if not await docker_stop(container) and await docker_state(container) not in {"exited", "stopped"}:
                    return False, "Failed to stop the VPS."
            db_update_vps(container, status="stopped", sshx_url=None, sshx_pid=None)
            return True, "VPS stopped successfully."

        if action == "restart":
            if not exists:
                return False, "The Docker container no longer exists."
            async with CAPACITY_LOCK:
                _, current = await docker_running_count()
                if current >= runtime_int('total_running_limit') and (await docker_state(container)) != "running":
                    return False, f"Global running VPS limit reached ({runtime_int('total_running_limit')})."
                await stop_sshx(container)
                ok, error = await docker_restart(container)
            if not ok:
                return False, error or "Failed to restart the VPS."
            for _ in range(20):
                if await docker_state(container) == "running":
                    break
                await asyncio.sleep(0.5)
            else:
                return False, "The VPS restart command returned, but the container is not running."
            if runtime_bool("guest_systemd_enabled"):
                ready, detail = await wait_for_guest_ready(container)
                if not ready:
                    return False, f"VPS restarted but guest services are not ready: {safe_log(detail)}"
            console = await install_and_start_sshx(container)
            existing = db_get_vps(vps["id"]) or vps
            db_update_vps(container, status="running", sshx_url=console["url"] if console else existing["sshx_url"], sshx_pid=console.get("pid") if console else existing["sshx_pid"])
            await supervise_vps_ports(vps)
            return True, "VPS restarted successfully." if console else "VPS restarted; press Console to retry SSHx."

        if action == "reinstall":
            return False, "Select an operating system from the Reinstall menu first."

        if action == "delete":
            for p_row in db_list_ports(vps["id"]):
                await stop_port_forward(p_row)
            if exists:
                await stop_sshx(container)
                if not await docker_remove(container):
                    return False, "Docker cleanup failed; the VPS record was kept."
            db_delete_vps(container)
            return True, "VPS deleted successfully."

        if action == "suspend":
            if exists:
                await stop_sshx(container)
                for p_row in db_list_ports(vps["id"]):
                    await stop_port_forward(p_row)
                stopped = await docker_stop(container)
                if not stopped:
                    state_now = await docker_state(container)
                    if state_now not in {"exited", "stopped", None}:
                        return False, "Failed to stop the VPS before suspension."
            db_update_vps(container, status="stopped", suspended=1, sshx_url=None, sshx_pid=None)
            return True, "VPS stopped and suspended."

        if action == "unsuspend":
            db_update_vps(container, suspended=0)
            return True, "VPS unsuspended."

        return False, "Unsupported VPS action."


async def create_console_access(vps: sqlite3.Row, user: discord.User | discord.Member) -> tuple[bool, str]:
    backend = str(vps["backend"] or "docker").lower()
    if backend == "pterodactyl":
        status = await ptero_status(vps)
        if status != "running":
            return False, "Start the Pterodactyl server before opening Console."
        url = await ptero_panel_link(vps)
        if not url:
            return False, "Pterodactyl panel URL is not configured."
        db_update_vps(vps["container_id"], status="running", sshx_url=url, sshx_pid=None)
        sent = await safe_dm(user, make_embed("✨ RGNODES™ • 🦖 Pterodactyl Panel", "Open your VPS panel from the button below."), sshx_view(url))
        return True, "Pterodactyl panel access link sent by DM." if sent else "Pterodactyl panel is ready, but your DM is closed."

    state = await docker_state(vps["container_id"])
    if state != "running":
        db_update_vps(vps["container_id"], status="stopped", sshx_url=None, sshx_pid=None)
        return False, "Start the VPS before opening Console."
    try:
        console = await asyncio.wait_for(
            install_and_start_sshx(vps["container_id"]),
            timeout=max(20, runtime_int("sshx_total_timeout") + 5),
        )
    except asyncio.TimeoutError:
        console = None
        logger.warning("SSHx Console request timed out for VPS %s", vps["id"])
    if not console:
        existing_url = normalize_sshx_url(vps["sshx_url"]) if vps["sshx_url"] else None
        existing_pid = str(vps["sshx_pid"] or "")
        if existing_url and existing_pid and await sshx_process_alive(vps["container_id"], existing_pid):
            return True, "Private SSHx link is still active and was kept unchanged."
        db_update_vps(vps["container_id"], sshx_url=None, sshx_pid=None)
        return False, "SSHx could not start a console session. The VPS is still online. Press Console again after checking SSHx network/launch logs."

    console_pid = str(console.get("pid") or "")
    console_url = normalize_sshx_url(console.get("url")) if console.get("url") else None

    # The launcher is intentionally non-blocking. When SSHx has started but
    # its encrypted browser URL has not been emitted yet, read the persisted
    # state once more after a very short delay. This does not turn deployment
    # into a long polling loop.
    if console_pid and not console_url:
        await asyncio.sleep(0.35)
        late_url, late_pid = await _read_sshx_state(vps["container_id"])
        if late_url and late_pid:
            console_url, console_pid = late_url, late_pid

    if not console_url:
        if console_pid and await sshx_process_alive(vps["container_id"], console_pid):
            db_update_vps(vps["container_id"], sshx_url=None, sshx_pid=console_pid)
            return False, "SSHx is running and initializing its encrypted console link. Press Console again in a moment."
        db_update_vps(vps["container_id"], sshx_url=None, sshx_pid=None)
        return False, "SSHx started incorrectly and stopped. Check the VPS SSHx log and press Console again."

    db_update_vps(vps["container_id"], sshx_url=console_url, sshx_pid=console_pid or None)
    network = await detect_public_network(force=True)
    ip_ok = valid_public_ipv4(network.get("ip"))
    if ip_ok:
        db_set_vps_ipv4(vps["container_id"], network["ip"])
    dm_sent = await safe_dm(user, console_embed(vps["container_name"], console_url, network.get("ip") if ip_ok else None, actual_location_label(network)), sshx_view(console_url))
    if ip_ok:
        await safe_dm(user, ipv4_dm_embed(vps, network))
    return (True, "Private SSHx link generated and sent to your DM." if dm_sent else "Console is ready, but your DM is closed. Enable DMs and press Console again.")


# ================================================================
# Public network identity + real host location
# ================================================================

NETWORK_CACHE: dict[str, str] = {"ip": "N/A", "country": "N/A", "region": "N/A", "city": "N/A"}
NETWORK_CACHE_AT = 0.0
NETWORK_LOCK = asyncio.Lock()


def valid_public_ipv4(value: str | None) -> bool:
    try:
        ip = ipaddress.ip_address(str(value or "").strip())
        return isinstance(ip, ipaddress.IPv4Address) and ip.is_global
    except ValueError:
        return False


def local_ipv4_addresses() -> set[str]:
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(item[4][0])
    except OSError:
        pass
    # iproute2 is already a bootstrap dependency; use it when available so
    # cloud secondary IPv4 addresses are detected reliably.
    return addresses


def _fetch_real_public_ipv4_sync() -> str | None:
    """Return an externally observed, globally routable IPv4 only after quorum.

    This is the host's real Internet egress/public IPv4. We intentionally do
    not invent or derive a public address from private container addresses.
    A value must be independently observed by at least two providers.
    """
    headers = {"User-Agent": "RGNODES-VPS/IPv4-verify"}
    endpoints = (
        "https://api.ipify.org",
        "https://icanhazip.com",
        "https://ifconfig.me/ip",
        "https://checkip.amazonaws.com",
    )
    observations: list[str] = []
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=7) as resp:
                value = resp.read().decode("utf-8", "replace").strip()
            # Reject anything containing extra text, not only invalid ipaddress objects.
            if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value) and valid_public_ipv4(value):
                observations.append(value)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
    counts: dict[str, int] = {}
    for value in observations:
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    winner, votes = max(counts.items(), key=lambda item: item[1])
    # Two independent confirmations are required for a verified address.
    return winner if votes >= 2 else None


async def real_public_ipv4(force: bool = False) -> str | None:
    global NETWORK_CACHE_AT, NETWORK_CACHE
    now = asyncio.get_running_loop().time()
    cached = str(NETWORK_CACHE.get("ip") or "")
    if not force and NETWORK_CACHE_AT and now - NETWORK_CACHE_AT < IPV4_REFRESH and valid_public_ipv4(cached):
        return cached
    ip = await asyncio.to_thread(_fetch_real_public_ipv4_sync)
    if ip:
        NETWORK_CACHE["ip"] = ip
        NETWORK_CACHE_AT = asyncio.get_running_loop().time()
    return ip


async def detect_public_network(force: bool = False) -> dict[str, str]:
    global NETWORK_CACHE_AT, NETWORK_CACHE
    now = asyncio.get_running_loop().time()
    if not force and NETWORK_CACHE_AT and now - NETWORK_CACHE_AT < PUBLIC_IP_REFRESH:
        return dict(NETWORK_CACHE)
    async with NETWORK_LOCK:
        now = asyncio.get_running_loop().time()
        if not force and NETWORK_CACHE_AT and now - NETWORK_CACHE_AT < PUBLIC_IP_REFRESH:
            return dict(NETWORK_CACHE)

        verified_ip = await real_public_ipv4(force=force)
        if not valid_public_ipv4(verified_ip):
            # Keep previously verified information only while it is still valid.
            return dict(NETWORK_CACHE)

        def fetch_geo() -> dict[str, str]:
            headers = {"User-Agent": "RGNODES-VPS/1.0"}
            for url in ("https://ipapi.co/json/", "https://ipinfo.io/json"):
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=7) as resp:
                        data = json.loads(resp.read().decode("utf-8", "replace"))
                    # The IP shown to users always comes from quorum verification;
                    # geo providers supply location metadata only.
                    return {
                        "ip": verified_ip,
                        "country": str(data.get("country_name") or data.get("country") or "N/A"),
                        "region": str(data.get("region") or data.get("regionName") or "N/A"),
                        "city": str(data.get("city") or "N/A"),
                    }
                except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
                    continue
            return {"ip": verified_ip, "country": "N/A", "region": "N/A", "city": "N/A"}

        NETWORK_CACHE = fetch_geo()
        NETWORK_CACHE["ip"] = verified_ip
        NETWORK_CACHE_AT = asyncio.get_running_loop().time()
        return dict(NETWORK_CACHE)


def actual_location_label(network: dict[str, str]) -> str:
    country = network.get("country", "N/A")
    if country in {"Singapore", "SG"}:
        return "Singapore 🇸🇬"
    if country in {"India", "IN"}:
        return "India 🇮🇳"
    return clean(country, 64)


# ================================================================
# Port forwarding (10 TCP mappings per VPS, supervised 24/7)
# ================================================================

PORT_LOCK = asyncio.Lock()


def port_in_use(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return True
    finally:
        sock.close()


def port_bindable(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


async def docker_container_ip(container: str) -> str | None:
    rc, out, _ = await docker_cli("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container, timeout=20, retries=1)
    if rc != 0:
        return None
    ip = out.decode("utf-8", "replace").strip().splitlines()[0] if out else ""
    return ip if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", ip) else None


async def process_matches(pid: int, needle: str) -> bool:
    if pid <= 0:
        return False
    try:
        proc_cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        return needle.lower() in proc_cmdline.lower()
    except (OSError, UnicodeError):
        return False


async def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def kill_host_pid(pid: int | None, expected_command: str | None = None) -> None:
    if not pid or int(pid) <= 0:
        return
    pid = int(pid)
    if expected_command and not await process_matches(pid, expected_command):
        return
    if not await process_alive(pid):
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, signal.SIGTERM)
    for _ in range(10):
        if not await process_alive(pid):
            return
        await asyncio.sleep(0.1)
    if await process_alive(pid):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGKILL)


async def allocate_host_port() -> int | None:
    conn = db_connect()
    try:
        reserved = {int(r[0]) for r in conn.execute("SELECT host_port FROM vps_ports")}
    finally:
        conn.close()
    start = max(1024, runtime_int('port_range_start'))
    end = min(65535, runtime_int('port_range_end'))
    for port in range(start, end + 1):
        if port in reserved:
            continue
        if port_bindable(port):
            return port
    return None


async def verify_host_listener(port: int) -> bool:
    """Confirm a local TCP listener exists on the selected host port."""
    try:
        rc, out, _ = await system_command("ss", "-H", "-ltn", timeout=8) if command_available("ss") else (127, "", "")
        if rc == 0:
            for line in out.splitlines():
                if re.search(rf":{int(port)}\b", line):
                    return True
        # Fallback: probe localhost. This does not guarantee Internet reachability
        # but confirms the forwarding process is accepting local TCP connections.
        return await asyncio.to_thread(port_in_use, "127.0.0.1", int(port))
    except Exception:
        return False


async def start_port_forward(port_row: sqlite3.Row, vps: sqlite3.Row) -> tuple[bool, str]:
    if str(port_row["protocol"]).lower() != "tcp":
        return False, "Only TCP forwarding is enabled."
    if await docker_state(vps["container_id"]) != "running":
        db_update_port(port_row["id"], status="stopped", pid=None, target_ip=None)
        return False, "VPS is not running. Start it first."
    if not command_available("socat"):
        return False, "Port forwarding requires `socat`. Run `/install-system confirm` as administrator."

    public_ipv4 = str(vps["public_ipv4"] or "").strip()
    if not valid_public_ipv4(public_ipv4):
        public_ipv4 = await real_public_ipv4(force=True) or ""
        if valid_public_ipv4(public_ipv4):
            db_set_vps_ipv4(vps["container_id"], public_ipv4)
    if not valid_public_ipv4(public_ipv4):
        return False, "Verified real public IPv4 is unavailable; forwarding was not started."

    target_ip = await docker_container_ip(vps["container_id"])
    if not target_ip:
        return False, "Could not determine the VPS container IPv4."
    try:
        target_obj = ipaddress.ip_address(target_ip)
        if not isinstance(target_obj, ipaddress.IPv4Address) or not target_obj.is_private:
            return False, "Container IPv4 validation failed."
    except ValueError:
        return False, "Container IPv4 validation failed."

    host_port = int(port_row["host_port"])
    container_port = int(port_row["container_port"])
    old_pid = int(port_row["pid"]) if str(port_row["pid"] or "").isdigit() else None

    async with PORT_LOCK:
        if old_pid and await process_alive(old_pid) and await process_matches(old_pid, "socat"):
            if str(port_row["target_ip"] or "") == target_ip and await verify_host_listener(host_port):
                db_update_port(port_row["id"], status="running")
                return True, f"Port forwarding is already online on public port `{host_port}`."
            await kill_host_pid(old_pid, "socat")

        if not port_bindable(host_port):
            # It may be the same listener just not represented by our PID; refuse
            # to steal an unrelated service's port.
            db_update_port(port_row["id"], status="error", pid=None, target_ip=target_ip)
            return False, f"Public port `{host_port}` is already in use."

        pid, error = await spawn_detached(
            "socat",
            "-ly",
            f"TCP4-LISTEN:{host_port},bind=0.0.0.0,reuseaddr,fork",
            f"TCP4:{target_ip}:{container_port}",
        )
        if not pid:
            db_update_port(port_row["id"], status="error", pid=None, target_ip=target_ip)
            return False, error or "Could not start the forwarding process."

        await asyncio.sleep(0.25)
        if not await process_alive(pid) or not await process_matches(pid, "socat") or not await verify_host_listener(host_port):
            await kill_host_pid(pid, "socat")
            db_update_port(port_row["id"], status="error", pid=None, target_ip=target_ip)
            return False, f"Forwarding process started but could not be verified on port `{host_port}`."

        db_update_port(port_row["id"], status="running", pid=pid, target_ip=target_ip)
        return True, f"Port forwarding is online: public port `{host_port}` → VPS port `{container_port}/TCP`."


async def stop_port_forward(port_row: sqlite3.Row) -> None:
    await kill_host_pid(int(port_row["pid"]) if port_row["pid"] else None, "socat")
    db_update_port(port_row["id"], status="stopped", pid=None)



async def ensure_ssh_forward(vps: sqlite3.Row) -> tuple[bool, str, int | None]:
    """Guarantee one stable TCP host port for guest SSH/22."""
    if not runtime_bool("auto_create_ssh_forward"):
        return True, "Automatic SSH forwarding is disabled.", None
    existing = db_list_ports(vps["id"])
    for row in existing:
        if int(row["container_port"]) == 22 and str(row["protocol"]).lower() == "tcp":
            if str(row["status"]).lower() == "running":
                return True, "SSH forwarding is already active.", int(row["host_port"])
            ok, msg = await start_port_forward(row, vps)
            return ok, msg, int(row["host_port"]) if ok else None

    conn = db_connect()
    try:
        reserved = {int(r[0]) for r in conn.execute("SELECT host_port FROM vps_ports")}
    finally:
        conn.close()
    for port in range(max(1024, SSH_FORWARD_PORT_START), min(65535, SSH_FORWARD_PORT_END) + 1):
        if port in reserved or not port_bindable(port):
            continue
        now = utc_now()
        conn = db_connect()
        try:
            cur = conn.execute(
                "INSERT INTO vps_ports(vps_id,container_port,host_port,protocol,target_ip,pid,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (int(vps["id"]), 22, port, "tcp", None, None, "stopped", now, now),
            )
            port_id = int(cur.lastrowid)
        except sqlite3.IntegrityError:
            conn.close()
            continue
        finally:
            with contextlib.suppress(Exception):
                conn.close()
        row = next((r for r in db_list_ports(vps["id"]) if int(r["id"]) == port_id), None)
        if not row:
            return False, "SSH forwarding record could not be saved.", None
        ok, msg = await start_port_forward(row, vps)
        return ok, msg, port if ok else None
    return False, "No free host port was available for SSH forwarding.", None

async def supervise_vps_ports(vps: sqlite3.Row) -> None:
    try:
        ports = db_list_ports(vps["id"])
        if not ports:
            return
        running = (await docker_state(vps["container_id"])) == "running"
        for p_row in ports:
            try:
                if running:
                    await start_port_forward(p_row, vps)
                else:
                    await stop_port_forward(p_row)
            except Exception as exc:
                logger.warning("Port supervisor failed for VPS #%s port #%s: %s", vps["id"], p_row["id"], safe_log(exc))
    except Exception as exc:
        logger.warning("Port supervisor unavailable for VPS #%s: %s", vps["id"], safe_log(exc))



# ================================================================
# Docker snapshots
# ================================================================
SNAPSHOT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


async def docker_snapshot_create(vps: sqlite3.Row, name: str) -> tuple[bool, str]:
    if str(vps["backend"] or "docker").lower() != "docker":
        return False, "Snapshots are currently available for Docker VPS instances only. Pterodactyl backups are managed by the panel."
    name = name.strip()
    if not SNAPSHOT_NAME_RE.fullmatch(name):
        return False, "Snapshot name must be 1–64 characters and use only letters, numbers, `.`, `_`, or `-`."
    if db_get_snapshot(vps["id"], name):
        return False, "A snapshot with that name already exists."
    if await docker_state(vps["container_id"]) != "running":
        return False, "Start the VPS before creating a snapshot."
    image_ref = f"rgnodes-snapshot:{int(vps['id'])}-{name.lower()}"
    rc, out, err = await docker_cli("commit", vps["container_id"], image_ref, timeout=180, retries=1)
    if rc != 0:
        return False, f"Docker snapshot failed: {safe_log(err.decode('utf-8', 'replace'))}"
    if not out.decode("utf-8", "replace").strip():
        return False, "Docker did not return a snapshot image ID."
    try:
        db_insert_snapshot(vps["id"], name, image_ref)
    except sqlite3.IntegrityError:
        return False, "Snapshot record already exists."
    return True, f"Snapshot `{name}` created successfully."


async def docker_snapshot_restore(vps: sqlite3.Row, name: str) -> tuple[bool, str]:
    if str(vps["backend"] or "docker").lower() != "docker":
        return False, "Snapshot restore is currently available for Docker VPS instances only."
    snap = db_get_snapshot(vps["id"], name.strip())
    if not snap:
        return False, "Snapshot not found."
    container = vps["container_id"]
    snapshot_image = str(snap["image_ref"])
    rc, _, err = await docker_cli("image", "inspect", snapshot_image, timeout=30, retries=1)
    if rc != 0:
        db_delete_snapshot(vps["id"], name.strip())
        return False, "Snapshot image no longer exists; its stale database record was removed."
    new_name = str(vps["container_name"])
    async with vps_lock(vps["id"]):
        await stop_sshx(container)
        for p_row in db_list_ports(vps["id"]):
            await stop_port_forward(p_row)
        if await docker_exists(container) and not await docker_remove(container):
            return False, "Could not remove the current container safely, so restore was aborted."
        new_container, create_error = await docker_run(
            image=snapshot_image,
            hostname=str(vps["hostname"]),
            ram=str(vps["ram"]),
            cpu=str(vps["cpu"]),
            disk=str(vps["disk"]),
            container_name=new_name,
            location=str(vps["location"]),
            persistent_key=str(vps["container_name"]),
        )
        if not new_container:
            return False, f"Restore failed while recreating the container: {create_error}"
        # `docker run --detach` starts the restored container already.
        # Only start it explicitly when it is not running.
        ok, error = await ensure_docker_running(new_container)
        if not ok:
            await docker_remove(new_container)
            return False, f"Restore created a container but could not start it: {error}"
        for _ in range(20):
            if await docker_state(new_container) == "running":
                break
            await asyncio.sleep(0.5)
        else:
            await docker_remove(new_container)
            return False, "Restored container did not reach running state."
        console = await install_and_start_sshx(new_container)
        # Atomically update the existing VPS record to the restored container.
        conn = db_connect()
        try:
            conn.execute(
                "UPDATE vps SET container_id=?, status='running', suspended=0, sshx_url=?, sshx_pid=?, updated_at=? WHERE id=?",
                (new_container, console["url"] if console else None, console.get("pid") if console else None, utc_now(), int(vps["id"])),
            )
        finally:
            conn.close()
        latest = db_get_vps(vps["id"])
        if latest:
            await supervise_vps_ports(latest)
    return True, f"Snapshot `{name}` restored successfully."


async def snapshot_delete_image(vps: sqlite3.Row, name: str) -> tuple[bool, str]:
    snap = db_get_snapshot(vps["id"], name.strip())
    if not snap:
        return False, "Snapshot not found."
    image_ref = str(snap["image_ref"])
    rc, _, err = await docker_cli("image", "rm", "-f", image_ref, timeout=60, retries=1)
    if rc != 0 and "No such image" not in err.decode("utf-8", "replace"):
        return False, f"Could not remove snapshot image: {safe_log(err.decode('utf-8', 'replace'))}"
    db_delete_snapshot(vps["id"], name.strip())
    return True, f"Snapshot `{name}` deleted."


# ================================================================
# Host/system bootstrap
# ================================================================

SYSTEM_PACKAGE_LOCK = asyncio.Lock()
SYSTEM_PACKAGES = (
    "ca-certificates",
    "curl",
    "bash",
    "coreutils",
    "procps",
    "iproute2",
    "iputils-ping",
    "tar",
    "gzip",
    "unzip",
    "socat",
    "systemd",
    "systemd-sysv",
    "dbus",
)
DOCKER_PACKAGE = "docker.io"


def host_os_info() -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for raw in Path("/etc/os-release").read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in raw or raw.startswith("#"):
                continue
            key, value = raw.split("=", 1)
            data[key] = value.strip().strip('"')
    except (OSError, UnicodeError):
        pass
    try:
        pid1 = Path("/proc/1/comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        pid1 = "unknown"
    return {
        "id": data.get("ID", "unknown"),
        "name": data.get("PRETTY_NAME", data.get("NAME", "Unknown Linux")),
        "version": data.get("VERSION_ID", "unknown"),
        "pid1": pid1 or "unknown",
        "systemd": str(pid1 == "systemd" or Path("/run/systemd/system").exists()).lower(),
        "root": str(os.geteuid() == 0).lower() if hasattr(os, "geteuid") else "unknown",
    }


async def system_command(*args: str, timeout: float = 180) -> tuple[int, str, str]:
    rc, out, err = await run_process(*args, timeout=timeout)
    return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def command_available(name: str) -> bool:
    return shutil.which(name) is not None


async def _probe_docker_info() -> tuple[bool, str]:
    """Probe the Docker CLI/daemon without attempting installation or repair."""
    if not docker_binary():
        return False, "Docker CLI is not installed or is not available in PATH."
    rc, out, err = await docker_cli("info", timeout=30, retries=2)
    if rc == 0:
        return True, out.decode("utf-8", "replace")
    detail = safe_log(err.decode("utf-8", "replace").strip() or out.decode("utf-8", "replace").strip() or "Docker daemon is unavailable.")
    return False, detail


async def docker_daemon_ready() -> tuple[bool, str]:
    return await _probe_docker_info()


async def install_system_dependencies() -> tuple[bool, str]:
    async with SYSTEM_PACKAGE_LOCK:
        info = host_os_info()
        if info["root"] != "true":
            return False, "Administrator/root privileges are required. Run the bot as root or grant it the required host permissions."

        package_manager = shutil.which("apt")
        if not package_manager:
            if shutil.which("apk"):
                return False, "This host uses Alpine/apk. Automatic bootstrap is intentionally limited to Debian/Ubuntu apt hosts."
            return False, "No supported package manager was found. Supported automatic bootstrap: Debian/Ubuntu with apt."

        os_id = info["id"].lower()
        if os_id not in {"debian", "ubuntu", "linuxmint", "pop", "raspbian"}:
            return False, f"Unsupported host OS for automatic bootstrap: `{info['name']}`."

        messages: list[str] = [f"Host: {info['name']}", f"PID 1: `{info['pid1']}`"]
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"

        async def apt(*args: str, timeout: float = 300) -> tuple[int, str, str]:
            return await system_command(
                "env", "DEBIAN_FRONTEND=noninteractive", "apt", *args,
                timeout=timeout,
            )

        # Do not assume systemd exists just because systemctl exists. This is
        # important in Docker/containers/WSL-like environments.
        rc, _, err = await apt("update", "-y", timeout=300)
        if rc != 0:
            return False, "apt update failed:\n" + safe_log(err.strip() or "unknown apt error")

        # command/package names differ (ca-certificates has no binary check).
        # Install the full small base set; apt safely skips packages already installed.
        rc, out, err = await apt("install", "-y", "--no-install-recommends", *SYSTEM_PACKAGES, timeout=360)
        if rc != 0:
            return False, "Base dependency installation failed:\n" + safe_log(err.strip() or out.strip() or "unknown apt error")
        messages.append("Base Linux dependencies: installed/verified.")

        # Install Docker only when the CLI is actually missing. Existing Docker
        # installations are never replaced by this command.
        if not docker_binary():
            rc, out, err = await apt("install", "-y", "--no-install-recommends", DOCKER_PACKAGE, timeout=360)
            if rc != 0:
                return False, "Docker installation failed:\n" + safe_log(err.strip() or out.strip() or "unknown apt error")
            messages.append("Docker CLI: installed from the distro package.")
        else:
            messages.append("Docker CLI: already present.")

        # Refresh PATH-dependent checks after package installation.
        docker_bin = docker_binary()
        if not docker_bin:
            return False, "\n".join(messages + ["Docker CLI is still unavailable after installation."])

        if info["systemd"] == "true" and command_available("systemctl"):
            rc_unit, _, _ = await system_command("systemctl", "list-unit-files", "docker.service", timeout=30)
            if rc_unit == 0:
                rc_start, out_start, err_start = await system_command(
                    "systemctl", "enable", "--now", "docker.service", timeout=90
                )
                if rc_start == 0:
                    messages.append("Docker service: enabled and started via systemd.")
                else:
                    messages.append("Docker service: systemd detected, but start failed: " + safe_log(err_start.strip() or out_start.strip() or "unknown error"))
            else:
                messages.append("systemd: running, but docker.service was not found; Docker daemon may be socket-managed or externally managed.")
        else:
            messages.append(
                "systemd: not active as PID 1. The command did not attempt `systemctl` "
                "because systemd cannot manage services from this environment."
            )

        ready, docker_detail = await _probe_docker_info()
        if ready:
            messages.append("Docker daemon: ✅ reachable.")
        else:
            messages.append("Docker daemon: ⚠️ not reachable.")
            messages.append("Reason: " + safe_log(docker_detail))

        messages.append("Install-system completed without modifying VPS containers.")
        return ready, "\n".join(messages)


# ================================================================
# Protection runtime configuration bridge
# ================================================================
PROTECTION_CONFIG_FILE = PROJECT_ROOT / "config" / "config.json"
PROTECTION_KEYS = {
    "monitor_interval": ("root", float, 0.5, 60.0),
    "thresholds.cpu_percent": ("thresholds", float, 1, 100),
    "thresholds.memory_percent": ("thresholds", float, 1, 100),
    "thresholds.disk_percent": ("thresholds", float, 1, 100),
    "thresholds.connections_per_ip": ("thresholds", int, 1, 100000),
    "thresholds.connection_events_per_minute": ("thresholds", int, 1, 100000),
    "thresholds.syn_like_states_per_ip": ("thresholds", int, 1, 100000),
    "thresholds.global_connection_events_per_minute": ("thresholds", int, 1, 1_000_000),
    "thresholds.block_score": ("thresholds", int, 1, 100),
    "thresholds.repeat_offense_window": ("thresholds", int, 1, 86400),
    "thresholds.repeat_offense_count": ("thresholds", int, 1, 100),
    "protection.auto_block": ("protection", bool, None, None),
    "protection.block_seconds": ("protection", int, 30, 604800),
    "protection.max_blocks": ("protection", int, 1, 100000),
    "protection.max_alerts_per_minute": ("protection", int, 1, 100000),
    "protection.firewall_backend": ("protection", str, None, None),
    "protection.auto_stop_confirmed_miners": ("protection", bool, None, None),
    "miner_detection.enabled": ("miner_detection", bool, None, None),
    "miner_detection.confirm_cpu_percent": ("miner_detection", float, 1, 100),
}

def _protection_load() -> dict[str, object]:
    try:
        data=json.loads(PROTECTION_CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data,dict) else {}
    except (OSError,json.JSONDecodeError):
        return {}

def _protection_get(data: dict[str, object], key: str):
    cur: object=data
    for part in key.split("."):
        if not isinstance(cur,dict): return None
        cur=cur.get(part)
    return cur

def set_protection_setting(key: str, raw: str) -> str:
    if key not in PROTECTION_KEYS: raise KeyError(key)
    _, typ, minimum, maximum=PROTECTION_KEYS[key]
    text=str(raw).strip()
    if typ is bool:
        low=text.lower()
        if low not in {"true","false","1","0","yes","no","on","off"}: raise ValueError("Boolean value must be true/false.")
        value=low in {"true","1","yes","on"}; normalized=str(value).lower()
    elif typ is int:
        value=int(text)
        if minimum is not None and value<minimum or maximum is not None and value>maximum: raise ValueError("Value is outside the allowed range.")
        normalized=str(value)
    elif typ is float:
        value=float(text)
        if minimum is not None and value<minimum or maximum is not None and value>maximum: raise ValueError("Value is outside the allowed range.")
        normalized=f"{value:g}"; value=float(value)
    else:
        value=text
        normalized=text
        if key.endswith("firewall_backend") and text.lower() not in {"auto","nftables","iptables"}: raise ValueError("Firewall backend must be auto, nftables, or iptables.")
    data=_protection_load()
    parts=key.split("."); cur=data
    if parts[0] == "root":
        # "root" is an internal marker for a top-level JSON setting.
        data["monitor_interval"] = value
    else:
        cur=data.setdefault(parts[0],{})
        for part in parts[1:-1]: cur=cur.setdefault(part,{})
        cur[parts[-1]]=value
    tmp=PROTECTION_CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
    os.replace(tmp,PROTECTION_CONFIG_FILE)
    return normalized

def protection_setting_rows() -> list[tuple[str,object]]:
    data=_protection_load()
    return [(k,_protection_get(data,k)) for k in sorted(PROTECTION_KEYS)]

# ================================================================
# Bot + UI
# ================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


def dynamic_prefix(bot_instance: commands.Bot, message: discord.Message):
    """Admins may use ! for administration and - for user commands."""
    author = getattr(message, "author", None)
    if author is not None and ADMIN_ID > 0 and int(author.id) == int(ADMIN_ID):
        return ["-", "!"]
    return ["-"]


class RGNODESBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix=dynamic_prefix, intents=intents, help_command=None)
        self.synced = False
        self.loops_started = False


bot = RGNODESBot()

@bot.tree.command(name="protection-config", description="Admin: show or change protection settings.")
@app_commands.describe(action="show or set", key="Protection key", value="New value")
async def protection_config_slash(interaction: discord.Interaction, action: str="show", key: str="", value: str=""):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    action=action.strip().lower(); key=key.strip().lower().replace("-","_")
    if action == "show":
        lines=[f"`{k}` → `{v}`" for k,v in protection_setting_rows()]
        await safe_respond(interaction,embed=make_embed("🛡️ Protection Configuration","\n".join(lines))); return
    try:
        if action != "set": raise ValueError("Use `show` or `set`.")
        normalized=set_protection_setting(key,value)
        await safe_respond(interaction,embed=make_embed("✅ Protection Setting Updated",f"`{key}` → `{normalized}`\nThe protection agent reloads `config.json` automatically; changes normally apply within one monitor cycle."))
    except (KeyError,ValueError,OSError) as exc:
        await safe_respond(interaction,embed=make_embed("❌ Protection Config Failed",safe_log(exc)))


@bot.command(name="protection-config")
async def prefix_protection_config(ctx: commands.Context, action: str="show", key: str="", *, value: str=""):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    action=action.strip().lower(); key=key.strip().lower().replace("-","_")
    if action == "show":
        await safe_ctx_send(ctx,make_embed("🛡️ Protection Configuration","\n".join(f"`{k}` → `{v}`" for k,v in protection_setting_rows()))); return
    try:
        normalized=set_protection_setting(key,value)
        await safe_ctx_send(ctx,make_embed("✅ Protection Setting Updated",f"`{key}` → `{normalized}`\nThe protection agent reloads `config.json` automatically; changes normally apply within one monitor cycle."))
    except (KeyError,ValueError,OSError) as exc:
        await safe_ctx_send(ctx,make_embed("❌ Protection Config Failed",safe_log(exc)))

CLAIMED_INTERACTION_IDS: set[str] = set()
INTERACTION_GUARD_LOCK = asyncio.Lock()

async def claim_interaction_once(interaction: discord.Interaction) -> bool:
    """Atomically allow exactly one response pipeline per Discord interaction."""
    interaction_id = getattr(interaction, "id", None)
    if interaction_id is None:
        return True
    key = str(interaction_id)
    async with INTERACTION_GUARD_LOCK:
        if key in CLAIMED_INTERACTION_IDS:
            return False
        if not claim_processed_event(f"interaction:{key}", "interaction"):
            return False
        CLAIMED_INTERACTION_IDS.add(key)
        if len(CLAIMED_INTERACTION_IDS) > 20000:
            CLAIMED_INTERACTION_IDS.clear()
            CLAIMED_INTERACTION_IDS.add(key)
        return True


class ReinstallView(discord.ui.View):
    def __init__(self, vps_id: int, owner_id: int):
        super().__init__(timeout=300)
        self.vps_id = int(vps_id)
        self.owner_id = int(owner_id)
        self.os_select = discord.ui.Select(
            placeholder="Select OS for clean reinstall",
            min_values=1, max_values=1, row=0,
            options=[
                discord.SelectOption(label=c["label"], value=k, emoji="🟠" if k.startswith("ubuntu") else "🔵")
                for k, c in OS_CONFIG.items()
            ],
        )
        self.confirm_button = discord.ui.Button(label="Confirm Reinstall", emoji="♻️", style=discord.ButtonStyle.danger, row=1)
        self.cancel_button = discord.ui.Button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary, row=1)
        self.os_select.callback = self.select_os
        self.confirm_button.callback = self.confirm
        self.cancel_button.callback = self.cancel
        self.add_item(self.os_select); self.add_item(self.confirm_button); self.add_item(self.cancel_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        vps = db_get_vps(self.vps_id)
        allowed = bool(vps and (interaction.user.id in {self.owner_id, ADMIN_ID} or db_find_accessible_vps(interaction.user.id, str(self.vps_id))))
        if allowed:
            return True
        await safe_respond(interaction, embed=make_embed("❌ Access Denied", "You do not have access to this VPS."))
        return False

    async def select_os(self, interaction: discord.Interaction) -> None:
        value = self.os_select.values[0]
        await safe_component_edit(
            interaction,
            embed=make_embed("♻️ Reinstall VPS", f"Selected OS: **{os_label(value)}**\n\nThis performs a clean reinstall and replaces the current container. Existing VPS data inside the container will be lost.\n\nPress **Confirm Reinstall** to continue."),
            view=self,
        )

    async def confirm(self, interaction: discord.Interaction) -> None:
        if not await claim_interaction_once(interaction):
            return
        selected = self.os_select.values[0] if self.os_select.values else ""
        if not selected:
            await safe_respond(interaction, embed=make_embed("⚠️ Select OS", "Choose an operating system before confirming reinstall."))
            return
        if not await safe_defer(interaction, ephemeral=True, claim=False):
            return
        try:
            vps = db_get_vps(self.vps_id)
            if not vps:
                await safe_edit_original(interaction, embed=make_embed("❌ VPS Not Found", "This VPS no longer exists."), view=None)
                return
            backend = str(vps["backend"] or "docker").lower()
            if backend == "docker":
                async with vps_lock(self.vps_id):
                    ok, message = await docker_reinstall_vps(vps, selected)
            elif backend == "pterodactyl":
                # lifecycle_action() owns its own VPS lock; do not nest it here.
                ok, message = await lifecycle_action(vps, "reinstall")
                if ok:
                    message = f"Pterodactyl reinstall requested. The panel controls the server image; OS selector **{os_label(selected)}** was informational only."
            else:
                ok, message = False, "Unsupported VPS backend."
            if ok:
                latest = db_get_vps(self.vps_id) or vps
                stats, uptime, disk, network, ports = await _dashboard_live_data(latest)
                await safe_edit_original(interaction, embed=dashboard_embed(latest, stats, uptime, disk, network, ports), view=ManageView(latest["id"], latest["user_id"]))
            else:
                await safe_edit_original(interaction, embed=make_embed("❌ Reinstall Failed", message), view=ManageView(vps["id"], vps["user_id"]))
        finally:
            self.stop()

    async def cancel(self, interaction: discord.Interaction) -> None:
        await safe_component_edit(
            interaction,
            embed=make_embed("♻️ Reinstall Cancelled", "No changes were made to this VPS."),
            view=None,
        )
        self.stop()


class ManageView(discord.ui.View):
    def __init__(self, vps_id: int, owner_id: int):
        super().__init__(timeout=900)
        self.vps_id = int(vps_id)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        vps = db_get_vps(self.vps_id)
        allowed = bool(vps and (
            int(interaction.user.id) in {int(self.owner_id), int(ADMIN_ID)}
            or db_find_accessible_vps(int(interaction.user.id), str(self.vps_id))
        ))
        if allowed:
            return True
        await safe_respond(
            interaction,
            embed=make_embed("❌ Access Denied", "You do not have access to this VPS."),
        )
        return False

    async def run_action(self, interaction: discord.Interaction, action: str) -> None:
        if not await claim_interaction_once(interaction):
            return
        if not await safe_defer(interaction, ephemeral=True, claim=False):
            return
        vps = db_get_vps(self.vps_id)
        if not vps:
            await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "This VPS no longer exists."))
            self.stop()
            return
        if int(interaction.user.id) not in {int(self.owner_id), int(ADMIN_ID)}:
            share_level = db_share_access_level(self.vps_id, interaction.user.id)
            if share_level == "manage" and action in {"delete", "reinstall"}:
                await safe_followup(interaction, embed=make_embed("🔒 Limited Access", "This VPS was shared with **manage** access. Delete/reinstall actions require full access or ownership."))
                return
            if share_level not in {"manage", "full"}:
                await safe_followup(interaction, embed=make_embed("❌ Access Denied", "Your VPS share is no longer valid."))
                return
        try:
            if action in {"stats", "refresh"}:
                await show_dashboard(interaction, vps)
                return
            if action == "console":
                ok, message = await create_console_access(vps, interaction.user)
                latest = db_get_vps(self.vps_id) or vps
                if ok and latest["sshx_url"]:
                    # Keep console result as a single edited response; no duplicate follow-up.
                    await safe_edit_original(
                        interaction,
                        embed=make_embed("✅ Console Ready", message),
                        view=sshx_view(latest["sshx_url"]),
                    )
                else:
                    await safe_edit_original(
                        interaction,
                        embed=make_embed("✅ Console Ready" if ok else "❌ Console Failed", message),
                        view=ManageView(vps["id"], vps["user_id"]) if ok else ManageView(vps["id"], vps["user_id"]),
                    )
                return
            ok, message = await lifecycle_action(vps, action)
            if ok and action in {"start", "restart"}:
                await send_private_ipv4(interaction.user, db_get_vps(vps["id"]) or vps)
            if action == "delete" and ok:
                self.stop()
                await safe_edit_original(interaction, embed=make_embed("🗑️ VPS Removed", f"`{clean(vps['container_name'])}` and its forwarding rules were removed successfully."), view=None)
                return
            latest = await refresh_vps_record_state(db_get_vps(self.vps_id) or vps)
            if str(latest["backend"] or "docker").lower() == "docker":
                await asyncio.gather(supervise_vps_ports(latest), detect_public_network(), return_exceptions=True)
                ports = db_list_ports(latest["id"])
            else:
                ports = []
            stats, uptime, disk = await _safe_vps_live_data(latest)
            dash = dashboard_embed(latest, stats, uptime, disk, NETWORK_CACHE, ports)
            prefix = "✅" if ok else "❌"
            dash.description = f"{prefix} {message}\n\n`{clean(latest['container_name'])}`\n\n**Status:** {status_text(latest['status'], bool(latest['suspended']))}"
            await safe_edit_original(interaction, embed=dash, view=ManageView(latest["id"], latest["user_id"]))
        except Exception as exc:
            logger.error("Manage action failed for VPS #%s: %s", self.vps_id, safe_log(exc))
            await safe_edit_original(
                interaction,
                embed=make_embed("❌ Action Failed", "The action could not be completed safely."),
                view=ManageView(vps["id"], vps["user_id"]),
            )

    @discord.ui.button(label="Start", emoji="▶️", style=discord.ButtonStyle.secondary, row=0)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button): await self.run_action(interaction, "start")
    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.secondary, row=0)
    async def stop_vps(self, interaction: discord.Interaction, button: discord.ui.Button): await self.run_action(interaction, "stop")
    @discord.ui.button(label="Console", emoji="🖥️", style=discord.ButtonStyle.secondary, row=0)
    async def console(self, interaction: discord.Interaction, button: discord.ui.Button): await self.run_action(interaction, "console")
    @discord.ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=0)
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button): await self.run_action(interaction, "stats")
    @discord.ui.button(label="Restart", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button): await self.run_action(interaction, "restart")
    @discord.ui.button(label="Reinstall", emoji="♻️", style=discord.ButtonStyle.danger, row=1)
    async def reinstall(self, interaction: discord.Interaction, button: discord.ui.Button):
        vps = db_get_vps(self.vps_id)
        if not vps:
            await safe_respond(interaction, embed=make_embed("❌ VPS Not Found", "This VPS no longer exists."))
            return
        await safe_respond(
            interaction,
            embed=make_embed("♻️ Reinstall VPS", "Select the operating system for the clean reinstall, then confirm.\n\n⚠️ Existing data inside the current VPS container will be lost."),
            view=ReinstallView(self.vps_id, self.owner_id),
            ephemeral=True,
        )
    @discord.ui.button(label="Refresh", emoji="🔃", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button): await self.run_action(interaction, "stats")
    @discord.ui.button(label="Delete", emoji="🗑️", style=discord.ButtonStyle.secondary, row=1)
    async def delete_vps(self, interaction: discord.Interaction, button: discord.ui.Button): await self.run_action(interaction, "delete")


class DeployView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = int(user_id)
        self.selected_os = "ubuntu-24.04"
        self.selected_location = DEFAULT_LOCATION
        self.os_select = discord.ui.Select(placeholder="1️⃣ Select operating system", options=[discord.SelectOption(label=c["label"], value=k, emoji="🟠" if k.startswith("ubuntu") else "🔵") for k, c in OS_CONFIG.items()], row=0)
        self.location_select = discord.ui.Select(
            placeholder="2️⃣ Select location",
            options=[
                discord.SelectOption(
                    label=cfg["label"],
                    value=code,
                    emoji=("🇸🇬" if code == "SG" else "🇮🇳"),
                )
                for code, cfg in LOCATION_CONFIG.items()
            ],
            row=1,
        )
        self.deploy_button = discord.ui.Button(label="Deploy VPS", emoji="🚀", style=discord.ButtonStyle.secondary, row=2)
        self.os_select.callback = self.select_os
        self.location_select.callback = self.select_location
        self.deploy_button.callback = self.deploy
        self.add_item(self.os_select); self.add_item(self.location_select); self.add_item(self.deploy_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in {self.user_id, ADMIN_ID}:
            return True
        await safe_respond(interaction, embed=make_embed("❌ Access Denied", "This deployment menu belongs to another user."))
        return False

    async def select_os(self, interaction: discord.Interaction) -> None:
        self.selected_os = normalize_os(self.os_select.values[0]) or self.selected_os
        await safe_component_edit(
            interaction,
            embed=make_embed("🚀 Configure RGNODES™ VPS", f"OS: **{os_label(self.selected_os)}**\nLocation: **{location_label(self.selected_location)}**\n\nSelect both options, then press **Deploy VPS**."),
            view=self,
        )

    async def select_location(self, interaction: discord.Interaction) -> None:
        self.selected_location = normalize_location(self.location_select.values[0]) or DEFAULT_LOCATION
        await safe_component_edit(
            interaction,
            embed=make_embed("🚀 Configure RGNODES™ VPS", f"OS: **{os_label(self.selected_os)}**\nLocation: **{location_label(self.selected_location)}**\n\nSelect both options, then press **Deploy VPS**."),
            view=self,
        )

    async def deploy(self, interaction: discord.Interaction) -> None:
        # Claim and defer exactly once before any early response.
        if not await safe_defer(interaction, ephemeral=True):
            return
        # Re-check slots at click time so two open deploy menus cannot oversubscribe.
        is_admin = ADMIN_BYPASS_LIMITS and ADMIN_ID > 0 and interaction.user.id == ADMIN_ID
        used = db_vps_count(interaction.user.id)
        limit = db_effective_slots(interaction.user.id)
        if not is_admin and used >= limit:
            await safe_edit_original(
                interaction,
                embed=make_embed(
                    "🎟️ Slots Full",
                    f"You are using **{used}/{limit}** VPS slots.\n\n**SLOTS FULL** — additional slots will be available soon. Ask an administrator to add slots.",
                ),
                view=self,
            )
            return
        self.deploy_button.disabled = True
        try:
            await deploy_flow(interaction, user=interaction.user, os_type=self.selected_os, location=self.selected_location, ram=DEFAULT_RAM, cpu=DEFAULT_CPU, disk=DEFAULT_DISK)
        finally:
            self.stop()


async def _dashboard_live_data(vps: sqlite3.Row) -> tuple[dict[str, str], str, dict[str, str], dict[str, str], list[sqlite3.Row]]:
    """Collect dashboard data independently so one broken probe cannot blank the UI."""
    backend = str(vps["backend"] or "docker").lower()
    network = NETWORK_CACHE
    ports: list[sqlite3.Row] = []
    if backend == "docker":
        await asyncio.gather(
            supervise_vps_ports(vps),
            detect_public_network(),
            return_exceptions=True,
        )
        try:
            ports = db_list_ports(vps["id"])
        except Exception as exc:
            logger.warning("VPS #%s port listing failed: %s", vps["id"], safe_log(exc))
            ports = []

    stats, uptime, disk = await _safe_vps_live_data(vps)
    return stats, uptime, disk, network, ports


async def _safe_vps_live_data(vps: sqlite3.Row) -> tuple[dict[str, str], str, dict[str, str]]:
    """Collect live metrics independently; failed probes become N/A."""
    try:
        stats, uptime, disk = await asyncio.gather(
            backend_stats(vps), backend_uptime(vps), backend_disk(vps),
            return_exceptions=True,
        )
    except Exception as exc:
        logger.warning("VPS #%s live-data gather failed: %s", vps["id"], safe_log(exc))
        stats, uptime, disk = RuntimeError("stats unavailable"), "N/A", RuntimeError("disk unavailable")
    if isinstance(stats, BaseException) or not isinstance(stats, dict):
        stats = {"cpu": "N/A", "memory": "N/A", "network": "N/A"}
    else:
        stats = {str(k): clean(v) for k, v in stats.items()}
    stats.setdefault("cpu", "N/A")
    stats.setdefault("memory", "N/A")
    stats.setdefault("network", "N/A")
    if isinstance(uptime, BaseException) or uptime is None:
        uptime = "N/A"
    if isinstance(disk, BaseException) or not isinstance(disk, dict):
        disk = {"used": "N/A", "total": clean(vps["disk"]), "percent": "N/A"}
    else:
        disk = {str(k): clean(v) for k, v in disk.items()}
    disk.setdefault("used", "N/A")
    disk.setdefault("total", clean(vps["disk"]))
    disk.setdefault("percent", "N/A")
    return stats, str(uptime), disk


async def show_dashboard(interaction: discord.Interaction, vps: sqlite3.Row) -> None:
    """Render exactly one dashboard response for slash/component interactions."""
    vps = await refresh_vps_record_state(vps)
    stats, uptime, disk, network, ports = await _dashboard_live_data(vps)
    embed = dashboard_embed(vps, stats, uptime, disk, network, ports)
    view = ManageView(vps["id"], vps["user_id"])
    # A deferred interaction already owns its original response. Editing it is
    # the only safe path; creating a follow-up here is what previously produced
    # duplicate dashboard messages after button presses.
    if interaction.response.is_done():
        await safe_edit_original(interaction, embed=embed, view=view)
    else:
        await safe_followup(interaction, embed=embed, view=view)


def db_claim_deploy_cooldown(user_id: int) -> tuple[bool, int]:
    """Atomically reserve the deployment action for this user.

    The cooldown is a duplicate-click guard, not a payment timer. The guard is
    removed automatically when a deployment finishes or fails.
    """
    uid=int(user_id)
    if runtime_int("deploy_cooldown") <= 0:
        return True, 0
    conn=db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now_dt=datetime.now(timezone.utc)
        row=conn.execute("SELECT next_at FROM economy_cooldowns WHERE user_id=? AND action='deploy'",(uid,)).fetchone()
        if row:
            try:
                remaining=max(0,int((datetime.fromisoformat(str(row[0]))-now_dt).total_seconds()))
            except (TypeError,ValueError):
                remaining=0
            if remaining:
                conn.rollback()
                return False, remaining
        conn.execute("INSERT OR IGNORE INTO economy(user_id,wallet,bank,invites,updated_at) VALUES(?,?,?,?,?)",(uid,0,0,0,now_dt.isoformat()))
        next_at=(now_dt+timedelta(seconds=runtime_int("deploy_cooldown"))).isoformat()
        conn.execute("INSERT INTO economy_cooldowns(user_id,action,next_at) VALUES(?,?,?) ON CONFLICT(user_id,action) DO UPDATE SET next_at=excluded.next_at",(uid,'deploy',next_at))
        conn.commit()
        return True, 0
    except Exception:
        with contextlib.suppress(Exception): conn.rollback()
        raise
    finally:
        conn.close()


def db_clear_deploy_cooldown(user_id: int) -> None:
    conn=db_connect()
    try:
        conn.execute("DELETE FROM economy_cooldowns WHERE user_id=? AND action='deploy'",(int(user_id),))
    finally:
        conn.close()


async def deploy_flow(interaction: discord.Interaction, *, user: discord.User | discord.Member, os_type: str, location: str, ram: str, cpu: str, disk: str, backend_override: str | None = None, charge_cost: bool = True) -> None:
    async def progress(embed: discord.Embed) -> None:
        await safe_edit_original(interaction, embed=embed)

    cooldown_claimed = False
    if charge_cost and not (ADMIN_ID > 0 and int(interaction.user.id) == int(ADMIN_ID)):
        cooldown_claimed, remaining = db_claim_deploy_cooldown(user.id)
        if not cooldown_claimed:
            await safe_edit_original(interaction, embed=make_embed("⏳ Deployment Cooldown", f"Please wait `{_cooldown_text(remaining)}` before deploying again."))
            return
    charged = False
    if charge_cost and not (ADMIN_ID > 0 and int(interaction.user.id) == int(ADMIN_ID)):
        wallet, _bank = db_balance(user.id)
        if wallet < runtime_int('deploy_cost'):
            await safe_edit_original(interaction, embed=make_embed("💰 Insufficient Coins", f"Deploying a VPS costs **{runtime_int('deploy_cost'):,} coins**.\nYour wallet: `{wallet:,}` coins."))
            if cooldown_claimed: db_clear_deploy_cooldown(user.id)
            return
        charged = db_take_coins(user.id, runtime_int('deploy_cost'))
        if not charged:
            await safe_edit_original(interaction, embed=make_embed("💰 Payment Failed", "Your coin balance changed before deployment. Please retry."))
            if cooldown_claimed: db_clear_deploy_cooldown(user.id)
            return
    try:
        ok, message, vps = await asyncio.wait_for(
            create_vps(
                user,
                os_type=os_type,
                location=location,
                ram=ram,
                cpu=cpu,
                disk=disk,
                progress=progress,
                backend_override=backend_override,
            ),
            timeout=DEPLOY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        if charged: db_add_coins(user.id, runtime_int('deploy_cost'))
        logger.error("Deployment timed out for user %s after %ss", user.id, DEPLOY_TIMEOUT)
        await safe_edit_original(
            interaction,
            embed=make_embed(
                "❌ VPS Creation Timed Out",
                "Docker took too long to complete the deployment. Any partially created container was cleaned up when possible. Please retry.",
            ),
        )
        if cooldown_claimed: db_clear_deploy_cooldown(user.id)
        return
    except Exception:
        if charged: db_add_coins(user.id, runtime_int('deploy_cost'))
        logger.exception("Unhandled deployment exception for user %s", user.id)
        await safe_edit_original(
            interaction,
            embed=make_embed("❌ VPS Creation Failed", "Deployment failed safely due to an unexpected backend error. Check the bot log for details."),
        )
        if cooldown_claimed: db_clear_deploy_cooldown(user.id)
        return
    if not ok or not vps:
        if charged: db_add_coins(user.id, runtime_int('deploy_cost'))
        await safe_edit_original(interaction, embed=make_embed("❌ VPS Creation Failed", message))
        if cooldown_claimed: db_clear_deploy_cooldown(user.id)
        return
    network = await detect_public_network(force=True)
    ip_ok = valid_public_ipv4(network.get("ip"))
    if ip_ok:
        with contextlib.suppress(Exception):
            db_set_vps_ipv4(vps["container_id"], network["ip"])

    console_url = normalize_sshx_url(vps["sshx_url"]) if vps["sshx_url"] else None
    dm_sent = False
    ssh_port = next((int(p["host_port"]) for p in db_list_ports(vps["id"]) if int(p["container_port"]) == 22 and str(p["protocol"]).lower() == "tcp"), None)
    await safe_dm(user, ssh_access_embed(vps, network.get("ip") if ip_ok else None, ssh_port))
    if console_url:
        dm_sent = await safe_dm(
            user,
            console_embed(vps["container_name"], console_url, network.get("ip") if ip_ok else None, actual_location_label(network)),
            sshx_view(console_url),
        )
        if ip_ok:
            await safe_dm(user, ipv4_dm_embed(vps, network))
    elif ip_ok:
        await safe_dm(user, ipv4_dm_embed(vps, network))

    final = make_embed("✅ VPS Ready", f"Your **{os_label(vps['os_type'])}** VPS is online.")
    final.add_field(name="🖥️ VPS", value=f"`{clean(vps['container_name'])}` • ID `{vps['id']}`", inline=False)
    final.add_field(name="🌍 Location", value=location_label(vps["location"]), inline=True)
    if console_url:
        console_state = "✅ SSHx link sent by DM" if dm_sent else "⚠️ SSHx ready, but DM is closed"
    else:
        console_state = "⚠️ SSHx temporarily unavailable — use Console/sshx to retry"
    final.add_field(name="🌐 Console", value=console_state, inline=True)
    await safe_edit_original(interaction, embed=final, view=ManageView(vps["id"], vps["user_id"]))
    if cooldown_claimed: db_clear_deploy_cooldown(user.id)


def actor_vps(interaction: discord.Interaction, identifier: str | None) -> sqlite3.Row | None:
    if interaction.user.id == ADMIN_ID:
        return db_find_vps(interaction.user.id, identifier, admin=True)
    return db_find_accessible_vps(interaction.user.id, identifier)


# ================================================================
# Slash commands (same public UI)
# ================================================================


def os_choices():
    return [app_commands.Choice(name=c["label"], value=k) for k, c in OS_CONFIG.items()]


def location_choices():
    return [app_commands.Choice(name=c["label"], value=k) for k, c in LOCATION_CONFIG.items()]


@bot.tree.command(name="deploy", description="Deploy a new RGNODES VPS.")
@app_commands.describe(os_type="Operating system", location="VPS location", ram="RAM, e.g. 2g", cpu="CPU cores, e.g. 1", disk="Disk allocation, e.g. 10g")
@app_commands.choices(os_type=os_choices(), location=location_choices())
async def deploy_slash(interaction: discord.Interaction, os_type: str, location: str = DEFAULT_LOCATION, ram: str = DEFAULT_RAM, cpu: str = DEFAULT_CPU, disk: str = DEFAULT_DISK) -> None:
    if await safe_defer(interaction, ephemeral=True):
        await deploy_flow(interaction, user=interaction.user, os_type=os_type, location=location, ram=ram, cpu=cpu, disk=disk)


@bot.tree.command(name="myvm", description="Open your newest RGNODES™ VPS dashboard.")
async def myvm_slash(interaction: discord.Interaction) -> None:
    """Open the caller's newest VPS, matching /manage with no identifier."""
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = db_find_vps(interaction.user.id, None, admin=True) if interaction.user.id == ADMIN_ID and ADMIN_ID > 0 else db_find_accessible_vps(interaction.user.id, None)
    if not vps:
        await safe_followup(
            interaction,
            embed=make_embed("❌ No VPS Found", f"You do not have any VPS instances yet. Use `{PREFIX}deploy` first."),
        )
        return
    await show_dashboard(interaction, vps)


@bot.tree.command(name="manage", description="Open your VPS management dashboard.")
@app_commands.describe(vps_identifier="VPS ID/name; blank uses your newest VPS")
async def manage_slash(interaction: discord.Interaction, vps_identifier: str | None = None):
    if not await safe_defer(interaction, ephemeral=True): return
    vps = actor_vps(interaction, vps_identifier)
    if not vps: await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found.")); return
    await show_dashboard(interaction, vps)


async def slash_lifecycle(interaction: discord.Interaction, identifier: str, action: str):
    if not await safe_defer(interaction, ephemeral=True): return
    vps = actor_vps(interaction, identifier)
    if not vps: await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found.")); return
    if action in {"delete", "reinstall"} and int(interaction.user.id) not in {int(vps["user_id"]), int(ADMIN_ID)}:
        if db_share_access_level(vps["id"], interaction.user.id) != "full":
            await safe_followup(interaction, embed=make_embed("🔒 Limited Access", "Delete/reinstall requires VPS ownership, admin access, or a full-access share.")); return
    ok, message = await lifecycle_action(vps, action)
    if ok and action in {"start", "restart"}:
        await send_private_ipv4(interaction.user, db_get_vps(vps["id"]) or vps)
    await safe_followup(interaction, embed=make_embed("✅ Action Complete" if ok else "❌ Action Failed", message))


@bot.tree.command(name="start", description="Start a VPS.")
async def start_slash(interaction: discord.Interaction, vps_identifier: str): await slash_lifecycle(interaction, vps_identifier, "start")
@bot.tree.command(name="stop", description="Stop a VPS.")
async def stop_slash(interaction: discord.Interaction, vps_identifier: str): await slash_lifecycle(interaction, vps_identifier, "stop")
@bot.tree.command(name="restart", description="Restart a VPS.")
async def restart_slash(interaction: discord.Interaction, vps_identifier: str): await slash_lifecycle(interaction, vps_identifier, "restart")


@bot.tree.command(name="console", description="Generate a private SSHx console link and send it by DM.")
async def console_slash(interaction: discord.Interaction, vps_identifier: str):
    if not await safe_defer(interaction, ephemeral=True): return
    vps = actor_vps(interaction, vps_identifier)
    if not vps: await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found.")); return
    ok, message = await create_console_access(vps, interaction.user)
    await safe_followup(interaction, embed=make_embed("✅ Console Ready" if ok else "❌ Console Failed", message))


@bot.tree.command(name="vps-info", description="Show full live VPS information.")
async def vps_info_slash(interaction: discord.Interaction, vps_identifier: str | None = None):
    if not await safe_defer(interaction, ephemeral=True): return
    vps = actor_vps(interaction, vps_identifier)
    if not vps: await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found.")); return
    await show_dashboard(interaction, vps)


@bot.tree.command(name="remove", description="Delete a VPS and its Docker container.")
async def remove_slash(interaction: discord.Interaction, vps_identifier: str): await slash_lifecycle(interaction, vps_identifier, "delete")


@bot.tree.command(name="list", description="List your VPS instances.")
async def list_slash(interaction: discord.Interaction):
    if not await safe_defer(interaction, ephemeral=True): return
    rows = db_get_all_vps() if interaction.user.id == ADMIN_ID and ADMIN_ID > 0 else db_get_user_vps(interaction.user.id)
    embed = make_embed("📋 Your RGNODES™ VPS")
    if not rows: embed.description = "You do not have any VPS instances."
    for row in rows[:25]:
        embed.add_field(name=f"{status_text(row['status'], bool(row['suspended']))} {clean(row['container_name'])}", value=f"ID: `{row['id']}` • {os_label(row['os_type'])}\n{clean(row['ram'])} RAM • {clean(row['cpu'])} CPU • {clean(row['disk'])} Disk • {location_label(row['location'])}", inline=False)
    await safe_followup(interaction, embed=embed)


@bot.tree.command(name="ping", description="Check RGNODES™ bot latency.")
async def ping_slash(interaction: discord.Interaction):
    await safe_respond(interaction, embed=make_embed("🏓 Pong!", f"Discord latency: `{round(bot.latency * 1000)}ms`"))



@bot.tree.command(name="myvps", description="Open your newest RGNODES™ VPS dashboard.")
async def myvps_slash_alias(interaction: discord.Interaction):
    await myvm_slash(interaction)


@bot.tree.command(name="vps-stats", description="Show live VPS statistics.")
async def vps_stats_slash(interaction: discord.Interaction, vps_identifier: str):
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = actor_vps(interaction, vps_identifier)
    if not vps:
        await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found."))
        return
    vps = await refresh_vps_record_state(vps)
    stats, uptime, disk = await _safe_vps_live_data(vps)
    await safe_followup(interaction, embed=make_embed(
        f"📈 VPS Stats • {clean(vps['container_name'])}",
        f"CPU: `{clean(stats.get('cpu'))}`\nMemory: `{clean(stats.get('memory'))}`\n"
        f"Disk: `{clean(disk.get('used'))} / {clean(vps['disk'])}`\n"
        f"Network: `{clean(stats.get('network'))}`\nUptime: `{clean(uptime)}`"
    ))


@bot.tree.command(name="vps-uptime", description="Show VPS uptime.")
async def vps_uptime_slash(interaction: discord.Interaction, vps_identifier: str):
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = actor_vps(interaction, vps_identifier)
    if not vps:
        await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found."))
        return
    await safe_followup(interaction, embed=make_embed("⏱️ VPS Uptime", f"`{clean(await backend_uptime(vps))}`"))


@bot.tree.command(name="restart-vps", description="Restart a VPS safely.")
async def restart_vps_slash(interaction: discord.Interaction, vps_identifier: str):
    await slash_lifecycle(interaction, vps_identifier, "restart")


@bot.tree.command(name="snapshot", description="Create a Docker VPS snapshot.")
async def snapshot_slash(interaction: discord.Interaction, vps_identifier: str, name: str | None = None):
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = actor_vps(interaction, vps_identifier)
    if not vps:
        await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found."))
        return
    snap_name = (name or "").strip() or f"snapshot-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    ok, message = await docker_snapshot_create(vps, snap_name)
    await safe_followup(interaction, embed=make_embed("📸 Snapshot Created" if ok else "❌ Snapshot Failed", message))


@bot.tree.command(name="list-snapshots", description="List snapshots for a VPS.")
async def list_snapshots_slash(interaction: discord.Interaction, vps_identifier: str):
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = actor_vps(interaction, vps_identifier)
    if not vps:
        await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found."))
        return
    if str(vps["backend"] or "docker").lower() != "docker":
        await safe_followup(interaction, embed=make_embed("🦖 Pterodactyl Backups", "This VPS uses Pterodactyl. Use the Panel's backup system instead of local Docker snapshots."))
        return
    rows = db_list_snapshots(vps["id"])
    body = "\n".join(f"`{clean(r['name'])}` • {str(r['created_at'])[:19]} UTC" for r in rows[:20]) or "No snapshots."
    await safe_followup(interaction, embed=make_embed(f"📋 Snapshots • {clean(vps['container_name'])}", body))


@bot.tree.command(name="restore-snapshot", description="Restore a Docker VPS snapshot.")
async def restore_snapshot_slash(interaction: discord.Interaction, vps_identifier: str, name: str):
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = actor_vps(interaction, vps_identifier)
    if not vps:
        await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found."))
        return
    if not db_is_owner_or_admin(interaction.user.id, vps):
        await safe_followup(interaction, embed=make_embed("❌ Permission Denied", "Only the VPS owner or administrator can restore snapshots."))
        return
    ok, message = await docker_snapshot_restore(vps, name)
    await safe_followup(interaction, embed=make_embed("✅ Snapshot Restored" if ok else "❌ Restore Failed", message))


@bot.tree.command(name="manage-shared", description="Manage a user's shared VPS access.")
async def manage_shared_slash(interaction: discord.Interaction, owner_user: discord.User, vps_identifier: str):
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = db_find_vps(owner_user.id, vps_identifier)
    if not vps or not db_is_owner_or_admin(interaction.user.id, vps):
        await safe_followup(interaction, embed=make_embed("❌ Permission Denied", "Only the VPS owner or administrator can manage shared access."))
        return
    shared = db_list_shared(vps["id"])
    body = "\n".join(f"<@{r['user_id']}> • granted by <@{r['shared_by']}>" for r in shared) or "No users currently have shared access."
    await safe_followup(interaction, embed=make_embed(f"👥 Shared Access • {clean(vps['container_name'])}", body), view=ManageView(vps["id"], vps["user_id"]))


@bot.tree.command(name="share-ruser", description="Revoke a user's shared VPS access.")
async def share_ruser_slash(interaction: discord.Interaction, vps_identifier: str, target_user: discord.User):
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = actor_vps(interaction, vps_identifier)
    if not vps or not db_is_owner_or_admin(interaction.user.id, vps):
        await safe_followup(interaction, embed=make_embed("❌ Permission Denied", "Only the VPS owner or administrator can revoke shared access."))
        return
    ok, message = db_unshare_vps(vps["id"], target_user.id)
    await safe_followup(interaction, embed=make_embed("✅ Access Removed" if ok else "⚠️ Nothing Changed", f"{message}\nVPS: `{clean(vps['container_name'])}` • User: <@{target_user.id}>"))


@bot.tree.command(name="serverstats", description="Show host and VPS server statistics.")
async def serverstats_slash(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)
    info = host_os_info()
    try:
        load = os.getloadavg()[0]
        load_text = f"{load:.2f}"
    except (AttributeError, OSError):
        load_text = "N/A"
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
        total_m = re.search(r"^MemTotal:\s+(\d+)", meminfo, re.M)
        avail_m = re.search(r"^MemAvailable:\s+(\d+)", meminfo, re.M)
        ram_text = f"{format_bytes((int(total_m.group(1))-int(avail_m.group(1)))*1024)} / {format_bytes(int(total_m.group(1))*1024)}" if total_m and avail_m else "N/A"
    except Exception:
        ram_text = "N/A"
    du = shutil.disk_usage("/")
    ok, active = await docker_running_count()
    if not ok:
        active = db_running_count()
    await safe_followup(interaction, embed=make_embed("📊 RGNODES™ • Server Statistics",
        f"OS: `{clean(info['name'], 100)}`\nPID 1: `{clean(info['pid1'])}`\n"
        f"Host uptime: `{await host_uptime()}`\nRAM: `{ram_text}`\n"
        f"Disk: `{format_bytes(du.used)} / {format_bytes(du.total)}`\n"
        f"CPU cores: `{os.cpu_count() or 1}` • Load: `{load_text}`\nActive VPS: `{active}`"))


@bot.tree.command(name="thresholds", description="Show RGNODES™ resource thresholds.")
async def thresholds_slash(interaction: discord.Interaction):
    await safe_respond(interaction, embed=make_embed("📋 RGNODES™ • Thresholds",
        f"Per-user slots: `{db_effective_slots(interaction.user.id)}`\nGlobal running VPS: `{runtime_int('total_running_limit')}`\n"
        f"Max ports/VPS: `{runtime_int('max_ports_per_vps')}`\nPort range: `{runtime_int('port_range_start')}-{runtime_int('port_range_end')}`\n"
        "RAM per VPS: `256MB-256GB`\nDisk per VPS: `1GB-10TB`\nCPU per VPS: `>0-64 cores`"))


@bot.tree.command(name="set-status", description="Admin: set the bot presence.")
@app_commands.choices(status_type=[
    app_commands.Choice(name="Playing", value="playing"),
    app_commands.Choice(name="Watching", value="watching"),
    app_commands.Choice(name="Listening", value="listening"),
    app_commands.Choice(name="Competing", value="competing"),
])
async def set_status_slash(interaction: discord.Interaction, status_type: str, name: str):
    if not admin_ok(interaction):
        await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required."))
        return
    if status_type == "watching":
        activity = discord.Activity(type=discord.ActivityType.watching, name=name)
    elif status_type == "listening":
        activity = discord.Activity(type=discord.ActivityType.listening, name=name)
    elif status_type == "competing":
        activity = discord.Activity(type=discord.ActivityType.competing, name=name)
    else:
        activity = discord.Game(name=name)
    await bot.change_presence(activity=activity)
    await safe_respond(interaction, embed=make_embed("✅ Status Updated", f"Type: `{status_type}`\nName: `{clean(name, 200)}`"))


@bot.tree.command(name="about", description="Show RGNODES™ information.")
async def about_slash(interaction: discord.Interaction):
    embed = make_embed("☁️ RGNODES™ VPS Management", "Professional Docker + systemd VPS hosting with economy, nodes, SSH and custom RGNODES SSHx.")
    embed.add_field(name="🛠️ Stack", value="Python 3 • discord.py • Docker • SQLite WAL", inline=False)
    embed.add_field(name="🔐 Security", value="Console links are generated on demand and sent by DM only.", inline=False)
    await safe_respond(interaction, embed=embed)


@bot.tree.command(name="logs", description="View recent logs for your VPS.")
async def logs_slash(interaction: discord.Interaction, vps_identifier: str, lines: int = 50):
    if not await safe_defer(interaction, ephemeral=True): return
    vps = actor_vps(interaction, vps_identifier)
    if not vps: await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found.")); return
    logs = await docker_logs(vps["container_id"], lines)
    embed = make_embed(f"📜 Logs • {clean(vps['container_name'])}")
    # Prevent user/container output from closing the Discord code block.
    logs = str(logs).replace("```", "'''")
    embed.add_field(name="Recent output", value=f"```text\n{logs[:3900]}\n```", inline=False)
    await safe_followup(interaction, embed=embed)


@bot.tree.command(name="ports", description="Show your VPS port forwarding rules.")
async def ports_slash(interaction: discord.Interaction, vps_identifier: str):
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = actor_vps(interaction, vps_identifier)
    if not vps:
        await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found."))
        return
    await supervise_vps_ports(vps)
    ports = db_list_ports(vps["id"])
    network = await detect_public_network(force=True)
    if valid_public_ipv4(network.get("ip")):
        db_set_vps_ipv4(vps["container_id"], network["ip"])
        await safe_dm(interaction.user, ipv4_dm_embed(vps, network))
    embed = make_embed(f"🌐 Ports • {clean(vps['container_name'])}", f"IPv4: 🔒 Sent by DM\nUsed: `{len(ports)}/{runtime_int('max_ports_per_vps')}`")
    if not ports:
        embed.description += "\n\nNo forwarding rules configured. Use `/port-add`."
    for p_row in ports:
        embed.add_field(name=f"#{p_row['id']} • {str(p_row['protocol']).upper()}", value=f"Public port `{p_row['host_port']}` → VPS port `:{p_row['container_port']}` • **{clean(p_row['status']).upper()}**", inline=False)
    await safe_followup(interaction, embed=embed)


@bot.tree.command(name="port-add", description="Add a TCP port forward (maximum 10 per VPS).")
@app_commands.describe(vps_identifier="VPS ID/name", container_port="Port inside the VPS", host_port="Public host port; leave 0 for automatic allocation")
async def port_add_slash(interaction: discord.Interaction, vps_identifier: str, container_port: int, host_port: int = 0):
    if not await safe_defer(interaction, ephemeral=True):
        return
    if not 1 <= container_port <= 65535:
        await safe_followup(interaction, embed=make_embed("❌ Invalid Port", "Container port must be between 1 and 65535."))
        return
    vps = actor_vps(interaction, vps_identifier)
    if not vps:
        await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found."))
        return
    async with PORT_LOCK:
        ports = db_list_ports(vps["id"])
        if len(ports) >= runtime_int('max_ports_per_vps'):
            await safe_followup(interaction, embed=make_embed("⚠️ Port Limit Reached", f"A VPS can use at most `{runtime_int('max_ports_per_vps')}` forwarding rules."))
            return
        if db_find_port(vps["id"], container_port, "tcp"):
            await safe_followup(interaction, embed=make_embed("⚠️ Already Exists", "That container port is already forwarded."))
            return
        if host_port == 0:
            host_port = await allocate_host_port() or 0
        if not 1024 <= host_port <= 65535 or not port_bindable(host_port):
            await safe_followup(interaction, embed=make_embed("❌ Host Port Unavailable", "Choose a free host port from 1024–65535, or use `0` for automatic allocation."))
            return
        try:
            port_id = db_insert_port(vps["id"], container_port, host_port, "tcp")
        except sqlite3.IntegrityError:
            await safe_followup(interaction, embed=make_embed("❌ Port Conflict", "That public port is already reserved by another VPS."))
            return
    row = db_get_port(port_id)
    ok, message = await start_port_forward(row, vps) if row else (False, "Forwarding record disappeared unexpectedly.")
    if not ok:
        if row:
            await stop_port_forward(row)
        db_delete_port(port_id)
    else:
        await send_private_ipv4(interaction.user, db_get_vps(vps["id"]) or vps)
    await safe_followup(interaction, embed=make_embed("✅ Port Forward Added" if ok else "❌ Port Forward Failed", message))


@bot.tree.command(name="port-remove", description="Remove a TCP port forward.")
async def port_remove_slash(interaction: discord.Interaction, vps_identifier: str, port_id: int):
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = actor_vps(interaction, vps_identifier)
    row = db_get_port(port_id)
    if not vps or not row or int(row["vps_id"]) != int(vps["id"]):
        await safe_followup(interaction, embed=make_embed("❌ Port Not Found", "That forwarding rule does not belong to the selected VPS."))
        return
    await stop_port_forward(row)
    db_delete_port(port_id)
    await safe_followup(interaction, embed=make_embed("🗑️ Port Forward Removed", f"Forwarding rule `#{port_id}` has been removed."))


@bot.tree.command(name="help", description="Open the RGNODES command navigator.")
async def help_slash(interaction: discord.Interaction):
    admin = interaction.user.id == ADMIN_ID
    await safe_respond(interaction, embed=build_help_embed(admin, "home"), ephemeral=True, view=HelpView(interaction.user.id, admin))


# ================================================================
# Admin commands — compatibility surface kept
# ================================================================


def admin_ok(source: discord.Interaction | commands.Context) -> bool:
    """Check the configured admin for slash interactions and prefix contexts."""
    user = getattr(source, "user", None)
    if user is None:
        user = getattr(source, "author", None)
    return bool(user and ADMIN_ID > 0 and int(user.id) == int(ADMIN_ID))


@bot.tree.command(name="admin-create", description="Admin: create a VPS for another user.")
@app_commands.choices(os_type=os_choices(), location=location_choices())
async def admin_create(interaction: discord.Interaction, target_user: discord.User, os_type: str, location: str = DEFAULT_LOCATION, ram: str = DEFAULT_RAM, cpu: str = DEFAULT_CPU, disk: str = DEFAULT_DISK):
    if not admin_ok(interaction): await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    if await safe_defer(interaction, ephemeral=True): await deploy_flow(interaction, user=target_user, os_type=os_type, location=location, ram=ram, cpu=cpu, disk=disk, charge_cost=False)


@bot.tree.command(name="add-slots", description="Admin: add VPS slots to a user.")
@app_commands.describe(target_user="Discord user", slots="How many additional VPS slots to add")
async def add_slots_slash(interaction: discord.Interaction, target_user: discord.User, slots: int):
    if not admin_ok(interaction):
        await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required."))
        return
    if slots <= 0 or slots > 1000:
        await safe_respond(interaction, embed=make_embed("❌ Invalid Slot Amount", "Choose a positive slot amount up to 1000."))
        return
    db_upsert_user(target_user.id, str(target_user))
    total = db_add_slots(target_user.id, slots)
    await safe_respond(interaction, embed=make_embed("🎟️ Slots Added", f"<@{target_user.id}> now has **{total} VPS slots**.\n\nAdditional slots added: `{slots}`"))


@bot.tree.command(name="remove-all", description="Admin: delete every RGNODES VPS and reset VPS IDs.")
@app_commands.describe(confirm="Must be true to perform this destructive action")
async def remove_all_slash(interaction: discord.Interaction, confirm: bool = False):
    if not admin_ok(interaction):
        await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required."))
        return
    if not confirm:
        await safe_respond(interaction, embed=make_embed("⚠️ Confirm Remove All", "This permanently removes all managed VPS containers, forwarding rules and VPS records. It also resets the VPS ID sequence. Run `/remove-all confirm:true` to continue."))
        return
    if not await safe_defer(interaction, ephemeral=True):
        return
    rows = db_get_all_vps()
    removed = 0
    failed = 0
    for vps in rows:
        try:
            ok, _ = await lifecycle_action(vps, "delete")
            removed += 1 if ok else 0
            failed += 0 if ok else 1
        except Exception as exc:
            failed += 1
            logger.exception("remove-all failed for VPS #%s: %s", vps["id"], exc)
    # Clean orphaned managed Docker containers and reset relational records/sequence.
    rc, out, _ = await docker_cli("ps", "-aq", "--format", "{{.ID}}\t{{.Names}}", timeout=60, retries=1)
    orphans = []
    if rc == 0:
        for line in out.decode("utf-8", "replace").splitlines():
            parts = line.strip().split("\t", 1)
            if len(parts) == 2 and re.fullmatch(r"rgnodes-\d+", parts[1].strip(), flags=re.I):
                orphans.append(parts[0])
    for cid in orphans:
        with contextlib.suppress(Exception):
            await docker_remove(cid)
    db_delete_all_vps()
    await safe_followup(interaction, embed=make_embed("✅ Remove All Complete", f"Managed VPS removed: `{removed}`\nFailures: `{failed}`\nVPS ID sequence reset to `1`.\nAll forwarding records were cleared."))


@bot.tree.command(name="admin-list", description="Admin: list all VPS instances.")
async def admin_list(interaction: discord.Interaction):
    if not admin_ok(interaction): await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    if not await safe_defer(interaction, ephemeral=True): return
    rows = db_get_all_vps(); embed = make_embed("🗂️ Admin • All VPS")
    if not rows: embed.description = "No VPS instances found."
    for row in rows[:25]: embed.add_field(name=f"#{row['id']} • {clean(row['container_name'])}", value=f"Owner: <@{row['user_id']}>\n{status_text(row['status'], bool(row['suspended']))} • {location_label(row['location'])}", inline=False)
    await safe_followup(interaction, embed=embed)



@bot.tree.command(name="admin-remove", description="Admin: remove all VPS instances belonging to a user.")
async def admin_remove_slash(interaction: discord.Interaction, target_user: discord.User):
    if not admin_ok(interaction):
        await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required."))
        return
    if not await safe_defer(interaction, ephemeral=True):
        return
    rows = db_get_user_vps(target_user.id)
    removed = 0
    failed = 0
    for vps in rows:
        try:
            ok, _ = await lifecycle_action(vps, "delete")
            removed += int(ok)
            failed += int(not ok)
        except Exception:
            failed += 1
            logger.exception("admin-remove failed for user %s VPS #%s", target_user.id, vps["id"])
    await safe_followup(interaction, embed=make_embed("🗑️ Admin Remove Complete", f"User: <@{target_user.id}>\nRemoved: `{removed}`\nFailed: `{failed}`"))

@bot.tree.command(name="admin-delete-user", description="Admin: delete one VPS belonging to a user.")
async def admin_delete_user(interaction: discord.Interaction, target_user: discord.User, vps_identifier: str):
    if not admin_ok(interaction): await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    if not await safe_defer(interaction, ephemeral=True): return
    vps = db_find_vps(target_user.id, vps_identifier)
    if not vps: await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found.")); return
    ok, message = await lifecycle_action(vps, "delete")
    await safe_followup(interaction, embed=make_embed("✅ VPS Deleted" if ok else "❌ Delete Failed", message))


@bot.tree.command(name="admin-ban", description="Admin: block a user from creating VPS instances.")
async def admin_ban(interaction: discord.Interaction, target_user: discord.User):
    if not admin_ok(interaction): await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    db_set_ban(target_user.id, True); await safe_respond(interaction, embed=make_embed("✅ User Restricted", f"<@{target_user.id}> can no longer create VPS instances."))


@bot.tree.command(name="admin-unban", description="Admin: allow a user to create VPS instances again.")
async def admin_unban(interaction: discord.Interaction, target_user: discord.User):
    if not admin_ok(interaction): await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    db_set_ban(target_user.id, False); await safe_respond(interaction, embed=make_embed("✅ User Restored", f"<@{target_user.id}> may create VPS instances again."))


@bot.tree.command(name="sshx", description="Generate a fresh private SSHx browser console link.")
async def sshx_slash(interaction: discord.Interaction, vps_identifier: str): await console_slash(interaction, vps_identifier)


@bot.tree.command(name="share-user", description="Share your VPS with another Discord user.")
async def share_user_slash(interaction: discord.Interaction, vps_identifier: str, target_user: discord.User):
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = actor_vps(interaction, vps_identifier)
    if not vps or not db_is_owner_or_admin(interaction.user.id, vps):
        await safe_followup(interaction, embed=make_embed("❌ Permission Denied", "Only the VPS owner or administrator can share this VPS."))
        return
    ok, message = db_share_vps(vps["id"], target_user.id, interaction.user.id)
    await safe_followup(interaction, embed=make_embed("✅ VPS Shared" if ok else "⚠️ Share Failed", f"{message}\nVPS: `{vps['container_name']}` • User: <@{target_user.id}>"))


@bot.tree.command(name="unshare-user", description="Remove a user's access to your VPS.")
async def unshare_user_slash(interaction: discord.Interaction, vps_identifier: str, target_user: discord.User):
    if not await safe_defer(interaction, ephemeral=True):
        return
    vps = actor_vps(interaction, vps_identifier)
    if not vps or not db_is_owner_or_admin(interaction.user.id, vps):
        await safe_followup(interaction, embed=make_embed("❌ Permission Denied", "Only the VPS owner or administrator can remove shared access."))
        return
    ok, message = db_unshare_vps(vps["id"], target_user.id)
    await safe_followup(interaction, embed=make_embed("✅ Access Removed" if ok else "⚠️ Nothing Changed", f"{message}\nVPS: `{vps['container_name']}` • User: <@{target_user.id}>"))




@bot.tree.command(name="admin-manage", description="Admin: control a user's VPS.")
@app_commands.choices(action=[app_commands.Choice(name=x.title(), value=x) for x in ("start", "stop", "restart", "delete", "suspend", "unsuspend")])
async def admin_manage(interaction: discord.Interaction, target_user: discord.User, vps_identifier: str, action: str):
    if not admin_ok(interaction): await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    if not await safe_defer(interaction, ephemeral=True): return
    vps = db_find_vps(target_user.id, vps_identifier)
    if not vps: await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found.")); return
    if action in {"start", "stop", "restart", "delete"}:
        ok, message = await lifecycle_action(vps, action)
    elif action == "suspend":
        ok, message = await lifecycle_action(vps, "stop")
        if ok: db_update_vps(vps["container_id"], suspended=1); message = "VPS stopped and suspended."
    else:
        db_update_vps(vps["container_id"], suspended=0); ok, message = True, "VPS unsuspended."
    await safe_followup(interaction, embed=make_embed("✅ Admin Action Complete" if ok else "❌ Admin Action Failed", message))


@bot.tree.command(name="admin-list-users", description="Admin: list users and VPS counts.")
async def admin_list_users(interaction: discord.Interaction):
    if not admin_ok(interaction): await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    if not await safe_defer(interaction, ephemeral=True): return
    conn = db_connect()
    try:
        rows = conn.execute("SELECT u.user_id,u.username,COUNT(v.id) AS total_vps,SUM(CASE WHEN v.status='running' AND v.suspended=0 THEN 1 ELSE 0 END) AS running_vps FROM users u LEFT JOIN vps v ON u.user_id=v.user_id GROUP BY u.user_id,u.username ORDER BY total_vps DESC").fetchall()
    finally: conn.close()
    embed = make_embed("👥 Admin • Users")
    if not rows: embed.description = "No users have been recorded yet."
    for row in rows[:25]: embed.add_field(name=clean(row["username"]), value=f"Total: `{row['total_vps']}` • Running: `{row['running_vps'] or 0}`", inline=False)
    await safe_followup(interaction, embed=embed)


@bot.tree.command(name="admin-stats", description="Admin: show RGNODES statistics.")
async def admin_stats(interaction: discord.Interaction):
    if not admin_ok(interaction): await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    if not await safe_defer(interaction, ephemeral=True): return
    conn = db_connect()
    try:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]; total = conn.execute("SELECT COUNT(*) FROM vps").fetchone()[0]; running = conn.execute("SELECT COUNT(*) FROM vps WHERE status='running' AND suspended=0").fetchone()[0]; banned = conn.execute("SELECT COUNT(*) FROM bans").fetchone()[0]
    finally: conn.close()
    embed = make_embed("📊 Admin • Statistics")
    docker_ok, docker_running = await docker_running_count()
    if not docker_ok:
        docker_running = db_running_count()
    ptero_running = sum(1 for row in db_get_all_vps() if str(row["backend"] or "docker").lower() == "pterodactyl" and str(row["status"]).lower() in {"running", "starting", "restarting"} and not row["suspended"])
    live_running = docker_running + ptero_running
    for n, v in (("Users", users), ("Banned", banned), ("Total VPS", total), ("DB Running", running), ("Live Active", live_running), ("Running limit", runtime_int('total_running_limit'))): embed.add_field(name=n, value=str(v), inline=True)
    await safe_followup(interaction, embed=embed)


@bot.tree.command(name="admin-vps-info", description="Admin: view a user's VPS dashboard.")
async def admin_vps_info(interaction: discord.Interaction, target_user: discord.User, vps_identifier: str):
    if not admin_ok(interaction): await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    if not await safe_defer(interaction, ephemeral=True): return
    vps = db_find_vps(target_user.id, vps_identifier)
    if not vps: await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found.")); return
    vps = await refresh_vps_record_state(vps)
    stats, uptime, disk = await _safe_vps_live_data(vps)
    ports = db_list_ports(vps["id"]) if str(vps["backend"] or "docker").lower() == "docker" else []
    await safe_followup(interaction, embed=dashboard_embed(vps, stats, uptime, disk, NETWORK_CACHE, ports))


@bot.tree.command(name="admin-logs", description="Admin: view a user's VPS logs.")
async def admin_logs(interaction: discord.Interaction, target_user: discord.User, vps_identifier: str, lines: int = 50):
    if not admin_ok(interaction): await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    if not await safe_defer(interaction, ephemeral=True): return
    vps = db_find_vps(target_user.id, vps_identifier)
    if not vps: await safe_followup(interaction, embed=make_embed("❌ VPS Not Found", "No matching VPS was found.")); return
    logs = (await docker_logs(vps["container_id"], lines)).replace("```", "'''")
    embed = make_embed(f"📜 Admin Logs • {clean(vps['container_name'])}"); embed.add_field(name="Recent output", value=f"```text\n{logs[:3900]}\n```", inline=False)
    await safe_followup(interaction, embed=embed)


@bot.tree.command(name="admin-kill-all", description="Admin: stop all running VPS instances.")
async def admin_kill_all(interaction: discord.Interaction):
    if not admin_ok(interaction): await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    if not await safe_defer(interaction, ephemeral=True): return
    results = await asyncio.gather(*(lifecycle_action(vps, "stop") for vps in db_get_all_vps() if vps["status"] == "running"), return_exceptions=True)
    stopped = sum(1 for x in results if isinstance(x, tuple) and x[0])
    await safe_followup(interaction, embed=make_embed("🛑 Admin • Kill All", f"Stopped `{stopped}` VPS instance(s)."))
















# ================================================================
# Admin runtime configuration + plans
# ================================================================

@bot.tree.command(name="admin-config", description="Admin: show, set, or reset runtime settings.")
@app_commands.describe(action="show, set, or reset", key="Setting key", value="New value")
async def admin_config_slash(interaction: discord.Interaction, action: str="show", key: str="", value: str=""):
    if not admin_ok(interaction):
        await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required.")); return
    action=action.strip().lower(); key=key.strip().lower().replace("-","_")
    if action == "show":
        lines=[f"`{k}` → `{runtime_value(k)}`" for k in sorted(RUNTIME_CONFIG_SPEC)]
        await safe_respond(interaction, embed=make_embed("⚙️ Admin • Runtime Settings", "\n".join(lines[:60]))); return
    if key not in RUNTIME_CONFIG_SPEC:
        await safe_respond(interaction, embed=make_embed("❌ Unknown Setting", "Use `/admin-config action:show`.")); return
    try:
        if action == "set":
            value=set_runtime_value(key,value,interaction.user.id)
            await safe_respond(interaction, embed=make_embed("✅ Setting Updated", f"`{key}` = `{clean(value)}`"))
        elif action == "reset":
            reset_runtime_value(key)
            await safe_respond(interaction, embed=make_embed("♻️ Setting Reset", f"`{key}` = `{clean(runtime_value(key))}`"))
        else:
            await safe_respond(interaction, embed=make_embed("❌ Invalid Action", "Use `show`, `set`, or `reset`."))
    except (ValueError, KeyError) as exc:
        await safe_respond(interaction, embed=make_embed("❌ Invalid Value", safe_log(exc)))


@bot.command(name="admin-config")
async def prefix_admin_config(ctx: commands.Context, action: str="show", key: str="", *, value: str=""):
    if not admin_ok(ctx):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Administrator access is required.")); return
    action=action.strip().lower(); key=key.strip().lower().replace("-","_")
    if action == "show":
        lines=[f"`{k}` → `{runtime_value(k)}`" for k in sorted(RUNTIME_CONFIG_SPEC)]
        await safe_ctx_send(ctx, make_embed("⚙️ Admin • Runtime Settings", "\n".join(lines[:60]))); return
    if key not in RUNTIME_CONFIG_SPEC:
        await safe_ctx_send(ctx, make_embed("❌ Unknown Setting", "Use `-admin-config show`.")); return
    try:
        if action == "set":
            normalized=set_runtime_value(key,value,ctx.author.id)
            await safe_ctx_send(ctx,make_embed("✅ Setting Updated",f"`{key}` = `{clean(normalized)}`"))
        elif action == "reset":
            reset_runtime_value(key); await safe_ctx_send(ctx,make_embed("♻️ Setting Reset",f"`{key}` = `{clean(runtime_value(key))}`"))
        else:
            await safe_ctx_send(ctx,make_embed("❌ Invalid Action","Use `show`, `set`, or `reset`."))
    except (ValueError,KeyError) as exc:
        await safe_ctx_send(ctx,make_embed("❌ Invalid Value",safe_log(exc)))


@bot.tree.command(name="plan-add", description="Admin: add a VPS plan preset.")
@app_commands.describe(name="Plan name", price="Plan price in coins", ram="RAM", cpu="CPU cores", disk="Disk", status="active or disabled")
async def plan_add_slash(interaction: discord.Interaction, name: str, price: int, ram: str, cpu: str, disk: str, status: str="active"):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    status=status.strip().lower()
    if price < 0 or status not in {"active","disabled"}:
        await safe_respond(interaction,embed=make_embed("❌ Invalid Plan","Price must be non-negative and status must be active/disabled.")); return
    try: ram,cpu,disk=validate_resources(ram,cpu,disk)
    except ValueError as exc: await safe_respond(interaction,embed=make_embed("❌ Invalid Resources",str(exc))); return
    conn=db_connect()
    try:
        cur=conn.execute("INSERT INTO plans(name,price,status,ram,cpu,disk,created_at) VALUES(?,?,?,?,?,?,?)",(name.strip()[:80],price,status,ram,cpu,disk,utc_now()))
    except sqlite3.IntegrityError:
        await safe_respond(interaction,embed=make_embed("⚠️ Plan Exists","A plan with that name already exists.")); return
    finally: conn.close()
    await safe_respond(interaction,embed=make_embed("✅ Plan Added",f"Plan `#{cur.lastrowid}` • `{clean(name)}` • `{price:,}` coins"))


@bot.tree.command(name="plan-edit", description="Admin: edit a VPS plan.")
@app_commands.describe(plan_id="Plan ID", name="Plan name", price="Price", ram="RAM", cpu="CPU", disk="Disk", status="active or disabled")
async def plan_edit_slash(interaction: discord.Interaction, plan_id: int, name: str="", price: int=-1, ram: str="", cpu: str="", disk: str="", status: str=""):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    conn=db_connect()
    try: row=conn.execute("SELECT * FROM plans WHERE id=?",(int(plan_id),)).fetchone()
    finally: conn.close()
    if not row: await safe_respond(interaction,embed=make_embed("❌ Plan Not Found","That plan does not exist.")); return
    name = name.strip() or str(row['name']); price = int(row['price']) if price < 0 else price
    ram = str(row['ram']) if not ram.strip() else ram; cpu=str(row['cpu']) if not cpu.strip() else cpu; disk=str(row['disk']) if not disk.strip() else disk; status=status.strip().lower() or str(row['status'])
    if price < 0 or status not in {"active","disabled"}:
        await safe_respond(interaction,embed=make_embed("❌ Invalid Plan","Price must be non-negative and status must be active/disabled.")); return
    try: ram,cpu,disk=validate_resources(ram,cpu,disk)
    except ValueError as exc: await safe_respond(interaction,embed=make_embed("❌ Invalid Resources",str(exc))); return
    conn=db_connect()
    try:
        cur=conn.execute("UPDATE plans SET name=?,price=?,status=?,ram=?,cpu=?,disk=? WHERE id=?",(name[:80],price,status,ram,cpu,disk,int(plan_id)))
    except sqlite3.IntegrityError:
        await safe_respond(interaction,embed=make_embed("⚠️ Plan Name Exists","Another plan already uses that name.")); return
    finally: conn.close()
    await safe_respond(interaction,embed=make_embed("✅ Plan Updated",f"Plan `#{plan_id}` • `{clean(name)}` • `{price:,}` coins"))


@bot.tree.command(name="plan-remove", description="Admin: remove a VPS plan.")
async def plan_remove_slash(interaction: discord.Interaction, plan_id: int):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    conn=db_connect()
    try:
        cur=conn.execute("DELETE FROM plans WHERE id=?",(int(plan_id),))
    finally: conn.close()
    await safe_respond(interaction,embed=make_embed("🗑️ Plan Removed" if cur.rowcount else "❌ Plan Not Found",f"Plan ID: `{plan_id}`"))


@bot.tree.command(name="plan-status", description="Admin: enable or disable a VPS plan.")
async def plan_status_slash(interaction: discord.Interaction, plan_id: int, status: str):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    status=status.strip().lower()
    if status not in {"active","disabled"}:
        await safe_respond(interaction,embed=make_embed("❌ Invalid Status","Use `active` or `disabled`.")); return
    conn=db_connect()
    try: cur=conn.execute("UPDATE plans SET status=? WHERE id=?",(status,int(plan_id)))
    finally: conn.close()
    await safe_respond(interaction,embed=make_embed("✅ Plan Updated" if cur.rowcount else "❌ Plan Not Found",f"Plan `{plan_id}` → `{status}`"))


@bot.tree.command(name="plans", description="Show the available VPS plans.")
async def plans_slash(interaction: discord.Interaction):
    rows=plan_rows(); embed=make_embed("🛒 RGNODES™ • VPS Plans")
    if not rows: embed.description="No plans configured."
    for row in rows[:25]: embed.add_field(name=f"#{row['id']} • {clean(row['name'])}",value=f"💰 `{int(row['price']):,}` coins • `{clean(row['status'])}`\n🧠 `{row['ram']}` RAM • ⚙️ `{row['cpu']}` CPU • 💾 `{row['disk']}`",inline=False)
    await safe_respond(interaction,embed=embed)


# ================================================================
# System bootstrap commands
# ================================================================

def confirm_value(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"confirm", "true", "yes", "y", "1"}


@bot.tree.command(
    name="install-system",
    description="Admin: install/repair Linux and Docker dependencies.",
)
@app_commands.describe(confirm="Set true to actually run the system bootstrap.")
async def install_system_slash(interaction: discord.Interaction, confirm: bool = False):
    if not admin_ok(interaction):
        await safe_respond(interaction, embed=make_embed(
            "❌ Permission Denied",
            "Administrator access is required.",
        ))
        return

    if not confirm:
        info = host_os_info()
        await safe_respond(interaction, embed=make_embed(
            "🛠️ RGNODES™ • Install System",
            (
                "This command can install/repair the small Linux dependency set "
                "and Docker when Docker is missing.\n\n"
                f"**Detected OS:** `{clean(info['name'])}`\n"
                f"**PID 1:** `{clean(info['pid1'])}`\n"
                f"**Root:** `{clean(info['root'])}`\n\n"
                "**Nothing has been changed.**\n"
                "Run `/install-system confirm:true` to proceed."
            ),
        ))
        return

    if not await safe_defer(interaction, ephemeral=True):
        return
    try:
        ok, result = await asyncio.wait_for(install_system_dependencies(), timeout=900)
        title = "✅ Install System Complete" if ok else "⚠️ Install System Finished With Issues"
        await safe_followup(interaction, embed=make_embed(title, f"```text\n{safe_log(result, 3800)}\n```"))
    except asyncio.TimeoutError:
        await safe_followup(interaction, embed=make_embed(
            "❌ Install System Timeout",
            "The host bootstrap exceeded the 15-minute safety timeout. Check the host package manager and Docker daemon manually.",
        ))
    except Exception as exc:
        logger.exception("install-system failed")
        await safe_followup(interaction, embed=make_embed(
            "❌ Install System Failed",
            f"```text\n{safe_log(exc, 3800)}\n```",
        ))



# ================================================================
# RGNODES Pro economy, admin controls, security and node management
# ================================================================

def _duration_seconds(raw: str) -> int:
    text = str(raw or "").strip().lower()
    match = re.fullmatch(r"(\d+)\s*([smhdw])", text)
    if not match:
        raise ValueError("Use a duration like `30m`, `2h`, `7d`, or `1w`.")
    amount = int(match.group(1))
    unit = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]
    return amount * unit


def _cooldown_text(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def economy_embed(user_id: int, title: str = "💰 RGNODES™ • Economy") -> discord.Embed:
    wallet, bank = db_balance(user_id)
    row = db_economy(user_id)
    embed = make_embed(title)
    embed.add_field(name="👛 Wallet", value=f"`{wallet:,}` coins", inline=True)
    embed.add_field(name="🏦 Bank", value=f"`{bank:,}` coins", inline=True)
    embed.add_field(name="💎 Total", value=f"`{wallet + bank:,}` coins", inline=True)
    embed.add_field(name="🎟️ Invites", value=f"`{int(row['invites'])}`", inline=True)
    embed.add_field(name="🖥️ VPS Slots", value=f"`{db_effective_slots(user_id)}`", inline=True)
    return embed


def db_set_invites(user_id: int, amount: int) -> None:
    db_economy(user_id)
    conn = db_connect()
    try:
        conn.execute("UPDATE economy SET invites=?,updated_at=? WHERE user_id=?", (max(0, int(amount)), utc_now(), int(user_id)))
    finally:
        conn.close()


def db_take_invites(user_id: int, amount: int) -> bool:
    db_economy(user_id)
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT invites FROM economy WHERE user_id=?", (int(user_id),)).fetchone()
        if not row or int(row[0]) < amount:
            conn.rollback()
            return False
        conn.execute("UPDATE economy SET invites=invites-?,updated_at=? WHERE user_id=?", (int(amount), utc_now(), int(user_id)))
        conn.commit()
        return True
    finally:
        conn.close()


def plan_rows() -> list[sqlite3.Row]:
    conn=db_connect()
    try: return conn.execute("SELECT * FROM plans ORDER BY price,id").fetchall()
    finally: conn.close()


def parse_plan_spec(name: str, price: int, status: str, ram: str | None, cpu: str | None, disk: str | None) -> tuple[str,str,str]:
    raw = str(name).lower()
    found = re.search(r"(\d+(?:\.\d+)?)\s*g", raw)
    plan_ram = ram or (f"{found.group(1)}g" if found else DEFAULT_RAM)
    c = re.search(r"(\d+(?:\.\d+)?)\s*(?:core|cores|c)", raw)
    plan_cpu = cpu or (c.group(1) if c else DEFAULT_CPU)
    d = re.search(r"(\d+(?:\.\d+)?)\s*g\s*(?:disk|ssd|storage)", raw)
    plan_disk = disk or (d.group(1)+"g" if d else DEFAULT_DISK)
    validate_resources(plan_ram, plan_cpu, plan_disk)
    return plan_ram.lower(), str(plan_cpu), plan_disk.lower()


async def scan_vps_security(vps: sqlite3.Row) -> tuple[bool, str]:
    """High-confidence defensive scan; never executes attacker-supplied commands."""
    if str(vps["backend"] or "docker").lower() != "docker":
        return False, "unsupported backend"
    if await docker_state(vps["container_id"]) != "running":
        return False, "not running"
    rc, out, err = await docker_cli("top", str(vps["container_id"]), "-eo", "pid,comm,args", timeout=20, retries=1)
    text=(out+err).decode("utf-8","replace").lower()
    suspicious=("xmrig","minerd","cpuminer","kinsing","kdevtmpfsi","cryptonight","masscan","zmap")
    hit=next((x for x in suspicious if x in text),None)
    if hit:
        return True, f"high-confidence suspicious process detected: {hit}"
    return False, "clean"


async def security_sweep(delete_suspicious: bool = False) -> tuple[int,int,list[str]]:
    scanned=suspicious=0; names=[]
    for vps in db_get_all_vps():
        try:
            hit, detail = await scan_vps_security(vps)
            scanned += 1
            if hit:
                suspicious += 1; names.append(f"#{vps['id']} {vps['container_name']}: {detail}")
                db_update_vps(vps["container_id"], suspended=1, status="stopped")
                if delete_suspicious:
                    await lifecycle_action(vps, "delete")
        except Exception as exc:
            logger.warning("Security scan failed for VPS #%s: %s", vps["id"], safe_log(exc))
    return scanned,suspicious,names


class MassDMModal(discord.ui.Modal, title="📨 RGNODES™ • Mass DM"):
    message = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, placeholder="Write your message…", max_length=4000, required=True)
    def __init__(self, members: list[discord.Member]):
        super().__init__(timeout=300)
        self.members=members
    async def on_submit(self, interaction: discord.Interaction):
        if not await safe_defer(interaction, ephemeral=True, claim=False):
            return
        sent=failed=0
        for member in self.members:
            if member.bot: continue
            try:
                await member.send(embed=make_embed("📢 RGNODES™ Announcement", str(self.message.value)))
                sent += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
            await asyncio.sleep(0.15)
        await safe_followup(interaction, embed=make_embed("✅ Mass DM Complete", f"Sent: `{sent}`\nFailed/closed: `{failed}`"), ephemeral=True)


class MassDMView(discord.ui.View):
    def __init__(self, members: list[discord.Member]):
        super().__init__(timeout=300)
        self.members=members
    @discord.ui.button(label="Write Message", emoji="✉️", style=discord.ButtonStyle.primary)
    async def write(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_ok(interaction):
            await safe_respond(interaction, embed=make_embed("❌ Permission Denied", "Administrator access is required."), ephemeral=True); return
        await interaction.response.send_modal(MassDMModal(self.members))


@bot.tree.command(name="create", description="Admin: create a VPS for a user.")
@app_commands.describe(target_user="User", ram="RAM", cpu="CPU cores", disk="Disk", location="SG or IN")
async def admin_create_pro(interaction: discord.Interaction, target_user: discord.User, ram: str=DEFAULT_RAM, cpu: str=DEFAULT_CPU, disk: str=DEFAULT_DISK, location: str=DEFAULT_LOCATION):
    if not admin_ok(interaction):
        await safe_respond(interaction, embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    if await safe_defer(interaction, ephemeral=True):
        await deploy_flow(interaction,user=target_user,os_type="ubuntu-24.04",location=location,ram=ram,cpu=cpu,disk=disk,charge_cost=False)


@bot.tree.command(name="status-all-vm", description="Admin: show all VPS status.")
async def status_all_vm(interaction: discord.Interaction):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    rows=db_get_all_vps(); running=sum(1 for r in rows if r["status"]=="running" and not r["suspended"])
    embed=make_embed("🖥️ RGNODES™ • All VPS Status", f"Total: `{len(rows)}` • Running: `{running}` • Global create limit: `{db_total_create_limit()}`")
    for r in rows[:25]: embed.add_field(name=f"#{r['id']} • {clean(r['container_name'])}",value=f"<@{r['user_id']}> • {status_text(r['status'],bool(r['suspended']))}",inline=False)
    await safe_respond(interaction,embed=embed)


@bot.tree.command(name="suspend-user", description="Admin: suspend a user for a duration.")
async def suspend_user_slash(interaction: discord.Interaction, target_user: discord.User, duration: str="1h"):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    try: seconds=_duration_seconds(duration)
    except ValueError as exc: await safe_respond(interaction,embed=make_embed("❌ Invalid Duration",str(exc))); return
    until=datetime.now(timezone.utc)+timedelta(seconds=seconds); db_set_user_suspension(target_user.id,until)
    for v in db_get_user_vps(target_user.id):
        await lifecycle_action(v,"stop"); db_update_vps(v["container_id"],suspended=1)
    await safe_respond(interaction,embed=make_embed("⛔ User Suspended",f"<@{target_user.id}> suspended for `{duration}`."))


@bot.tree.command(name="unsuspend-user", description="Admin: unsuspend a user.")
async def unsuspend_user_slash(interaction: discord.Interaction,target_user: discord.User):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    db_clear_user_suspension(target_user.id)
    for v in db_get_user_vps(target_user.id): db_update_vps(v["container_id"],suspended=0)
    await safe_respond(interaction,embed=make_embed("✅ User Unsuspended",f"<@{target_user.id}> can create VPS again."))


@bot.tree.command(name="add-total-slot", description="Admin: set global VPS creation limit.")
async def add_total_slot_slash(interaction: discord.Interaction, amount: int):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    value=db_set_total_create_limit(amount)
    await safe_respond(interaction,embed=make_embed("🎟️ Global Limit Updated",f"Maximum total VPS records: `{value}`."))


@bot.tree.command(name="add-user-slots", description="Admin: add VPS slots to a user.")
async def add_user_slots_pro(interaction: discord.Interaction,target_user: discord.User,amount: int):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    db_upsert_user(target_user.id,str(target_user)); total=db_add_slots(target_user.id,amount)
    await safe_respond(interaction,embed=make_embed("🎟️ User Slots Updated",f"<@{target_user.id}> now has `{total}` slots."))


@bot.tree.command(name="add-coins", description="Admin: add coins to a user wallet.")
async def add_coins_slash(interaction: discord.Interaction,target_user: discord.User,amount: int):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    if amount<=0: await safe_respond(interaction,embed=make_embed("❌ Invalid Amount","Amount must be positive.")); return
    total=db_add_coins(target_user.id,amount)
    await safe_respond(interaction,embed=make_embed("💰 Coins Added",f"Added `{amount:,}` coins to <@{target_user.id}>. Combined balance: `{total:,}`."))


@bot.tree.command(name="rm-coins", description="Admin: remove coins from a user wallet.")
async def rm_coins_slash(interaction: discord.Interaction,target_user: discord.User,amount: int):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    ok=db_take_coins(target_user.id,amount)
    await safe_respond(interaction,embed=make_embed("🗑️ Coins Removed" if ok else "❌ Insufficient Coins",f"Removed `{amount:,}` coins from <@{target_user.id}>." if ok else "The user does not have enough wallet coins."))


@bot.tree.command(name="dm-all", description="Admin: open a message box for mass DM.")
async def dm_all_slash(interaction: discord.Interaction):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    await safe_respond(interaction,embed=make_embed("📨 RGNODES™ • Mass DM","Press **Write Message** to open the message box. Emojis are supported."),view=MassDMView(interaction.guild.members if interaction.guild else []))


@bot.tree.command(name="dm", description="Admin: DM a user.")
async def dm_slash(interaction: discord.Interaction,target_user: discord.User):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    await safe_respond(interaction,embed=make_embed("📨 DM User",f"Open a message box for <@{target_user.id}>."),view=SingleDMView(target_user))


class SingleDMModal(discord.ui.Modal, title="📨 RGNODES™ • Direct Message"):
    message=discord.ui.TextInput(label="Message",style=discord.TextStyle.paragraph,max_length=4000,required=True)
    def __init__(self,target: discord.User): super().__init__(timeout=300); self.target=target
    async def on_submit(self,interaction: discord.Interaction):
        if not await safe_defer(interaction,ephemeral=True,claim=False):
            return
        ok=await safe_dm(self.target,make_embed("📨 RGNODES™ • Message",str(self.message.value)))
        await safe_followup(interaction,embed=make_embed("✅ DM Sent" if ok else "❌ DM Failed",f"Target: <@{self.target.id}>"),ephemeral=True)
class SingleDMView(discord.ui.View):
    def __init__(self,target: discord.User): super().__init__(timeout=300); self.target=target
    @discord.ui.button(label="Write Message",emoji="✉️",style=discord.ButtonStyle.primary)
    async def write(self,interaction: discord.Interaction,button: discord.ui.Button):
        if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
        await interaction.response.send_modal(SingleDMModal(self.target))


@bot.tree.command(name="backup-vm", description="Admin: create a safe VPS backup archive.")
async def backup_vm_slash(interaction: discord.Interaction):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    if not await safe_defer(interaction,ephemeral=True): return
    archive=Path(tempfile.gettempdir())/f"rgnodes-backup-{int(time.time())}.zip"
    try:
        with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as z:
            conn=db_connect()
            try:
                dbcopy=archive.with_suffix(".db"); shutil.copy2(DATABASE_FILE,dbcopy); z.write(dbcopy,"vps_bot.db"); dbcopy.unlink(missing_ok=True)
                manifest=[]
                for r in db_get_all_vps():
                    if int(r["critical"] or 0): continue
                    meta={k:("<redacted>" if k=="ssh_password" else r[k]) for k in r.keys()}
                    manifest.append(meta)
                    logs=await docker_logs(r["container_id"],80)
                    z.writestr(f"logs/vps-{r['id']}.txt",logs[:100000])
                z.writestr("metadata.json",json.dumps(manifest,indent=2,default=str))
            finally: conn.close()
        size=archive.stat().st_size
        if size>20*1024*1024:
            await safe_followup(interaction,embed=make_embed("⚠️ Backup Created",f"Archive created but is too large for a Discord DM attachment (`{size/1024/1024:.1f}MB`). Host path: `{archive}`")); return
        sent=await safe_dm_file(interaction.user,make_embed("💾 RGNODES™ • VPS Backup","Backup excludes VPS records marked critical. SSH passwords are redacted."),str(archive))
        await safe_followup(interaction,embed=make_embed("✅ Backup Complete" if sent else "⚠️ Backup Created",f"Archive size: `{size/1024/1024:.1f}MB`. Admin DM: `{'sent' if sent else 'unavailable'}`."))
    finally:
        with contextlib.suppress(OSError): archive.unlink()


@bot.tree.command(name="vm-backup", description="Admin: backup one user's VPS metadata/logs.")
async def vm_backup_slash(interaction: discord.Interaction,target_user: discord.User):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    rows=[r for r in db_get_user_vps(target_user.id) if not int(r["critical"] or 0)]
    embed=make_embed("💾 User VPS Backup",f"Target: <@{target_user.id}> • VPS included: `{len(rows)}`")
    for r in rows[:25]: embed.add_field(name=f"#{r['id']} {clean(r['container_name'])}",value=f"{os_label(r['os_type'])} • {r['ram']} • {r['cpu']} CPU • {r['disk']}",inline=False)
    await safe_respond(interaction,embed=embed)


@bot.tree.command(name="reset-pass", description="Admin: reset root SSH password for a user's VPS.")
async def reset_pass_slash(interaction: discord.Interaction,target_user: discord.User):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    rows=db_get_user_vps(target_user.id)
    if not rows: await safe_respond(interaction,embed=make_embed("❌ No VPS", "User has no VPS.")); return
    password="".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(SSH_PASSWORD_LENGTH))
    ok_count=0
    for v in rows:
        rc,_,err=await docker_exec(v["container_id"],"bash","-lc",f"printf '%s\\n' {json.dumps('root:'+password)} | chpasswd",timeout=20,retries=0)
        if rc==0: db_update_vps(v["container_id"],ssh_password=password); ok_count+=1
    await safe_dm(target_user,make_embed("🔐 RGNODES™ • SSH Password Reset",f"Your root SSH password was reset.\n\n**Password:** `{password}`\n\nKeep it private."))
    await safe_respond(interaction,embed=make_embed("🔐 Password Reset",f"Updated `{ok_count}/{len(rows)}` VPS. New password sent by DM to <@{target_user.id}>."))


@bot.tree.command(name="anty-hacking", description="Admin: enable defensive anti-hacking scans.")
async def anty_hacking_slash(interaction: discord.Interaction):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    conn=db_connect()
    try: conn.execute("INSERT INTO security_settings(key,value) VALUES('anti_hacking','1') ON CONFLICT(key) DO UPDATE SET value='1'")
    finally: conn.close()
    scanned, suspicious, names=await security_sweep(delete_suspicious=True)
    await safe_respond(interaction,embed=make_embed("🛡️ Anti-Hacking Enabled",f"Scanned: `{scanned}` • Removed suspicious VPS: `{suspicious}`\n"+"\n".join(names[:10]) if names else "All scanned VPS were clean."))


@bot.tree.command(name="nodes", description="Show configured RGNODES nodes.")
async def nodes_slash(interaction: discord.Interaction):
    conn=db_connect()
    try: rows=conn.execute("SELECT * FROM nodes ORDER BY id").fetchall()
    finally: conn.close()
    embed=make_embed("🌍 RGNODES™ • Nodes",f"Configured nodes: `{len(rows)}`")
    for n in rows[:25]: embed.add_field(name=f"#{n['id']} • {clean(n['name'])}",value=f"{location_label(n['location'])} • `{clean(n['host'])}:{n['port']}` • **{clean(n['status'])}**",inline=False)
    if not rows: embed.description="No nodes configured. An admin can use `/add-node`."
    await safe_respond(interaction,embed=embed)


@bot.tree.command(name="add-node", description="Admin: add a node record.")
async def add_node_slash(interaction: discord.Interaction,name: str,location: str,host: str,port: int=22):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    location=normalize_location(location) or "SG"
    if not 1<=port<=65535: await safe_respond(interaction,embed=make_embed("❌ Invalid Port","Use 1-65535.")); return
    conn=db_connect()
    try: conn.execute("INSERT INTO nodes(name,location,host,port,status,created_at) VALUES(?,?,?,?,?,?)",(name[:80],location,host[:255],port,"online",utc_now()))
    except sqlite3.IntegrityError: await safe_respond(interaction,embed=make_embed("⚠️ Node Exists","A node with that name already exists.")); return
    finally: conn.close()
    await safe_respond(interaction,embed=make_embed("✅ Node Added",f"`{name}` • {location_label(location)} • `{host}:{port}`"))


@bot.tree.command(name="add-plans", description="Admin: add a resource/slot plan.")
async def add_plans_slash(interaction: discord.Interaction,name: str,price: int,status: str="active",ram: str=DEFAULT_RAM,cpu: str=DEFAULT_CPU,disk: str=DEFAULT_DISK):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    try: ram,cpu,disk=validate_resources(ram,cpu,disk)
    except ValueError as exc: await safe_respond(interaction,embed=make_embed("❌ Invalid Plan Resources",str(exc))); return
    conn=db_connect()
    try: cur=conn.execute("INSERT INTO plans(name,price,status,ram,cpu,disk,created_at) VALUES(?,?,?,?,?,?,?)",(name[:80],max(0,price),status[:20],ram,cpu,disk,utc_now())); plan_id=cur.lastrowid
    except sqlite3.IntegrityError: await safe_respond(interaction,embed=make_embed("⚠️ Plan Exists","That plan name already exists.")); return
    finally: conn.close()
    await safe_respond(interaction,embed=make_embed("✅ Plan Added",f"Plan ID: `{plan_id}` • `{name}` • `{price:,}` coins • `{status}`"))


@bot.tree.command(name="rm-redeem", description="Admin: remove a redeem code.")
async def rm_redeem_slash(interaction: discord.Interaction,code_id: int):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    conn=db_connect()
    try: cur=conn.execute("DELETE FROM redeem_codes WHERE id=?",(int(code_id),))
    finally: conn.close()
    await safe_respond(interaction,embed=make_embed("🗑️ Redeem Removed" if cur.rowcount else "❌ Redeem Not Found",f"Redeem ID: `{code_id}`"))


@bot.tree.command(name="add-redeem", description="Admin: add a redeem code.")
async def add_redeem_slash(interaction: discord.Interaction,code: str,reward_coins: int=0,reward_slots: int=0,max_uses: int=1):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    conn=db_connect()
    try: cur=conn.execute("INSERT INTO redeem_codes(code,reward_coins,reward_slots,max_uses,uses,status,created_at) VALUES(?,?,?,?,?,?,?)",(code.strip().upper(),max(0,reward_coins),max(0,reward_slots),max(1,max_uses),0,"active",utc_now()))
    except sqlite3.IntegrityError: await safe_respond(interaction,embed=make_embed("⚠️ Code Exists","That redeem code already exists.")); return
    finally: conn.close()
    await safe_respond(interaction,embed=make_embed("🎁 Redeem Added",f"ID: `{cur.lastrowid}` • Code: `{code.upper()}` • Coins: `{reward_coins:,}` • Slots: `{reward_slots}` • Uses: `{max_uses}`"))


@bot.tree.command(name="rm", description="Admin: remove one user's selected VPS.")
async def admin_rm_slash(interaction: discord.Interaction,target_user: discord.User,vps_identifier: str|None=None):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    v=db_find_vps(target_user.id,vps_identifier)
    if not v: await safe_respond(interaction,embed=make_embed("❌ VPS Not Found","No VPS matched.")); return
    ok,msg=await lifecycle_action(v,"delete"); await safe_respond(interaction,embed=make_embed("✅ VPS Removed" if ok else "❌ Remove Failed",msg))


@bot.tree.command(name="re-boot", description="Admin: restart all VPS and security-scan them.")
async def reboot_all_slash(interaction: discord.Interaction):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    if not await safe_defer(interaction,ephemeral=True): return
    scanned,suspicious,names=await security_sweep(delete_suspicious=True)
    rows=db_get_all_vps(); restarted=0
    for v in rows:
        if str(v["status"]).lower()=="running" and not v["suspended"]:
            ok,_=await lifecycle_action(v,"restart"); restarted += int(ok)
    await safe_followup(interaction,embed=make_embed("🔄 Re-Boot Complete",f"Scanned: `{scanned}` • removed: `{suspicious}` • restarted: `{restarted}`" + ("\n"+"\n".join(names[:10]) if names else "")))


@bot.tree.command(name="re-install", description="Admin: reinstall all VPS with their current OS.")
async def reinstall_all_slash(interaction: discord.Interaction):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    if not await safe_defer(interaction,ephemeral=True): return
    rows=db_get_all_vps(); success=failed=0
    for v in rows:
        try:
            ok,_=await docker_reinstall_vps(v,str(v["os_type"])); success+=int(ok); failed+=int(not ok)
        except Exception as exc: failed+=1; logger.warning("Reinstall-all VPS #%s failed: %s",v["id"],safe_log(exc))
    await safe_followup(interaction,embed=make_embed("♻️ Re-Install Complete",f"Success: `{success}` • Failed: `{failed}`"))



@bot.tree.command(name="rm-all", description="Admin: remove all VPS instances.")
async def rm_all_slash_alias(interaction: discord.Interaction, confirm: bool=False):
    await remove_all_slash(interaction, confirm)

@bot.tree.command(name="vm-creating-ban", description="Admin: block VPS creation for a user.")
async def vm_creating_ban_slash(interaction: discord.Interaction, target_user: discord.User):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    db_set_ban(target_user.id,True); await safe_respond(interaction,embed=make_embed("🚫 VPS Creation Banned",f"<@{target_user.id}> cannot create VPS."))

@bot.tree.command(name="suspand", description="Admin: suspend a user for a duration.")
async def suspand_slash_alias(interaction: discord.Interaction,target_user: discord.User,duration: str="1h"):
    await suspend_user_slash(interaction,target_user,duration)

@bot.tree.command(name="unsuspand", description="Admin: unsuspend a user.")
async def unsuspand_slash_alias(interaction: discord.Interaction,target_user: discord.User):
    await unsuspend_user_slash(interaction,target_user)

@bot.tree.command(name="ssh-user", description="Admin: send a user's SSH credentials by DM.")
async def ssh_user_slash(interaction: discord.Interaction,target_user: discord.User):
    if not admin_ok(interaction): await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Administrator access is required.")); return
    rows=db_get_user_vps(target_user.id)
    if not rows: await safe_respond(interaction,embed=make_embed("❌ VPS Not Found","User has no VPS.")); return
    v=rows[0]; network=await detect_public_network(force=True); ip=network.get("ip") if valid_public_ipv4(network.get("ip")) else None
    port=next((int(r["host_port"]) for r in db_list_ports(v["id"]) if int(r["container_port"])==22),None)
    sent=await safe_dm(target_user,ssh_access_embed(v,ip,port)); await safe_respond(interaction,embed=make_embed("✅ SSH Sent" if sent else "⚠️ DM Unavailable",f"SSH information for <@{target_user.id}> was {'sent' if sent else 'not sent'} by DM."))


@bot.tree.command(name="ssh", description="Get your SSH credentials; admins can target a user.")
async def ssh_slash(interaction: discord.Interaction, target_user: discord.User | None = None):
    target = target_user if (admin_ok(interaction) and target_user) else interaction.user
    if target_user is not None and not admin_ok(interaction):
        target = interaction.user
    rows = db_get_user_vps(target.id)
    if not rows:
        await safe_respond(interaction,embed=make_embed("❌ VPS Not Found","No VPS found.")); return
    v=rows[0]; network=await detect_public_network(force=True); ip=network.get('ip') if valid_public_ipv4(network.get('ip')) else None
    port=next((int(r['host_port']) for r in db_list_ports(v['id']) if int(r['container_port'])==22),None)
    sent=await safe_dm(target,ssh_access_embed(v,ip,port))
    await safe_respond(interaction,embed=make_embed("✅ SSH Sent" if sent else "⚠️ DM Unavailable",f"SSH credentials {'sent to' if sent else 'could not be sent to'} <@{target.id}> by DM."))


@bot.tree.command(name="sshx-user", description="Get custom RGNODES SSHx for a user or yourself.")
async def sshx_user_slash(interaction: discord.Interaction, target_user: discord.User | None = None):
    if target_user is not None and not admin_ok(interaction):
        await safe_respond(interaction,embed=make_embed("❌ Permission Denied","Only administrators can target another user.")); return
    target=target_user or interaction.user; rows=db_get_user_vps(target.id)
    if not rows:
        await safe_respond(interaction,embed=make_embed("❌ VPS Not Found","No VPS found.")); return
    ok,msg=await create_console_access(rows[0],target)
    await safe_respond(interaction,embed=make_embed("✅ SSHx Ready" if ok else "❌ SSHx Failed",msg))

# User slash economy commands
@bot.tree.command(name="balance", description="Show your RGNODES coin balance.")
async def balance_slash(interaction: discord.Interaction): await safe_respond(interaction,embed=economy_embed(interaction.user.id))

@bot.tree.command(name="bal", description="Show your current RGNODES coin balance.")
async def bal_slash(interaction: discord.Interaction):
    await safe_respond(interaction, embed=economy_embed(interaction.user.id, "💰 RGNODES™ • Balance"))

@bot.tree.command(name="buy-slot", description="Buy one additional VPS slot with coins.")
async def buy_slot_slash(interaction: discord.Interaction):
    ok, msg, balance, slots = db_purchase_slot(interaction.user.id)
    title="✅ VPS Slot Purchased" if ok else "❌ Slot Purchase Failed"
    await safe_respond(interaction, embed=make_embed(title, f"{msg}\n\n💰 Balance: `{balance:,}` coins\n🎟️ Slots: `{slots}`"))

@bot.tree.command(name="inventory", description="Show your RGNODES inventory/invites.")
async def inventory_slash(interaction: discord.Interaction): await safe_respond(interaction,embed=economy_embed(interaction.user.id,"🎒 RGNODES™ • Inventory"))


# ================================================================
# Prefix compatibility layer
# ================================================================

def ctx_vps(ctx: commands.Context, identifier: str | None = None) -> sqlite3.Row | None:
    """Single VPS resolver for every prefix command. Admins can see all VPSs;
    normal users can see owned or shared VPSs. Empty identifier means newest.
    """
    needle = (identifier or "").strip()
    if ctx.author.id == ADMIN_ID and ADMIN_ID > 0:
        return db_find_vps(ctx.author.id, needle, admin=True)
    return db_find_accessible_vps(ctx.author.id, needle)


@bot.check
async def _global_prefix_event_guard(ctx: commands.Context) -> bool:
    """Prevent the same Discord message from being processed twice.

    This protects against brief multi-process overlap and duplicate gateway
    delivery during a restart without sending a second response.
    """
    message = getattr(ctx, "message", None)
    event_id = getattr(message, "id", None)
    allowed = claim_processed_event(f"msg:{event_id}", "prefix")
    if not allowed:
        setattr(ctx, "_rgnodes_duplicate_event", True)
    return allowed


@bot.command(name="share-user")
async def prefix_share_user(ctx: commands.Context, target_user: discord.User, vps_identifier: str):
    vps = ctx_vps(ctx, vps_identifier)
    if not vps or not db_is_owner_or_admin(ctx.author.id, vps):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Only the VPS owner or administrator can share this VPS."))
        return
    ok, message = db_share_vps(vps["id"], target_user.id, ctx.author.id)
    await safe_ctx_send(ctx, make_embed("✅ VPS Shared" if ok else "⚠️ Share Failed", f"{message}\nVPS: `{clean(vps['container_name'])}` • User: <@{target_user.id}>"))


@bot.command(name="share-ruser", aliases=["unshare-user"])
async def prefix_share_ruser(ctx: commands.Context, target_user: discord.User, vps_identifier: str):
    vps = ctx_vps(ctx, vps_identifier)
    if not vps or not db_is_owner_or_admin(ctx.author.id, vps):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Only the VPS owner or administrator can revoke access."))
        return
    ok, message = db_unshare_vps(vps["id"], target_user.id)
    await safe_ctx_send(ctx, make_embed("✅ Access Removed" if ok else "⚠️ Nothing Changed", f"{message}\nVPS: `{clean(vps['container_name'])}` • User: <@{target_user.id}>"))




@bot.command(name="myvps", aliases=["myvm"])
async def prefix_myvps(ctx: commands.Context) -> None:
    await prefix_manage(ctx, "")


@bot.command(name="uptime")
async def prefix_uptime(ctx: commands.Context):
    await safe_ctx_send(ctx, make_embed("⏱️ RGNODES™ • Host Uptime", f"Host uptime: `{await host_uptime()}`"))


@bot.command(name="vpsinfo", aliases=["vps-info"])
async def prefix_vpsinfo(ctx: commands.Context, identifier: str = ""):
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No VPS matches that identifier."))
        return
    await safe_ctx_send(ctx, make_embed(
        f"📊 VPS Info • {clean(vps['container_name'])}",
        f"ID: `{vps['id']}`\nStatus: **{status_text(vps['status'], bool(vps['suspended']))}**\n"
        f"OS: `{os_label(vps['os_type'])}`\nLocation: `{location_label(vps['location'])}`\n"
        f"RAM: `{vps['ram']}` • CPU: `{vps['cpu']}` • Disk: `{vps['disk']}`\n"
        f"Backend: `{str(vps['backend'] or 'docker').lower()}`"
    ))


@bot.command(name="vps-stats")
async def prefix_vps_stats(ctx: commands.Context, identifier: str = ""):
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No VPS matches that identifier."))
        return
    vps = await refresh_vps_record_state(vps)
    stats, uptime, disk = await _safe_vps_live_data(vps)
    await safe_ctx_send(ctx, make_embed(
        f"📈 VPS Stats • {clean(vps['container_name'])}",
        f"CPU: `{clean(stats.get('cpu'))}`\nMemory: `{clean(stats.get('memory'))}`\n"
        f"Disk: `{clean(disk.get('used'))} / {clean(vps['disk'])}`\nNetwork: `{clean(stats.get('network'))}`\nUptime: `{clean(uptime)}`"
    ))


@bot.command(name="vps-uptime")
async def prefix_vps_uptime(ctx: commands.Context, identifier: str = ""):
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No VPS matches that identifier."))
        return
    try:
        uptime = await asyncio.wait_for(backend_uptime(vps), timeout=25)
    except Exception as exc:
        logger.warning("Prefix VPS uptime failed for #%s: %s", vps["id"], safe_log(exc))
        uptime = "N/A"
    await safe_ctx_send(ctx, make_embed("⏱️ VPS Uptime", f"`{clean(uptime)}`"))




@bot.command(name="restart-vps")
async def prefix_restart_vps(ctx: commands.Context, identifier: str = ""):
    await prefix_action(ctx, identifier, "restart")


@bot.command(name="snapshot")
async def prefix_snapshot(ctx: commands.Context, identifier: str, name: str = ""):
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No VPS matches that identifier."))
        return
    name = name.strip() or f"snapshot-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    ok, message = await docker_snapshot_create(vps, name)
    await safe_ctx_send(ctx, make_embed("📸 Snapshot Created" if ok else "❌ Snapshot Failed", message))


@bot.command(name="list-snapshots")
async def prefix_list_snapshots(ctx: commands.Context, identifier: str = ""):
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No VPS matches that identifier."))
        return
    rows = db_list_snapshots(vps["id"])
    body = "\n".join(f"`{r['name']}` • {str(r['created_at'])[:19]}" for r in rows[:20]) or "No snapshots."
    await safe_ctx_send(ctx, make_embed(f"📋 Snapshots • {clean(vps['container_name'])}", body))


@bot.command(name="restore-snapshot")
async def prefix_restore_snapshot(ctx: commands.Context, identifier: str, name: str):
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No VPS matches that identifier."))
        return
    if not db_is_owner_or_admin(ctx.author.id, vps):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Only the VPS owner or administrator can restore snapshots."))
        return
    ok, message = await docker_snapshot_restore(vps, name)
    await safe_ctx_send(ctx, make_embed("✅ Snapshot Restored" if ok else "❌ Restore Failed", message))


@bot.command(name="manage-shared")
async def prefix_manage_shared(ctx: commands.Context, target_user: discord.User, vps_identifier: str):
    vps = db_find_vps(target_user.id, vps_identifier)
    if not vps or not db_is_owner_or_admin(ctx.author.id, vps):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Only the VPS owner or administrator can manage shared access."))
        return
    shared = db_list_shared(vps["id"])
    body = "\n".join(f"<@{r['user_id']}> • granted by <@{r['shared_by']}>" for r in shared) or "No users currently have shared access."
    await safe_ctx_send(ctx, make_embed(f"👥 Shared Access • {clean(vps['container_name'])}", body), ManageView(vps["id"], vps["user_id"]))


@bot.command(name="serverstats")
async def prefix_serverstats(ctx: commands.Context):
    info = host_os_info()
    try:
        load = os.getloadavg()[0]
        load_text = f"{load:.2f}"
    except (AttributeError, OSError):
        load_text = "N/A"
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
        total_kb = int(re.search(r"^MemTotal:\\s+(\\d+)", meminfo, re.M).group(1))
        avail_kb = int(re.search(r"^MemAvailable:\\s+(\\d+)", meminfo, re.M).group(1))
        ram_text = f"{format_bytes((total_kb-avail_kb)*1024)} / {format_bytes(total_kb*1024)}"
    except Exception:
        ram_text = "N/A"
    disk = shutil.disk_usage("/")
    ok, active = await docker_running_count()
    if not ok:
        active = db_running_count()
    await safe_ctx_send(ctx, make_embed("📊 RGNODES™ • Server Statistics",
        f"OS: `{clean(info['name'], 100)}`\nPID 1: `{clean(info['pid1'])}`\n"
        f"Host uptime: `{await host_uptime()}`\nRAM: `{ram_text}`\n"
        f"Disk: `{format_bytes(disk.used)} / {format_bytes(disk.total)}`\n"
        f"CPU cores: `{os.cpu_count() or 1}` • Load: `{load_text}`\nActive VPS: `{active}`"))


@bot.command(name="thresholds")
async def prefix_thresholds(ctx: commands.Context):
    await safe_ctx_send(ctx, make_embed("📋 RGNODES™ • Thresholds",
        f"Per-user slots: `{db_effective_slots(ctx.author.id)}`\n"
        f"Global running VPS: `{runtime_int('total_running_limit')}`\n"
        f"Max ports/VPS: `{runtime_int('max_ports_per_vps')}`\n"
        f"Port range: `{runtime_int('port_range_start')}-{runtime_int('port_range_end')}`\n"
        f"RAM per VPS: `256MB-256GB`\nDisk per VPS: `1GB-10TB`\nCPU per VPS: `0-64 cores`"))


@bot.command(name="set-status")
async def prefix_set_status(ctx: commands.Context, status_type: str, *, name: str):
    if not admin_ok(ctx):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Administrator access is required."))
        return
    kind = status_type.strip().lower()
    activity: discord.BaseActivity
    if kind == "watching":
        activity = discord.Activity(type=discord.ActivityType.watching, name=name)
    elif kind == "listening":
        activity = discord.Activity(type=discord.ActivityType.listening, name=name)
    elif kind == "competing":
        activity = discord.Activity(type=discord.ActivityType.competing, name=name)
    else:
        kind = "playing"
        activity = discord.Game(name=name)
    await bot.change_presence(activity=activity)
    await safe_ctx_send(ctx, make_embed("✅ Status Updated", f"Type: `{kind}`\nName: `{clean(name, 200)}`"))


@bot.group(name="ports", invoke_without_command=True)
async def prefix_ports(ctx: commands.Context, identifier: str = ""):
    if ctx.invoked_subcommand:
        return
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", f"Use `{PREFIX}ports list <vps#>` or open `{PREFIX}manage`."))
        return
    if str(vps["backend"] or "docker").lower() != "docker":
        panel = await ptero_panel_link(vps)
        await safe_ctx_send(ctx, make_embed("🦖 Pterodactyl Ports", f"Port allocations are managed by Pterodactyl.\nPanel: {panel or 'not configured'}"))
        return
    rows = db_list_ports(vps["id"])
    body = "\n".join(f"#{r['id']} • `{r['host_port']}→{r['container_port']}/TCP` • `{r['status']}`" for r in rows) or "No forwarding rules."
    await safe_ctx_send(ctx, make_embed(f"🌐 Ports • {clean(vps['container_name'])}", body))


@prefix_ports.command(name="add")
async def prefix_ports_add(ctx: commands.Context, identifier: str, container_port: int, host_port: int = 0):
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No VPS matches that identifier."))
        return
    if str(vps["backend"] or "docker").lower() != "docker":
        await safe_ctx_send(ctx, make_embed("🦖 Pterodactyl Allocations", "This VPS uses Pterodactyl. Manage ports/allocations from the panel."))
        return
    if not 1 <= container_port <= 65535:
        await safe_ctx_send(ctx, make_embed("❌ Invalid Port", "Container port must be between 1 and 65535."))
        return
    async with PORT_LOCK:
        if len(db_list_ports(vps["id"])) >= runtime_int('max_ports_per_vps'):
            await safe_ctx_send(ctx, make_embed("⚠️ Port Limit Reached", f"Maximum `{runtime_int('max_ports_per_vps')}` forwarding rules per VPS."))
            return
        if db_find_port(vps["id"], container_port, "tcp"):
            await safe_ctx_send(ctx, make_embed("⚠️ Already Exists", "That container port is already forwarded."))
            return
        if host_port == 0:
            host_port = await allocate_host_port() or 0
        if not 1024 <= host_port <= 65535 or not port_bindable(host_port):
            await safe_ctx_send(ctx, make_embed("❌ Host Port Unavailable", "Use a free port 1024–65535 or 0 for auto-allocation."))
            return
        try:
            port_id = db_insert_port(vps["id"], container_port, host_port, "tcp")
        except sqlite3.IntegrityError:
            await safe_ctx_send(ctx, make_embed("❌ Port Conflict", "That public port is already reserved."))
            return
    row = db_get_port(port_id)
    ok, message = await start_port_forward(row, vps) if row else (False, "Forwarding record disappeared.")
    if not ok:
        if row:
            await stop_port_forward(row)
        db_delete_port(port_id)
    await safe_ctx_send(ctx, make_embed("✅ Port Added" if ok else "❌ Port Failed", message))


@prefix_ports.command(name="list")
async def prefix_ports_list(ctx: commands.Context, identifier: str = ""):
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No VPS matches that identifier."))
        return
    if str(vps["backend"] or "docker").lower() != "docker":
        panel = await ptero_panel_link(vps)
        await safe_ctx_send(ctx, make_embed("🦖 Pterodactyl Ports", f"Port allocations are managed by Pterodactyl.\nPanel: {panel or 'not configured'}"))
        return
    rows = db_list_ports(vps["id"])
    body = "\n".join(f"#{r['id']} • `{r['host_port']}→{r['container_port']}/TCP` • `{r['status']}`" for r in rows) or "No forwarding rules."
    await safe_ctx_send(ctx, make_embed(f"🌐 Ports • {clean(vps['container_name'])}", body))


@prefix_ports.command(name="remove")
async def prefix_ports_remove(ctx: commands.Context, port_id: int):
    row = db_get_port(port_id)
    if not row:
        await safe_ctx_send(ctx, make_embed("❌ Port Not Found", "No such forwarding rule exists."))
        return
    vps = db_get_vps(int(row["vps_id"]))
    if not vps or not db_is_owner_or_admin(ctx.author.id, vps):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "You do not own that forwarding rule."))
        return
    await stop_port_forward(row)
    db_delete_port(port_id)
    await safe_ctx_send(ctx, make_embed("🗑️ Port Removed", f"Forwarding rule `#{port_id}` removed."))


@bot.command(name="deploy")
async def prefix_deploy(
    ctx: commands.Context,
    os_type: str | None = None,
    ram: str = DEFAULT_RAM,
    cpu: str = DEFAULT_CPU,
    disk: str = DEFAULT_DISK,
    location: str = DEFAULT_LOCATION,
):
    if not os_type:
        os_type = runtime_str("default_os")
        ram = runtime_str("default_ram")
        cpu = runtime_str("default_cpu")
        disk = runtime_str("default_disk")
        location = runtime_str("default_location")
        is_admin = ADMIN_BYPASS_LIMITS and ADMIN_ID > 0 and ctx.author.id == ADMIN_ID
        used = db_vps_count(ctx.author.id)
        limit = db_effective_slots(ctx.author.id)
        if not is_admin and used >= limit:
            await safe_ctx_send(ctx, make_embed(
                "🎟️ VPS Slots Full",
                f"You are using **{used}/{limit}** VPS slots.\n\n**SLOTS FULL** — more slots are coming soon. Ask an administrator to add slots.\n\nCurrent allocation: `{used}/{limit}`.",
            ))
            return
        await safe_ctx_send(
            ctx,
            make_embed(
                "🚀 Deploy RGNODES™ VPS",
                f"Current slots: **{slot_status_text(ctx.author.id)}**\n\nSelect the operating system first, then choose a location: Singapore 🇸🇬 or India 🇮🇳.",
            ),
            DeployView(ctx.author.id),
        )
        return

    normalized_location = normalize_location(location) or DEFAULT_LOCATION
    message = await ctx.send(
        embed=progress_embed(
            1,
            "Starting deployment",
            normalize_os(os_type) or os_type,
            normalized_location,
            ram,
            cpu,
            disk,
            "rgnodes-pending",
        )
    )

    async def edit(embed: discord.Embed):
        with contextlib.suppress(discord.HTTPException):
            await message.edit(embed=embed)

    cooldown_claimed = False
    if not (ADMIN_ID > 0 and int(ctx.author.id) == int(ADMIN_ID)):
        cooldown_claimed, remaining = db_claim_deploy_cooldown(ctx.author.id)
        if not cooldown_claimed:
            await message.edit(embed=make_embed("⏳ Deployment Cooldown", f"Please wait `{_cooldown_text(remaining)}` before deploying again."))
            return
    charged = False
    if not (ADMIN_ID > 0 and int(ctx.author.id) == int(ADMIN_ID)):
        wallet, _bank = db_balance(ctx.author.id)
        if wallet < runtime_int('deploy_cost'):
            await message.edit(embed=make_embed("💰 Insufficient Coins", f"Deploying a VPS costs **{runtime_int('deploy_cost'):,} coins**.\nYour wallet: `{wallet:,}` coins."))
            if cooldown_claimed: db_clear_deploy_cooldown(ctx.author.id)
            return
        charged = db_take_coins(ctx.author.id, runtime_int('deploy_cost'))
        if not charged:
            await message.edit(embed=make_embed("💰 Payment Failed", "Your coin balance changed before deployment. Please retry."))
            if cooldown_claimed: db_clear_deploy_cooldown(ctx.author.id)
            return
    try:
        ok, reason, vps = await asyncio.wait_for(
            create_vps(
                ctx.author,
                os_type=os_type,
                location=normalized_location,
                ram=ram,
                cpu=cpu,
                disk=disk,
                progress=edit,
            ),
            timeout=DEPLOY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        if charged: db_add_coins(ctx.author.id, runtime_int('deploy_cost'))
        ok, reason, vps = False, "Deployment timed out safely. Check the bot log before retrying.", None
    except Exception:
        logger.exception("Prefix deployment failed for user %s", ctx.author.id)
        ok, reason, vps = False, "Deployment failed safely. Check the bot log for details.", None
    if not ok or not vps:
        if charged: db_add_coins(ctx.author.id, runtime_int('deploy_cost'))
        with contextlib.suppress(discord.HTTPException):
            await message.edit(embed=make_embed("❌ VPS Creation Failed", reason))
        if cooldown_claimed: db_clear_deploy_cooldown(ctx.author.id)
        return
    view = sshx_view(vps["sshx_url"]) if vps["sshx_url"] else None
    dm_sent = False
    if vps["sshx_url"]:
        dm_sent = await safe_dm(ctx.author, console_embed(vps["container_name"], vps["sshx_url"]), view)
    network = await detect_public_network(force=True)
    ip = network.get("ip") if valid_public_ipv4(network.get("ip")) else None
    ssh_port = next((int(p["host_port"]) for p in db_list_ports(vps["id"]) if int(p["container_port"]) == 22 and str(p["protocol"]).lower() == "tcp"), None)
    await safe_dm(ctx.author, ssh_access_embed(vps, ip, ssh_port))
    final = make_embed("✅ VPS Ready", f"`{clean(vps['container_name'])}` is online.")
    final.add_field(name="🌐 Console", value="✅ Link sent by DM" if dm_sent else ("⚠️ SSHx pending — use Console to retry" if not vps["sshx_url"] else "⚠️ DM unavailable"), inline=False)
    await message.edit(embed=final, view=ManageView(vps["id"], vps["user_id"]))
    if cooldown_claimed: db_clear_deploy_cooldown(ctx.author.id)


@bot.command(name="manage")
async def prefix_manage(ctx: commands.Context, identifier: str = ""):
    if admin_ok(ctx) and ctx.message.mentions:
        target = ctx.message.mentions[0]
        vps = db_find_vps(target.id, None)
    else:
        vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", f"No matching VPS was found. Create one with `{PREFIX}deploy`."))
        return
    try:
        vps = await refresh_vps_record_state(vps)
        stats, uptime, disk, network, ports = await _dashboard_live_data(vps)
        await safe_ctx_send(
            ctx,
            dashboard_embed(vps, stats, uptime, disk, network, ports),
            ManageView(vps["id"], vps["user_id"]),
        )
    except Exception as exc:
        logger.exception("Prefix manage failed for VPS %s: %s", identifier or "latest", exc)
        await safe_ctx_send(ctx, make_embed("❌ Dashboard Error", "The VPS record exists, but its live dashboard could not be loaded. Try the command again."))


@bot.command(name="console")
async def prefix_console(ctx: commands.Context, identifier: str = ""):
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No matching VPS was found."))
        return
    ok, message = await create_console_access(vps, ctx.author)
    latest = db_get_vps(vps["id"]) or vps
    view = sshx_view(latest["sshx_url"]) if latest["sshx_url"] else None
    await safe_ctx_send(ctx, make_embed("✅ Console Ready" if ok else "❌ Console Failed", message), view=view)


@bot.command(name="list")
async def prefix_list(ctx: commands.Context):
    rows = db_get_all_vps() if (ctx.author.id == ADMIN_ID and ADMIN_ID > 0) else db_get_user_vps(ctx.author.id); embed = make_embed("📋 Your RGNODES™ VPS")
    if not rows:
        embed.description = "You do not have any VPS instances."
    else:
        embed.add_field(name="🎟️ VPS Slots", value=slot_status_text(ctx.author.id), inline=False)
    for row in rows[:25]:
        embed.add_field(
            name=f"{status_text(row['status'], bool(row['suspended']))} {clean(row['container_name'])}",
            value=f"ID: `{row['id']}` • {os_label(row['os_type'])} • {location_label(row['location'])}",
            inline=False,
        )
    await safe_ctx_send(ctx, embed)



@bot.command(name="admin-remove", aliases=["admin_rm"])
async def prefix_admin_remove(ctx: commands.Context, target: discord.User | None = None):
    if not admin_ok(ctx):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Administrator access is required."))
        return
    if target is None:
        await safe_ctx_send(ctx, make_embed("❌ User Required", f"Usage: `{PREFIX}admin-remove @user`"))
        return
    rows = db_get_user_vps(target.id)
    removed = 0
    failed = 0
    for vps in rows:
        try:
            ok, _ = await lifecycle_action(vps, "delete")
            removed += int(ok)
            failed += int(not ok)
        except Exception:
            failed += 1
            logger.exception("prefix admin-remove failed for user %s VPS #%s", target.id, vps["id"])
    await safe_ctx_send(ctx, make_embed("🗑️ Admin Remove Complete", f"User: <@{target.id}>\nRemoved: `{removed}`\nFailed: `{failed}`"))

@bot.command(name="remove")
async def prefix_remove(ctx: commands.Context, identifier: str = ""):
    if not identifier:
        await safe_ctx_send(ctx, make_embed("⚠️ VPS ID Required", f"Use `{PREFIX}remove <vps#>` to remove exactly one VPS."))
        return
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No matching VPS was found."))
        return
    if int(ctx.author.id) not in {int(vps["user_id"]), int(ADMIN_ID)} and db_share_access_level(vps["id"], ctx.author.id) != "full":
        await safe_ctx_send(ctx, make_embed("🔒 Limited Access", "Delete requires VPS ownership, admin access, or a full-access share."))
        return
    await prefix_action(ctx, identifier, "delete")


@bot.command(name="start")
async def prefix_start(ctx: commands.Context, identifier: str = ""): await prefix_action(ctx, identifier, "start")
@bot.command(name="stop")
async def prefix_stop(ctx: commands.Context, identifier: str = ""): await prefix_action(ctx, identifier, "stop")
@bot.command(name="restart")
async def prefix_restart(ctx: commands.Context, identifier: str = ""): await prefix_action(ctx, identifier, "restart")


async def prefix_action(ctx: commands.Context, identifier: str, action: str):
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No matching VPS was found."))
        return
    try:
        ok, message = await lifecycle_action(vps, action)
    except Exception:
        logger.exception("Prefix lifecycle action %s failed for VPS #%s", action, vps["id"])
        ok, message = False, "The VPS action failed safely. Check the bot log for details."
    await safe_ctx_send(ctx, make_embed("✅ Action Complete" if ok else "❌ Action Failed", message))


class HelpSelect(discord.ui.Select):
    def __init__(self, owner_id: int, admin: bool):
        options = [
            discord.SelectOption(label="User Commands", value="user", emoji="👤", description="Commands for all VPS users"),
            discord.SelectOption(label="VPS Management", value="vps", emoji="🖥️", description="Start, stop, stats and VPS access"),
            discord.SelectOption(label="Port Forwarding", value="ports", emoji="🔌", description="Network and port management"),
            discord.SelectOption(label="System Status", value="system", emoji="⚙️", description="Host monitoring and limits"),
            discord.SelectOption(label="Bot Info", value="bot", emoji="🤖", description="Bot information and status"),
        ]
        if admin:
            options.append(discord.SelectOption(label="Admin Commands", value="admin", emoji="🛡️", description="Administrator commands"))
        super().__init__(
            placeholder="Select Category",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.owner_id = owner_id
        self.admin = admin

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await safe_respond(interaction, embed=make_embed("❌ Access Denied", "This help menu belongs to another user."))
            return
        try:
            await safe_component_edit(interaction, embed=build_help_embed(self.admin, self.values[0]), view=self.view)
        except discord.NotFound:
            return
        except discord.HTTPException as exc:
            logger.warning("Help menu interaction failed: %s", safe_log(exc))
            with contextlib.suppress(Exception):
                await safe_respond(interaction, embed=make_embed("❌ Help Error", f"The menu expired. Run `{PREFIX}help` again."))
        except Exception as exc:
            logger.exception("Help menu callback failed: %s", exc)
            with contextlib.suppress(Exception):
                await safe_respond(interaction, embed=make_embed("❌ Help Error", "The help menu could not be updated."))


class HelpView(discord.ui.View):
    def __init__(self, owner_id: int, admin: bool):
        super().__init__(timeout=600)
        self.add_item(HelpSelect(owner_id, admin))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


def build_help_embed(admin: bool, category: str = "user") -> discord.Embed:
    embed = make_embed("📚 RGNODES™ Pro Command Center", "Premium command navigator • choose a category below.")
    category = category if category in {"user", "vps", "ports", "system", "bot", "admin"} else "user"

    if category == "user":
        embed.title = "📚 RGNODES™ • 👤 User Commands"
        embed.add_field(name="🚀 VPS", value=(
            f"`{PREFIX}deploy` • **155 coins** VPS deployment\n"
            f"`{PREFIX}manage` • Dashboard + control buttons\n"
            f"`{PREFIX}myvps` • List your VPS\n"
            f"`{PREFIX}share-vps-manage @user` • Manage access\n"
            f"`{PREFIX}share-vps-full @user` • Full access\n"
            f"`{PREFIX}unshare @user` • Remove share\n"
            f"`{PREFIX}trust @user` • `{PREFIX}untrust @user`"
        ), inline=False)
        embed.add_field(name="💰 Economy & Games", value=(
            f"`{PREFIX}i` • Inventory / coins / invites\n"
            f"`{PREFIX}inv2coins` • 1 invite → 10 coins\n"
            f"`{PREFIX}work` • `{PREFIX}hour` • `{PREFIX}day` • `{PREFIX}week`\n"
            f"`{PREFIX}monthe` • `{PREFIX}year` • `{PREFIX}deposit all` / `{PREFIX}dp`\n"
            f"`{PREFIX}reedim CODE` • `{PREFIX}plans` • `{PREFIX}buy-item ID`\n"
            f"`{PREFIX}coinflp` • `{PREFIX}spain` • `{PREFIX}dice` • `{PREFIX}quiz`"
        ), inline=False)
        embed.add_field(name="🤖 Utility", value=(
            f"`{PREFIX}ping` • `{PREFIX}uptime` • `{PREFIX}tps` • `{PREFIX}nodes`\n"
            f"`{PREFIX}bot-info` • `{PREFIX}help`"
        ), inline=False)
        return embed

    if category == "vps":
        embed.title = "📚 RGNODES™ • 🖥️ VPS Management"
        embed.add_field(name="⚙️ Control", value=(
            f"`{PREFIX}manage [vps#]` • `{PREFIX}start` • `{PREFIX}stop` • `{PREFIX}restart`\n"
            f"`{PREFIX}console [vps#]` • `{PREFIX}ssh-me [vps#]` • `{PREFIX}sshx [vps#]`\n"
            f"`{PREFIX}vpsinfo [vps#]` • `{PREFIX}vps-stats [vps#]` • `{PREFIX}vps-uptime [vps#]`\n"
            f"`{PREFIX}snapshot` • `{PREFIX}list-snapshots` • `{PREFIX}restore-snapshot`\n"
            f"`{PREFIX}remove <vps#>` • deletes **one VPS only**"
        ), inline=False)
        embed.add_field(name="🔐 SSH", value="`ssh root@IP -p PORT` • root password is delivered privately by DM.", inline=False)
        return embed

    if category == "ports":
        embed.title = "📚 RGNODES™ • 🔌 Network & Ports"
        embed.add_field(name="Port Forwarding", value=(
            f"`{PREFIX}ports` • List rules\n"
            f"`{PREFIX}ports add <vps#> <container-port> [host-port]`\n"
            f"`{PREFIX}ports remove <port-id>`\n"
            "Guest firewall allows: **22 • 80 • 443 • 8080 • 8443**"
        ), inline=False)
        return embed

    if category == "system":
        embed.title = "📚 RGNODES™ • ⚙️ System"
        embed.add_field(name="Monitoring", value=(
            f"`{PREFIX}serverstats` • host capacity\n"
            f"`{PREFIX}thresholds` • resource thresholds\n"
            f"`{PREFIX}status-all-vm` • all VPS status"
        ), inline=False)
        return embed

    if category == "bot":
        embed.title = "📚 RGNODES™ • 🤖 Platform"
        embed.add_field(name="Identity", value=(
            f"Hosting: `{HOSTING_NAME}`\nVersion: `{BOT_VERSION}`\n"
            "Owners: `MrZetrix` • `Zynox2`\n"
            f"Hostname: `{VPS_HOSTNAME_PREFIX}` • Health: `:{WEB_PORT}`"
        ), inline=False)
        return embed

    if category == "admin" and admin:
        embed.title = "📚 RGNODES™ • 🛡️ Admin Commands"
        embed.add_field(name="🖥️ VPS Control", value=(
            "`!create @user ram cpu disk location`\n"
            "`!manage @user` • `!suspand @user 1h` • `!unsuspand @user`\n"
            "`!rm @user [vps#]` • `!admin-remove @user` • `!rm-all confirm`\n"
            "`!re-boot` • `!re-install` • `!status-all-vm`\n"
            "`!vm-creating-ban @user` • `!reset-pass @user`"
        ), inline=False)
        embed.add_field(name="🛡️ Security / Access", value=(
            "`!anty-hacking` • scan/remove high-confidence malicious processes\n"
            "`!ssh @user` • `!sshx @user`\n"
            "`!backup-vm` • `!vm-backup @user`"
        ), inline=False)
        embed.add_field(name="💰 Economy / Nodes / DM", value=(
            "`!add-toal-slot 1000` • `!add-user-slots @user 1`\n"
            "`!add-coins @user 100` • `!rm-coins @user 100`\n"
            "`!add-plans name price active`\n"
            "`!add-reedim CODE 500 1 10` • `!rm-reedim ID`\n"
            "`!add-node name SG host 22` • `!dm @user` • `!dm-all`"
        ), inline=False)
        return embed

    return build_help_embed(admin, "user")


@bot.command(name="ping")
async def prefix_ping(ctx: commands.Context):
    latency = round(bot.latency * 1000) if bot.is_ready() else 0
    await safe_ctx_send(ctx, make_embed("🏓 Pong!", f"Discord latency: `{latency}ms`"))


@bot.command(name="about")
async def prefix_about(ctx: commands.Context):
    embed = make_embed("☁️ RGNODES™ VPS Management", "Professional RGNODES VPS management with Docker + systemd guests.")
    embed.add_field(name="🛠️ Stack", value="Python • discord.py • Docker • SQLite WAL", inline=False)
    embed.add_field(name="⚙️ Prefix", value=f"`{PREFIX}`", inline=True)
    embed.add_field(name="🖥️ Backend", value=f"`{active_backend()}`", inline=True)
    await safe_ctx_send(ctx, embed)


@bot.command(name="logs")
async def prefix_logs(ctx: commands.Context, identifier: str, lines: int = 50):
    vps = ctx_vps(ctx, identifier)
    if not vps:
        await safe_ctx_send(ctx, make_embed("❌ VPS Not Found", "No VPS matches that identifier."))
        return
    if str(vps["backend"] or "docker").lower() != "docker":
        await safe_ctx_send(ctx, make_embed("🦖 Pterodactyl Logs", "Use the Pterodactyl Panel for server logs."))
        return
    logs = (await docker_logs(vps["container_id"], lines)).replace("```", "'''")
    await safe_ctx_send(ctx, make_embed(
        f"📜 Logs • {clean(vps['container_name'])}",
        f"```text\n{logs[:3900]}\n```",
    ))


@bot.command(name="help")
async def prefix_help(ctx: commands.Context):
    admin = ctx.author.id == ADMIN_ID
    await safe_ctx_send(ctx, build_help_embed(admin, "user"), HelpView(ctx.author.id, admin))


async def safe_ctx_send(ctx: commands.Context, embed: discord.Embed, view: discord.ui.View | None = None) -> None:
    """Best-effort prefix response that never creates a second command exception."""
    try:
        kwargs: dict[str, Any] = {"embed": embed}
        if view is not None:
            kwargs["view"] = view
        await ctx.send(**kwargs)
    except discord.HTTPException as exc:
        logger.warning("Prefix response HTTP failure: %s", safe_log(exc))
    except Exception as exc:
        logger.exception("Prefix response failed: %s", exc)



# ================================================================
# Requested prefix commands: admin (!) and user (-)
# ================================================================

@bot.command(name="create")
async def prefix_admin_create(ctx: commands.Context, target: discord.User, ram: str=DEFAULT_RAM, cpu: str=DEFAULT_CPU, disk: str=DEFAULT_DISK, location: str=DEFAULT_LOCATION):
    if not admin_ok(ctx):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Administrator access is required.")); return
    msg=await ctx.send(embed=progress_embed(1,"Starting admin deployment","ubuntu-24.04",normalize_location(location) or DEFAULT_LOCATION,ram,cpu,disk,"rgnodes-pending"))
    async def edit(e):
        with contextlib.suppress(discord.HTTPException): await msg.edit(embed=e)
    try:
        ok,reason,vps=await asyncio.wait_for(create_vps(target,os_type="ubuntu-24.04",location=location,ram=ram,cpu=cpu,disk=disk,progress=edit),timeout=DEPLOY_TIMEOUT)
    except Exception as exc:
        logger.exception("Admin prefix create failed: %s",safe_log(exc)); ok=False; reason="Admin deployment failed safely."; vps=None
    if not ok or not vps:
        await msg.edit(embed=make_embed("❌ VPS Creation Failed",reason)); return
    await safe_dm(target, make_embed("✅ VPS Ready",f"Admin created **{vps['container_name']}** for you.\nVMID: `{vps['id']}`\nResources: `{vps['ram']}` RAM • `{vps['cpu']}` CPU • `{vps['disk']}` Disk."))
    await msg.edit(embed=make_embed("✅ VPS Created",f"Created VPS `{vps['container_name']}` for <@{target.id}> • ID `{vps['id']}`."))


@bot.command(name="manage-user")
async def prefix_admin_manage(ctx: commands.Context,target: discord.User,identifier: str=""):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    v=db_find_vps(target.id,identifier)
    if not v: await safe_ctx_send(ctx,make_embed("❌ VPS Not Found","No VPS found for that user.")); return
    await safe_ctx_send(ctx,make_embed("🛠️ Admin • Manage",f"User: <@{target.id}>\nVPS: `{v['container_name']}` • ID `{v['id']}`\nStatus: {status_text(v['status'],bool(v['suspended']))}"),ManageView(v['id'],v['user_id']))


@bot.command(name="suspand",aliases=["suspend"])
async def prefix_admin_suspend(ctx: commands.Context,target: discord.User,duration: str="1h"):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    try: seconds=_duration_seconds(duration)
    except ValueError as exc: await safe_ctx_send(ctx,make_embed("❌ Invalid Duration",str(exc))); return
    db_set_user_suspension(target.id,datetime.now(timezone.utc)+timedelta(seconds=seconds))
    for v in db_get_user_vps(target.id):
        with contextlib.suppress(Exception): await lifecycle_action(v,"stop")
        db_update_vps(v['container_id'],suspended=1,status='stopped')
    await safe_ctx_send(ctx,make_embed("⛔ User Suspended",f"<@{target.id}> suspended for `{duration}`."))


@bot.command(name="unsuspand",aliases=["unsuspend"])
async def prefix_admin_unsuspend(ctx: commands.Context,target: discord.User):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    db_clear_user_suspension(target.id)
    for v in db_get_user_vps(target.id): db_update_vps(v['container_id'],suspended=0)
    await safe_ctx_send(ctx,make_embed("✅ User Unsuspended",f"<@{target.id}> has been unsuspended."))


@bot.command(name="rm-all")
async def prefix_rm_all(ctx: commands.Context,confirm: str=""):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    if str(confirm).lower() not in {"confirm","yes","true","1"}:
        await safe_ctx_send(ctx,make_embed("⚠️ Confirm RM-ALL","Use `!rm-all confirm` to remove all managed VPS.")); return
    removed=0
    for v in db_get_all_vps():
        ok,_=await lifecycle_action(v,"delete"); removed+=int(ok)
    await safe_ctx_send(ctx,make_embed("🗑️ RM-ALL Complete",f"Removed `{removed}` VPS instance(s)."))


@bot.command(name="rm")
async def prefix_rm(ctx: commands.Context,target: discord.User,identifier: str=""):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    v=db_find_vps(target.id,identifier)
    if not v: await safe_ctx_send(ctx,make_embed("❌ VPS Not Found","No VPS matched.")); return
    ok,msg=await lifecycle_action(v,"delete")
    await safe_ctx_send(ctx,make_embed("✅ VPS Removed" if ok else "❌ Remove Failed",msg))


@bot.command(name="vm-creating-ban",aliases=["vm-creation-ban","vm-crmeating-ban"])
async def prefix_vm_ban(ctx: commands.Context,target: discord.User):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    db_set_ban(target.id,True); await safe_ctx_send(ctx,make_embed("🚫 VPS Creation Banned",f"<@{target.id}> cannot create VPS."))


@bot.command(name="add-total-slot",aliases=["add-toal-slot"])
async def prefix_total_slots(ctx: commands.Context,amount: int):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    if not 1<=amount<=100000: await safe_ctx_send(ctx,make_embed("❌ Invalid Limit","Use 1–100000.")); return
    db_set_total_create_limit(amount); await safe_ctx_send(ctx,make_embed("🎟️ Global VPS Limit",f"Maximum total VPS records: `{amount}`."))


@bot.command(name="reset-pass")
async def prefix_reset_pass(ctx: commands.Context,target: discord.User):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    rows=db_get_user_vps(target.id)
    if not rows: await safe_ctx_send(ctx,make_embed("❌ No VPS","Target user has no VPS.")); return
    password="".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(SSH_PASSWORD_LENGTH)); ok_count=0
    for v in rows:
        rc,_,_=await docker_exec(v['container_id'],'bash','-lc',f"printf '%s\\n' {json.dumps('root:'+password)} | chpasswd",timeout=20,retries=0)
        if rc==0: db_update_vps(v['container_id'],ssh_password=password); ok_count+=1
    await safe_dm(target,make_embed("🔐 SSH Password Reset",f"New root password: `{password}`\nKeep it private."))
    await safe_ctx_send(ctx,make_embed("✅ Password Reset",f"Updated `{ok_count}/{len(rows)}` VPS."))


@bot.command(name="anty-hacking",aliases=["anti-hacking"])
async def prefix_anty_hacking(ctx: commands.Context):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    conn=db_connect(); conn.execute("INSERT INTO security_settings(key,value) VALUES('anti_hacking','1') ON CONFLICT(key) DO UPDATE SET value='1'"); conn.close()
    scanned,suspicious,names=await security_sweep(delete_suspicious=True)
    await safe_ctx_send(ctx,make_embed("🛡️ Anti-Hacking Enabled",f"Scanned `{scanned}` • removed `{suspicious}` suspicious VPS."+(("\n"+"\n".join(names[:10])) if names else "")))


@bot.command(name="status-all-vm")
async def prefix_status_all(ctx: commands.Context):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    rows=db_get_all_vps(); embed=make_embed("🖥️ All VPS Status",f"Total `{len(rows)}` • Global limit `{db_total_create_limit()}`")
    for v in rows[:25]: embed.add_field(name=f"#{v['id']} • {clean(v['container_name'])}",value=f"<@{v['user_id']}> • {status_text(v['status'],bool(v['suspended']))}",inline=False)
    await safe_ctx_send(ctx,embed)


@bot.command(name="ssh")
async def prefix_admin_ssh(ctx: commands.Context, identifier: str=""):
    if admin_ok(ctx):
        target = ctx.message.mentions[0] if ctx.message.mentions else None
        if target is None:
            await safe_ctx_send(ctx,make_embed("❌ User Required","Usage: `!ssh @user`")); return
        v=db_find_vps(target.id,None); recipient=target
    else:
        v=ctx_vps(ctx,identifier); recipient=ctx.author
    if not v: await safe_ctx_send(ctx,make_embed("❌ VPS Not Found","No VPS found.")); return
    network=await detect_public_network(force=True); ip=network.get('ip') if valid_public_ipv4(network.get('ip')) else None
    port=next((int(r['host_port']) for r in db_list_ports(v['id']) if int(r['container_port'])==22),None)
    sent=await safe_dm(recipient,ssh_access_embed(v,ip,port))
    await safe_ctx_send(ctx,make_embed("🔐 SSH Details Sent" if sent else "⚠️ DM Unavailable",f"SSH credentials {'sent to' if sent else 'could not be sent to'} <@{recipient.id}> by DM."))


@bot.command(name="sshx")
async def prefix_admin_sshx(ctx: commands.Context, identifier: str=""):
    if admin_ok(ctx):
        target = ctx.message.mentions[0] if ctx.message.mentions else None
        if target is None:
            await safe_ctx_send(ctx,make_embed("❌ User Required","Usage: `!sshx @user`")); return
        v=db_find_vps(target.id,None); recipient=target
    else:
        v=ctx_vps(ctx,identifier); recipient=ctx.author
    if not v: await safe_ctx_send(ctx,make_embed("❌ VPS Not Found","No VPS found.")); return
    ok,msg=await create_console_access(v,recipient)
    await safe_ctx_send(ctx,make_embed("✅ SSHx Ready" if ok else "❌ SSHx Failed",msg))


@bot.command(name="dm-all")
async def prefix_dm_all(ctx: commands.Context):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    members=list(ctx.guild.members) if ctx.guild else []
    await safe_ctx_send(ctx,make_embed("📨 Mass DM","Press **Write Message** to open the message box. Emojis are supported."),MassDMView(members))


@bot.command(name="dm")
async def prefix_dm(ctx: commands.Context,target: discord.User):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    await safe_ctx_send(ctx,make_embed("📨 Direct DM",f"Press **Write Message** to message <@{target.id}>."),SingleDMView(target))


@bot.command(name="add-coins", aliases=["add_coin"])
async def prefix_add_coins(ctx: commands.Context,target: discord.User,amount: int):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    if amount<=0: await safe_ctx_send(ctx,make_embed("❌ Invalid Amount","Amount must be positive.")); return
    db_add_coins(target.id,amount); await safe_ctx_send(ctx,make_embed("💰 Coins Added",f"Added `{amount:,}` coins to <@{target.id}>."))


@bot.command(name="rm-coins", aliases=["remove-coins"])
async def prefix_rm_coins(ctx: commands.Context,target: discord.User,amount: int):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    ok=db_take_coins(target.id,amount); await safe_ctx_send(ctx,make_embed("🗑️ Coins Removed" if ok else "❌ Insufficient Coins",f"Amount: `{amount:,}`"))


@bot.command(name="add-user-slots")
async def prefix_add_user_slots(ctx: commands.Context,target: discord.User,amount: int):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    db_upsert_user(target.id,str(target)); total=db_add_slots(target.id,amount); await safe_ctx_send(ctx,make_embed("🎟️ Slots Added",f"<@{target.id}> now has `{total}` slots."))


@bot.command(name="edit-plan", aliases=["plan-edit"])
async def prefix_edit_plan(ctx: commands.Context, plan_id: int, name: str="", price: str="", ram: str="", cpu: str="", disk: str="", status: str=""):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    conn=db_connect()
    try: row=conn.execute("SELECT * FROM plans WHERE id=?",(int(plan_id),)).fetchone()
    finally: conn.close()
    if not row: await safe_ctx_send(ctx,make_embed("❌ Plan Not Found","That plan does not exist.")); return
    try:
        new_price=int(price) if price else int(row['price'])
        new_name=name.strip() or str(row['name']); new_ram=ram or str(row['ram']); new_cpu=cpu or str(row['cpu']); new_disk=disk or str(row['disk']); new_status=status.strip().lower() or str(row['status'])
        if new_price<0 or new_status not in {"active","disabled"}: raise ValueError("Invalid price/status.")
        new_ram,new_cpu,new_disk=validate_resources(new_ram,new_cpu,new_disk)
    except ValueError as exc: await safe_ctx_send(ctx,make_embed("❌ Invalid Plan",str(exc))); return
    conn=db_connect()
    try: conn.execute("UPDATE plans SET name=?,price=?,status=?,ram=?,cpu=?,disk=? WHERE id=?",(new_name[:80],new_price,new_status,new_ram,new_cpu,new_disk,int(plan_id)))
    except sqlite3.IntegrityError: await safe_ctx_send(ctx,make_embed("⚠️ Plan Name Exists","Another plan already uses that name.")); return
    finally: conn.close()
    await safe_ctx_send(ctx,make_embed("✅ Plan Updated",f"Plan `#{plan_id}` • `{clean(new_name)}` • `{new_price:,}` coins"))


@bot.command(name="add-plans", aliases=["add-plan"])
async def prefix_add_plans(ctx: commands.Context,*args: str):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    if len(args)==1 and "," in args[0]:
        args=tuple(x.strip() for x in args[0].split(",") if x.strip())
    if len(args)<3:
        await safe_ctx_send(ctx,make_embed("❌ Usage", "`!add-plans name,price,status` or `!add-plans name price status [ram cpu disk]`")); return
    name,price,status=args[0],int(args[1]),args[2]; ram=args[3] if len(args)>3 else DEFAULT_RAM; cpu=args[4] if len(args)>4 else DEFAULT_CPU; disk=args[5] if len(args)>5 else DEFAULT_DISK
    ram,cpu,disk=validate_resources(ram,cpu,disk); conn=db_connect()
    try: cur=conn.execute("INSERT INTO plans(name,price,status,ram,cpu,disk,created_at) VALUES(?,?,?,?,?,?,?)",(name,price,status,ram,cpu,disk,utc_now()))
    except sqlite3.IntegrityError: await safe_ctx_send(ctx,make_embed("⚠️ Plan Exists","That plan name already exists.")); return
    finally: conn.close()
    await safe_ctx_send(ctx,make_embed("✅ Plan Added",f"ID `{cur.lastrowid}` • `{name}` • `{price:,}` coins"))


@bot.command(name="add-node")
async def prefix_add_node(ctx: commands.Context,name: str,location: str,host: str,port: int=22):
    if not admin_ok(ctx): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    conn=db_connect()
    try: conn.execute("INSERT INTO nodes(name,location,host,port,status,created_at) VALUES(?,?,?,?,?,?)",(name,normalize_location(location) or 'SG',host,port,'online',utc_now()))
    except sqlite3.IntegrityError: await safe_ctx_send(ctx,make_embed("⚠️ Node Exists","That node already exists.")); return
    finally: conn.close()
    await safe_ctx_send(ctx,make_embed("✅ Node Added",f"`{name}` • `{host}:{port}`"))


@bot.command(name="balance", aliases=["bal"])
async def prefix_balance(ctx: commands.Context):
    await safe_ctx_send(ctx, economy_embed(ctx.author.id, "💰 RGNODES™ • Balance"))

@bot.command(name="buy-slot", aliases=["buy-slot1", "slot-buy"])
async def prefix_buy_slot(ctx: commands.Context):
    ok, msg, balance, slots = db_purchase_slot(ctx.author.id)
    title="✅ VPS Slot Purchased" if ok else "❌ Slot Purchase Failed"
    await safe_ctx_send(ctx, make_embed(title, f"{msg}\n\n💰 Balance: `{balance:,}` coins\n🎟️ Slots: `{slots}`"))

@bot.command(name="i",aliases=["inventory"])
async def prefix_inventory(ctx: commands.Context): await safe_ctx_send(ctx,economy_embed(ctx.author.id,"🎒 RGNODES™ • Inventory"))

@bot.command(name="inv2coins")
async def prefix_inv2coins(ctx: commands.Context):
    row=db_economy(ctx.author.id); invites=int(row['invites'])
    if invites<=0: await safe_ctx_send(ctx,make_embed("🎟️ No Invites","You have no tracked invites to convert.")); return
    db_set_invites(ctx.author.id,0); db_add_coins(ctx.author.id,invites*runtime_int('invite_rate'))
    await safe_ctx_send(ctx,make_embed("💰 Invites Converted",f"Converted `{invites}` invites → `{invites*runtime_int('invite_rate')}` coins."))


async def _claim_reward(ctx: commands.Context,action: str,seconds: int,low: int,high: int):
    uid=int(ctx.author.id)
    conn=db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now_dt=datetime.now(timezone.utc)
        row=conn.execute("SELECT next_at FROM economy_cooldowns WHERE user_id=? AND action=?",(uid,action)).fetchone()
        if row:
            try:
                next_at=datetime.fromisoformat(str(row[0]))
                remaining=max(0,int((next_at-now_dt).total_seconds()))
            except (TypeError,ValueError):
                remaining=0
            if remaining:
                conn.rollback()
                await safe_ctx_send(ctx,make_embed("⏳ Cooldown",f"Try again in `{_cooldown_text(remaining)}`.")); return
        low, high = sorted((int(low), int(high)))
        reward=random.randint(low, high)
        when=(now_dt+timedelta(seconds=max(0,int(seconds)))).isoformat()
        conn.execute("INSERT OR IGNORE INTO economy(user_id,wallet,bank,invites,updated_at) VALUES(?,?,?,?,?)",(uid,0,0,0,now_dt.isoformat()))
        conn.execute("UPDATE economy SET wallet=wallet+?,updated_at=? WHERE user_id=?",(reward,now_dt.isoformat(),uid))
        conn.execute("INSERT INTO economy_cooldowns(user_id,action,next_at) VALUES(?,?,?) ON CONFLICT(user_id,action) DO UPDATE SET next_at=excluded.next_at",(uid,action,when))
        conn.commit()
    except Exception:
        with contextlib.suppress(Exception): conn.rollback()
        raise
    finally:
        conn.close()
    await safe_ctx_send(ctx,make_embed("💰 Reward Claimed",f"You earned **{reward:,} coins** from `{action}`."))


@bot.command(name="work")
async def prefix_work(ctx): await _claim_reward(ctx,"work",3600,runtime_int("reward_work_min"),runtime_int("reward_work_max"))
@bot.command(name="hour")
async def prefix_hour(ctx): await _claim_reward(ctx,"hour",3600,runtime_int("reward_hour"),runtime_int("reward_hour"))
@bot.command(name="day")
async def prefix_day(ctx): await _claim_reward(ctx,"day",86400,runtime_int("reward_day"),runtime_int("reward_day"))
@bot.command(name="week")
async def prefix_week(ctx): await _claim_reward(ctx,"week",604800,runtime_int("reward_week"),runtime_int("reward_week"))
@bot.command(name="monthe",aliases=["month"])
async def prefix_month(ctx): await _claim_reward(ctx,"month",2592000,runtime_int("reward_month"),runtime_int("reward_month"))
@bot.command(name="year")
async def prefix_year(ctx): await _claim_reward(ctx,"year",31536000,runtime_int("reward_year"),runtime_int("reward_year"))


@bot.command(name="coinflp",aliases=["coinflip"])
async def prefix_coinflip(ctx: commands.Context,amount: int=10,guess: str="heads"):
    if amount<=0 or amount>100000: await safe_ctx_send(ctx,make_embed("❌ Invalid Bet","Bet 1–100000 coins.")); return
    if not db_take_coins(ctx.author.id,amount): await safe_ctx_send(ctx,make_embed("💰 Insufficient Coins","Not enough wallet coins.")); return
    result=random.choice(("heads","tails")); won=result==guess.lower().strip()
    payout=amount*2 if won else 0
    if payout: db_add_coins(ctx.author.id,payout)
    await safe_ctx_send(ctx,make_embed("🪙 Coin Flip",f"Result: **{result}**\nYour guess: **{guess}**\n"+(f"✅ Won `{payout:,}` coins." if won else f"❌ Lost `{amount:,}` coins.")))


@bot.command(name="spain",aliases=["spin"])
async def prefix_spin(ctx: commands.Context,amount: int=10):
    if amount<=0 or amount>100000: await safe_ctx_send(ctx,make_embed("❌ Invalid Bet","Bet 1–100000 coins.")); return
    if not db_take_coins(ctx.author.id,amount): await safe_ctx_send(ctx,make_embed("💰 Insufficient Coins","Not enough wallet coins.")); return
    roll=random.randint(1,100); mult=5 if roll==100 else 3 if roll>=95 else 0; payout=amount*mult
    if payout: db_add_coins(ctx.author.id,payout)
    await safe_ctx_send(ctx,make_embed("🎰 Spin",f"Roll: `{roll}`\n"+(f"🎉 Payout: `{payout:,}` coins." if payout else f"No payout. Lost `{amount:,}` coins.")))


@bot.command(name="deposit",aliases=["dp"])
async def prefix_deposit(ctx: commands.Context,amount: str):
    conn=db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT wallet,bank FROM economy WHERE user_id=?",(int(ctx.author.id),)).fetchone()
        wallet=int(row[0]) if row else 0; bank=int(row[1]) if row else 0
        value=wallet if amount.lower().strip()=="all" else int(amount)
        if value<=0 or value>wallet:
            conn.rollback(); await safe_ctx_send(ctx,make_embed("🏦 Deposit Failed","Invalid amount or insufficient wallet coins.")); return
        now=utc_now()
        conn.execute("UPDATE economy SET wallet=wallet-?,bank=bank+?,updated_at=? WHERE user_id=?",(value,value,now,int(ctx.author.id)))
        conn.commit()
    except ValueError:
        with contextlib.suppress(Exception): conn.rollback()
        await safe_ctx_send(ctx,make_embed("🏦 Deposit Failed","Amount must be a positive number or `all`.")); return
    except Exception:
        with contextlib.suppress(Exception): conn.rollback()
        raise
    finally: conn.close()
    await safe_ctx_send(ctx,make_embed("🏦 Deposit Complete",f"Deposited `{value:,}` coins. Bank: `{bank+value:,}`."))


@bot.command(name="redeem",aliases=["reedim"])
async def prefix_redeem(ctx: commands.Context,code: str):
    code=code.strip().upper(); conn=db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM redeem_codes WHERE code=? AND status='active'",(code,)).fetchone()
        if not row or int(row['uses'])>=int(row['max_uses']):
            conn.rollback(); await safe_ctx_send(ctx,make_embed("❌ Redeem Failed","Invalid or exhausted code.")); return
        if conn.execute("SELECT 1 FROM redeem_claims WHERE code_id=? AND user_id=?",(int(row['id']),int(ctx.author.id))).fetchone():
            conn.rollback(); await safe_ctx_send(ctx,make_embed("❌ Redeem Failed","You have already used this code.")); return
        slots_reward=int(row['reward_slots']); coins_reward=int(row['reward_coins'])
        slots_row=conn.execute("SELECT slots FROM user_slots WHERE user_id=?",(int(ctx.author.id),)).fetchone()
        current=int(slots_row[0]) if slots_row else max(1,runtime_int("server_limit"))
        max_slots=runtime_int("max_user_slots")
        if slots_reward and current + slots_reward > max_slots:
            conn.rollback(); await safe_ctx_send(ctx,make_embed("❌ Redeem Failed",f"This code grants `{slots_reward}` slots, but your maximum is `{max_slots}`.")); return
        now=utc_now()
        conn.execute("INSERT INTO redeem_claims(code_id,user_id,claimed_at) VALUES(?,?,?)",(int(row['id']),int(ctx.author.id),now))
        conn.execute("UPDATE redeem_codes SET uses=uses+1,status=CASE WHEN uses+1>=max_uses THEN 'used' ELSE status END WHERE id=?",(int(row['id']),))
        conn.execute("INSERT OR IGNORE INTO economy(user_id,wallet,bank,invites,updated_at) VALUES(?,?,?,?,?)",(int(ctx.author.id),0,0,0,now))
        conn.execute("UPDATE economy SET wallet=wallet+?,updated_at=? WHERE user_id=?",(coins_reward,now,int(ctx.author.id)))
        if slots_reward:
            conn.execute("INSERT INTO user_slots(user_id,slots,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET slots=excluded.slots,updated_at=excluded.updated_at",(int(ctx.author.id),current+slots_reward,now))
        conn.commit()
    except Exception:
        with contextlib.suppress(Exception): conn.rollback()
        raise
    finally: conn.close()
    await safe_ctx_send(ctx,make_embed("🎁 Redeem Successful",f"Code `{code}` → `{coins_reward:,}` coins • `{slots_reward}` VPS slots."))


@bot.command(name="plans-legacy")
async def prefix_plans_legacy(ctx: commands.Context):
    rows=plan_rows(); embed=make_embed("🛒 RGNODES™ • Plans")
    if not rows: embed.description="No plans are currently available." 
    for r in rows[:20]: embed.add_field(name=f"#{r['id']} • {clean(r['name'])}",value=f"💰 `{r['price']:,}` coins • {clean(r['status'])}\n🖥️ `{r['ram']}` RAM • `{r['cpu']}` CPU • `{r['disk']}` disk",inline=False)
    await safe_ctx_send(ctx,embed)


@bot.command(name="buy-item")
async def prefix_buy_item(ctx: commands.Context, plan_id: int):
    conn=db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM plans WHERE id=? AND status='active'",(int(plan_id),)).fetchone()
        if not row:
            conn.rollback(); await safe_ctx_send(ctx,make_embed("❌ Plan Not Found","That plan is not active.")); return
        price=int(row["price"])
        econ=conn.execute("SELECT wallet FROM economy WHERE user_id=?",(int(ctx.author.id),)).fetchone()
        wallet=int(econ[0]) if econ else 0
        if wallet < price:
            conn.rollback(); await safe_ctx_send(ctx,make_embed("💰 Insufficient Coins",f"Need `{price:,}` wallet coins; you have `{wallet:,}`.")); return
        slots_row=conn.execute("SELECT slots FROM user_slots WHERE user_id=?",(int(ctx.author.id),)).fetchone()
        current=int(slots_row[0]) if slots_row else max(1,runtime_int("server_limit"))
        max_slots=runtime_int("max_user_slots")
        if current >= max_slots:
            conn.rollback(); await safe_ctx_send(ctx,make_embed("🎟️ Slot Limit Reached",f"Maximum `{max_slots}` slots allowed.")); return
        now=utc_now()
        conn.execute("UPDATE economy SET wallet=wallet-?,updated_at=? WHERE user_id=?",(price,now,int(ctx.author.id)))
        conn.execute("INSERT INTO user_slots(user_id,slots,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET slots=excluded.slots,updated_at=excluded.updated_at",(int(ctx.author.id),current+1,now))
        conn.execute("INSERT INTO plan_purchases(user_id,plan_id,created_at) VALUES(?,?,?)",(int(ctx.author.id),int(plan_id),now))
        conn.commit()
    except Exception:
        with contextlib.suppress(Exception): conn.rollback()
        raise
    finally: conn.close()
    await safe_ctx_send(ctx,make_embed("✅ Plan Purchased",f"**{clean(row['name'])}** purchased for `{price:,}` coins.\nResource tier: `{row['ram']}` RAM • `{row['cpu']}` CPU • `{row['disk']}` disk.\n🎟️ +1 VPS slot granted."))


@bot.command(name="bot-info")
async def prefix_bot_info(ctx: commands.Context):
    embed=make_embed("🤖 RGNODES™ • Pro Information","Professional VPS management and economy system.")
    embed.add_field(name="👑 Owners",value="MrZetrix • Zynox2",inline=True)
    embed.add_field(name="🏷️ Hosting",value=f"`{HOSTING_NAME}` • `Bot v{BOT_VERSION}`",inline=True)
    embed.add_field(name="🖥️ Hostname",value=f"`{VPS_HOSTNAME_PREFIX}`",inline=True)
    embed.add_field(name="🌐 Web",value=f"`:{WEB_PORT}`",inline=True)
    embed.add_field(name="💰 Deploy Cost",value=f"`{runtime_int('deploy_cost'):,}` coins",inline=True)
    embed.add_field(name="🎟️ Global VPS Limit",value=f"`{db_total_create_limit():,}`",inline=True)
    embed.add_field(name="⚙️ Backend",value="Docker + systemd guest",inline=True)
    await safe_ctx_send(ctx,embed)


@bot.command(name="tps")
async def prefix_tps(ctx: commands.Context):
    active=sum(1 for r in db_get_all_vps() if r['status']=='running' and not r['suspended'])
    await safe_ctx_send(ctx,make_embed("📈 RGNODES™ • TPS",f"Tracked running VPS: `{active}`\nDocker guest metrics are available from `{PREFIX}manage`."))


@bot.command(name="nodes")
async def prefix_nodes(ctx: commands.Context):
    conn=db_connect()
    try: rows=conn.execute("SELECT * FROM nodes ORDER BY id").fetchall()
    finally: conn.close()
    embed=make_embed("🌍 RGNODES™ • Nodes",f"Configured nodes: `{len(rows)}`")
    for r in rows[:25]: embed.add_field(name=f"#{r['id']} • {clean(r['name'])}",value=f"{location_label(r['location'])} • `{r['host']}:{r['port']}` • `{r['status']}`",inline=False)
    await safe_ctx_send(ctx,embed)


@bot.command(name="ssh-me")
async def prefix_ssh_user(ctx: commands.Context,identifier: str=""):
    v=ctx_vps(ctx,identifier)
    if not v: await safe_ctx_send(ctx,make_embed("❌ VPS Not Found","No VPS found.")); return
    network=await detect_public_network(force=True); ip=network.get('ip') if valid_public_ipv4(network.get('ip')) else None
    port=next((int(r['host_port']) for r in db_list_ports(v['id']) if int(r['container_port'])==22),None)
    await safe_dm(ctx.author,ssh_access_embed(v,ip,port)); await safe_ctx_send(ctx,make_embed("🔐 SSH Sent", "SSH credentials were sent to your DM."))


@bot.command(name="share-vps-manage")
async def prefix_share_manage(ctx: commands.Context,target: discord.User,identifier: str=""):
    v=ctx_vps(ctx,identifier)
    if not v or not db_is_owner_or_admin(ctx.author.id,v): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Only the owner/admin can share this VPS.")); return
    ok,msg=db_share_vps(v['id'],target.id,ctx.author.id,'manage'); await safe_ctx_send(ctx,make_embed("✅ Access Granted" if ok else "⚠️ Share Failed",msg))


@bot.command(name="share-vps-full")
async def prefix_share_full(ctx: commands.Context,target: discord.User,identifier: str=""):
    v=ctx_vps(ctx,identifier)
    if not v or not db_is_owner_or_admin(ctx.author.id,v): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Only the owner/admin can share this VPS.")); return
    ok,msg=db_share_vps(v['id'],target.id,ctx.author.id,'full'); await safe_ctx_send(ctx,make_embed("✅ Full Access Granted" if ok else "⚠️ Share Failed",msg))


@bot.command(name="unshare")
async def prefix_unshare(ctx: commands.Context,target: discord.User,identifier: str=""):
    v=ctx_vps(ctx,identifier)
    if not v or not db_is_owner_or_admin(ctx.author.id,v): await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Only the owner/admin can unshare this VPS.")); return
    ok,msg=db_unshare_vps(v['id'],target.id); await safe_ctx_send(ctx,make_embed("✅ Access Removed" if ok else "⚠️ Nothing Changed",msg))



@bot.command(name="re-boot")
async def prefix_reboot_all(ctx: commands.Context):
    if not admin_ok(ctx):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Administrator access is required.")); return
    scanned, suspicious, names = await security_sweep(delete_suspicious=True)
    restarted = 0
    for v in db_get_all_vps():
        if str(v["status"]).lower() == "running" and not v["suspended"]:
            ok, _ = await lifecycle_action(v, "restart")
            restarted += int(ok)
    await safe_ctx_send(ctx, make_embed("🔄 Re-Boot Complete", f"Scanned `{scanned}` • removed `{suspicious}` • restarted `{restarted}`." + (("\n" + "\n".join(names[:10])) if names else "")))


@bot.command(name="re-install")
async def prefix_reinstall_all(ctx: commands.Context):
    if not admin_ok(ctx):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Administrator access is required.")); return
    success = failed = 0
    for v in db_get_all_vps():
        try:
            ok, _ = await docker_reinstall_vps(v, str(v["os_type"]))
            success += int(ok); failed += int(not ok)
        except Exception as exc:
            failed += 1; logger.warning("re-install VPS #%s failed: %s", v["id"], safe_log(exc))
    await safe_ctx_send(ctx, make_embed("♻️ Re-Install Complete", f"Success: `{success}` • Failed: `{failed}`."))


@bot.command(name="backup-vm")
async def prefix_backup_vm(ctx: commands.Context):
    if not admin_ok(ctx):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Administrator access is required.")); return
    archive = Path(tempfile.gettempdir()) / f"rgnodes-backup-{int(time.time())}.zip"
    rows = [r for r in db_get_all_vps() if not int(r["critical"] or 0)]
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("metadata.json", json.dumps([{k:("<redacted>" if k=="ssh_password" else r[k]) for k in r.keys()} for r in rows], indent=2, default=str))
            for r in rows:
                z.writestr(f"logs/vps-{r['id']}.txt", (await docker_logs(r["container_id"], 80))[:100000])
        sent = await safe_dm_file(ctx.author, make_embed("💾 RGNODES™ • VPS Backup", "Critical VPS records are excluded and passwords are redacted."), str(archive))
        await safe_ctx_send(ctx, make_embed("✅ Backup Complete" if sent else "⚠️ Backup Created", f"VPS included: `{len(rows)}` • DM: `{'sent' if sent else 'unavailable'}`."))
    finally:
        with contextlib.suppress(OSError): archive.unlink()


@bot.command(name="vm-backup")
async def prefix_vm_backup(ctx: commands.Context, target: discord.User):
    if not admin_ok(ctx):
        await safe_ctx_send(ctx, make_embed("❌ Permission Denied", "Administrator access is required.")); return
    rows=[r for r in db_get_user_vps(target.id) if not int(r["critical"] or 0)]
    embed=make_embed("💾 User VPS Backup",f"Target: <@{target.id}> • VPS: `{len(rows)}`")
    for r in rows[:25]: embed.add_field(name=f"#{r['id']} • {clean(r['container_name'])}",value=f"{os_label(r['os_type'])} • {r['ram']} RAM • {r['cpu']} CPU • {r['disk']}",inline=False)
    await safe_ctx_send(ctx,embed)


@bot.command(name="add-reedim", aliases=["add-redeem"])
async def prefix_add_redeem(ctx: commands.Context, code: str, reward_coins: int=0, reward_slots: int=0, max_uses: int=1):
    if not admin_ok(ctx):
        await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    conn=db_connect()
    try:
        cur=conn.execute("INSERT INTO redeem_codes(code,reward_coins,reward_slots,max_uses,uses,status,created_at) VALUES(?,?,?,?,?,?,?)",(code.upper(),max(0,reward_coins),max(0,reward_slots),max(1,max_uses),0,"active",utc_now()))
    except sqlite3.IntegrityError:
        await safe_ctx_send(ctx,make_embed("⚠️ Code Exists","That redeem code already exists.")); return
    finally: conn.close()
    await safe_ctx_send(ctx,make_embed("🎁 Redeem Added",f"ID `{cur.lastrowid}` • `{code.upper()}` • `{reward_coins:,}` coins • `{reward_slots}` slots."))


@bot.command(name="rm-reedim", aliases=["rm-redeem"])
async def prefix_rm_redeem(ctx: commands.Context, code_id: int):
    if not admin_ok(ctx):
        await safe_ctx_send(ctx,make_embed("❌ Permission Denied","Administrator access is required.")); return
    conn=db_connect()
    try: cur=conn.execute("DELETE FROM redeem_codes WHERE id=?",(int(code_id),))
    finally: conn.close()
    await safe_ctx_send(ctx,make_embed("🗑️ Redeem Removed" if cur.rowcount else "❌ Redeem Not Found",f"ID `{code_id}`"))


@bot.command(name="trust")
async def prefix_trust(ctx: commands.Context, target: discord.User):
    conn=db_connect()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS trusted_users(user_id INTEGER NOT NULL, trusted_user_id INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(user_id,trusted_user_id))")
        conn.execute("INSERT OR IGNORE INTO trusted_users(user_id,trusted_user_id,created_at) VALUES(?,?,?)",(ctx.author.id,target.id,utc_now()))
    finally: conn.close()
    await safe_ctx_send(ctx,make_embed("🤝 Trusted User",f"<@{target.id}> is now trusted by <@{ctx.author.id}>."))


@bot.command(name="untrust")
async def prefix_untrust(ctx: commands.Context, target: discord.User):
    conn=db_connect()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS trusted_users(user_id INTEGER NOT NULL, trusted_user_id INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(user_id,trusted_user_id))")
        conn.execute("DELETE FROM trusted_users WHERE user_id=? AND trusted_user_id=?",(ctx.author.id,target.id))
    finally: conn.close()
    await safe_ctx_send(ctx,make_embed("🚫 Trust Removed",f"<@{target.id}> is no longer trusted."))


@bot.command(name="dice")
async def prefix_dice(ctx: commands.Context):
    await _claim_reward(ctx,"dice",300,10,35)


@bot.command(name="quiz")
async def prefix_quiz(ctx: commands.Context):
    questions=(("What is the default SSH port?","22"),("What does HTTP stand for?","hypertext transfer protocol"),("What command lists Docker containers?","docker ps"))
    question,answer=random.choice(questions)
    await safe_ctx_send(ctx,make_embed("🧠 RGNODES™ • Mini Quiz",f"**Question:** {question}\nReply with the answer within 20 seconds to earn `{runtime_int('quiz_reward')}` coins."))
    def check(m): return m.author.id==ctx.author.id and m.channel.id==ctx.channel.id
    try: msg=await bot.wait_for("message",check=check,timeout=20)
    except asyncio.TimeoutError: await safe_ctx_send(ctx,make_embed("⏳ Quiz Expired","Time's up.")); return
    if msg.content.strip().lower()==answer: db_add_coins(ctx.author.id,runtime_int('quiz_reward')); await safe_ctx_send(ctx,make_embed("✅ Correct", f"+{runtime_int('quiz_reward')} coins"))
    else: await safe_ctx_send(ctx,make_embed("❌ Incorrect",f"Correct answer: `{answer}`"))

# ================================================================
# Background sync / startup recovery
# ================================================================

STATUS_SEMAPHORE = asyncio.Semaphore(STATUS_CONCURRENCY)


async def sync_one(row: sqlite3.Row) -> None:
    async with STATUS_SEMAPHORE:
        try:
            backend = str(row["backend"] or "docker").lower()
            if backend == "pterodactyl":
                state = await ptero_status(row)
                if state in {"running", "starting", "restarting", "stopped"}:
                    db_update_vps(row["container_id"], status=state, sshx_url=await ptero_panel_link(row), sshx_pid=None)
                return

            state = await docker_state(row["container_id"])
            if state == "running":
                if row["status"] != "running":
                    db_update_vps(row["container_id"], status="running")
                await supervise_vps_ports(row)
            elif state in {"exited", "created", "dead", "paused", "restarting", "removing"}:
                if row["status"] != "stopped" or row["sshx_url"] or row["sshx_pid"]:
                    db_update_vps(row["container_id"], status="stopped", sshx_url=None, sshx_pid=None)
                for p_row in db_list_ports(row["id"]):
                    await stop_port_forward(p_row)
            elif state is None:
                logger.debug("Docker inspect unavailable for VPS #%s; retaining current database status.", row["id"])
        except Exception as exc:
            logger.warning("Status sync failed for #%s: %s", row["id"], safe_log(exc))


@tasks.loop(seconds=STATUS_INTERVAL)
async def sync_statuses() -> None:
    rows = db_get_all_vps()
    if rows:
        await asyncio.gather(*(sync_one(row) for row in rows), return_exceptions=True)


@sync_statuses.before_loop
async def before_sync_statuses(): await bot.wait_until_ready()


@tasks.loop(seconds=60)
async def update_presence():
    try:
        if not bot.is_ready():
            return
        docker_ok, docker_active = await docker_running_count()
        if not docker_ok:
            docker_active = db_running_count()
        ptero_active = sum(1 for row in db_get_all_vps() if str(row["backend"] or "docker").lower() == "pterodactyl" and str(row["status"]).lower() in {"running", "starting", "restarting"} and not row["suspended"])
        active = docker_active + ptero_active
        await bot.change_presence(activity=discord.Game(name=f"{BOT_STATUS_NAME} • {active} Active ⚡"))
    except (discord.HTTPException, discord.GatewayNotFound, discord.ClientException, asyncio.CancelledError):
        if isinstance(sys.exc_info()[1], asyncio.CancelledError):
            raise
    except Exception as exc:
        logger.debug("Presence update skipped: %s", safe_log(exc))


@update_presence.before_loop
async def before_update_presence(): await bot.wait_until_ready()


@tasks.loop(seconds=PORT_SUPERVISOR_INTERVAL)
async def supervise_all_ports_loop():
    try:
        await asyncio.gather(*(supervise_vps_ports(vps) for vps in db_get_all_vps()), return_exceptions=True)
    except Exception as exc:
        logger.warning("Port supervisor loop error: %s", safe_log(exc))


@supervise_all_ports_loop.before_loop
async def before_supervise_all_ports_loop(): await bot.wait_until_ready()


@tasks.loop(seconds=REAL_LOCATION_REFRESH)
async def refresh_network_identity():
    try:
        await detect_public_network(force=True)
    except Exception as exc:
        logger.debug("Network identity refresh skipped: %s", safe_log(exc))


@refresh_network_identity.before_loop
async def before_refresh_network_identity(): await bot.wait_until_ready()



INVITE_CACHE: dict[int, dict[str, tuple[int,int|None]]] = {}
INVITE_LOCK = asyncio.Lock()

async def refresh_invites(guild: discord.Guild) -> None:
    try:
        invites = await guild.invites()
        INVITE_CACHE[guild.id] = {str(i.code):(int(i.uses or 0), int(i.inviter.id) if i.inviter else None) for i in invites}
    except (discord.Forbidden, discord.HTTPException):
        INVITE_CACHE.setdefault(guild.id,{})


@bot.event
async def on_member_join(member: discord.Member):
    async with INVITE_LOCK:
        before = INVITE_CACHE.get(member.guild.id,{})
        try:
            invites = await member.guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return
        after = {str(i.code):(int(i.uses or 0), int(i.inviter.id) if i.inviter else None) for i in invites}
        INVITE_CACHE[member.guild.id]=after
        inviter_id=None
        for code,(uses,inviter) in after.items():
            old_uses=before.get(code,(0,None))[0]
            if uses>old_uses:
                inviter_id=inviter; break
        if inviter_id and inviter_id != member.id:
            row=db_economy(inviter_id); db_set_invites(inviter_id,int(row['invites'])+1)


@bot.event
async def on_ready():
    logger.info("RGNODES™ online as %s", bot.user)
    if not bot.loops_started:
        if not sync_statuses.is_running():
            sync_statuses.start()
        if not update_presence.is_running():
            update_presence.start()
        if not supervise_all_ports_loop.is_running():
            supervise_all_ports_loop.start()
        if not refresh_network_identity.is_running():
            refresh_network_identity.start()
        bot.loops_started = True
        await detect_public_network()
        for guild in bot.guilds:
            await refresh_invites(guild)

    if not bot.synced:
        for attempt in range(3):
            try:
                synced = await bot.tree.sync()
                bot.synced = True
                logger.info("Synced %d slash commands", len(synced))
                break
            except discord.HTTPException as exc:
                logger.warning("Slash command sync attempt %d failed: %s", attempt + 1, safe_log(exc))
                if attempt < 2:
                    await asyncio.sleep(3 * (attempt + 1))


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if getattr(ctx, "_rgnodes_duplicate_event", False):
        return
    # CommandInvokeError is the wrapper discord.py uses for exceptions raised
    # inside a command. Always log the original exception so the real cause is
    # visible instead of producing an unhelpful generic Discord message.
    original = getattr(error, "original", error)
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CommandOnCooldown):
        await safe_ctx_send(ctx, make_embed("⏳ Please Wait", f"Try again in `{error.retry_after:.1f}s`."))
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await safe_ctx_send(ctx, make_embed("❌ Missing Argument", f"Use `{PREFIX}help` to view the correct command syntax."))
        return
    if isinstance(error, (commands.BadArgument, commands.UserNotFound, commands.MemberNotFound, commands.ChannelNotFound, commands.RoleNotFound)):
        await safe_ctx_send(ctx, make_embed("❌ Invalid Argument", f"Use `{PREFIX}help` to view the correct command syntax."))
        return
    command_name = getattr(ctx.command, "qualified_name", "unknown")
    if isinstance(original, BaseException):
        logger.error(
            "Prefix command '%s' failed: %s",
            command_name,
            safe_log(original),
            exc_info=(type(original), original, original.__traceback__),
        )
    else:
        logger.error("Prefix command '%s' failed: %s", command_name, safe_log(original))
    await safe_ctx_send(ctx, make_embed("❌ Command Error", "That command hit an internal error. The failure was logged for repair."))


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.error("Slash command error: %s", safe_log(error))
    await safe_respond(
        interaction,
        embed=make_embed("❌ Command Error", "The command could not be completed safely."),
    )


async def main() -> None:
    acquire_singleton()
    # Start the health listener before Discord so hosting platforms can still
    # observe the service while Discord connectivity is temporarily unavailable.
    await start_health_server()

    if not TOKEN:
        await stop_health_server()
        raise SystemExit(
            "Discord bot token is missing. Set TOKEN=YOUR_BOT_TOKEN in .env "
            "(or DISCORD_TOKEN/BOT_TOKEN) and restart the bot."
        )

    if ADMIN_ID <= 0:
        logger.warning("ADMIN_ID is not configured; admin commands will be unavailable.")

    logger.info(
        "RGNODES starting | build=%s | token_source=%s | prefix=%r | backend=%s | quota=%s fallback=%s location=%s | locations=SG,IN",
        RGNODES_BUILD, TOKEN_SOURCE, PREFIX, active_backend(), ENABLE_HARD_DISK_QUOTA, QUOTA_FALLBACK, DEFAULT_LOCATION,
    )

    attempt = 0
    try:
        while True:
            attempt += 1
            try:
                # discord.py's reconnect=True handles normal gateway disconnects.
                # This outer retry additionally covers failures before the
                # gateway session exists, such as aiohttp TCP/TLS resets while
                # requesting GET /users/@me.
                await bot.start(TOKEN, reconnect=True)
                logger.warning("Discord client stopped cleanly; restarting login loop.")
                attempt = 0

            except discord.LoginFailure:
                # Invalid/revoked credentials will not be fixed by retrying.
                logger.error("Discord rejected the bot token (HTTP 401 / invalid credentials).")
                logger.error(
                    "Check that %s contains the current token for the correct Discord bot.",
                    TOKEN_SOURCE,
                )
                logger.error("The value should be the raw bot token, without a leading 'Bot '.")
                raise SystemExit(1) from None

            except (aiohttp.ClientConnectorError, aiohttp.ClientConnectionError,
                    aiohttp.ClientOSError, aiohttp.ServerDisconnectedError,
                    asyncio.TimeoutError, ConnectionError, ConnectionResetError,
                    BrokenPipeError, OSError) as exc:
                # These are transport-level failures. They are often caused by
                # transient host egress/DNS/TLS problems and are safe to retry.
                delay = min(
                    DISCORD_LOGIN_RETRY_MAX,
                    DISCORD_LOGIN_RETRY_BASE * (2 ** min(max(attempt - 1, 0), 6)),
                )
                logger.warning(
                    "Discord network connection failed during startup/session: %s | retry=%ss | attempt=%s",
                    safe_log(exc), delay, attempt,
                )
                if DISCORD_LOGIN_MAX_ATTEMPTS and attempt >= DISCORD_LOGIN_MAX_ATTEMPTS:
                    logger.error("Discord retry limit reached (%s attempts).", DISCORD_LOGIN_MAX_ATTEMPTS)
                    raise SystemExit(1) from None
                with contextlib.suppress(Exception):
                    await bot.close()
                await asyncio.sleep(delay)

            except discord.HTTPException as exc:
                status = getattr(exc, "status", None)
                # Retry transient HTTP failures, but do not loop forever on
                # authentication/permission failures.
                if status in {401, 403}:
                    logger.error(
                        "Discord HTTP authentication/permission failure: status=%s code=%s message=%s",
                        status, getattr(exc, "code", "unknown"), safe_log(str(exc)),
                    )
                    raise SystemExit(1) from None
                delay = min(
                    DISCORD_LOGIN_RETRY_MAX,
                    DISCORD_LOGIN_RETRY_BASE * (2 ** min(max(attempt - 1, 0), 6)),
                )
                logger.warning(
                    "Discord HTTP startup/session failure: status=%s code=%s message=%s | retry=%ss",
                    status, getattr(exc, "code", "unknown"), safe_log(str(exc)), delay,
                )
                if DISCORD_LOGIN_MAX_ATTEMPTS and attempt >= DISCORD_LOGIN_MAX_ATTEMPTS:
                    logger.error("Discord retry limit reached (%s attempts).", DISCORD_LOGIN_MAX_ATTEMPTS)
                    raise SystemExit(1) from None
                with contextlib.suppress(Exception):
                    await bot.close()
                await asyncio.sleep(delay)

            except discord.GatewayNotFound as exc:
                delay = min(
                    DISCORD_LOGIN_RETRY_MAX,
                    DISCORD_LOGIN_RETRY_BASE * (2 ** min(max(attempt - 1, 0), 6)),
                )
                logger.warning("Discord gateway unavailable: %s | retry=%ss", safe_log(exc), delay)
                if DISCORD_LOGIN_MAX_ATTEMPTS and attempt >= DISCORD_LOGIN_MAX_ATTEMPTS:
                    logger.error("Discord retry limit reached (%s attempts).", DISCORD_LOGIN_MAX_ATTEMPTS)
                    raise SystemExit(1) from None
                with contextlib.suppress(Exception):
                    await bot.close()
                await asyncio.sleep(delay)

            except discord.ClientException as exc:
                # Client lifecycle/configuration errors are generally not fixed
                # by retrying, except when they are explicitly transport-like.
                text = str(exc).lower()
                if any(token in text for token in (
                    "connection", "connect", "reset", "timeout", "gateway", "disconnected"
                )):
                    delay = min(
                        DISCORD_LOGIN_RETRY_MAX,
                        DISCORD_LOGIN_RETRY_BASE * (2 ** min(max(attempt - 1, 0), 6)),
                    )
                    logger.warning("Discord client connectivity error: %s | retry=%ss", safe_log(exc), delay)
                    if DISCORD_LOGIN_MAX_ATTEMPTS and attempt >= DISCORD_LOGIN_MAX_ATTEMPTS:
                        raise SystemExit(1) from None
                    with contextlib.suppress(Exception):
                        await bot.close()
                    await asyncio.sleep(delay)
                    continue
                logger.error("Discord client startup failed: %s", safe_log(exc))
                raise SystemExit(1) from None

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                # Last-resort protection: unexpected startup exceptions are
                # logged and retried unless explicitly limited by env config.
                delay = min(
                    DISCORD_LOGIN_RETRY_MAX,
                    DISCORD_LOGIN_RETRY_BASE * (2 ** min(max(attempt - 1, 0), 6)),
                )
                logger.exception(
                    "Unexpected Discord startup/session exception: %s | retry=%ss",
                    safe_log(exc), delay,
                )
                if DISCORD_LOGIN_MAX_ATTEMPTS and attempt >= DISCORD_LOGIN_MAX_ATTEMPTS:
                    raise
                with contextlib.suppress(Exception):
                    await bot.close()
                await asyncio.sleep(delay)

    finally:
        for loop in (sync_statuses, update_presence, supervise_all_ports_loop, refresh_network_identity):
            if loop.is_running():
                loop.cancel()
        with contextlib.suppress(Exception):
            await bot.close()
        with contextlib.suppress(Exception):
            await stop_health_server()
        release_singleton()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("RGNODES stopped by user.")
