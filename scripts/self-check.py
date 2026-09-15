#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def compile_file(path: Path) -> bool:
    try:
        ast.parse(path.read_text(encoding="utf-8"))
        ok(f"Python syntax: {path.relative_to(ROOT)}")
        return True
    except Exception as exc:
        fail(f"Python syntax: {path}: {exc}")
        return False


def check_import(name: str) -> bool:
    if importlib.util.find_spec(name) is None:
        fail(f"Python package/module missing: {name}")
        return False
    ok(f"Python package/module available: {name}")
    return True


def check_docker(deep: bool = False) -> bool:
    docker = shutil.which("docker")
    if not docker:
        fail("Docker CLI not installed.")
        return False
    try:
        proc = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            text=True, capture_output=True, timeout=15,
        )
    except Exception as exc:
        fail(f"Docker preflight error: {exc}")
        return False
    if proc.returncode != 0:
        fail("Docker CLI exists but Docker daemon/socket is unavailable.")
        print(proc.stderr.strip())
        return False
    ok(f"Docker daemon reachable (server {proc.stdout.strip() or 'unknown'})")
    required = ["run", "exec", "inspect", "ps", "pull"]
    help_text = subprocess.run([docker, "run", "--help"], text=True, capture_output=True, timeout=10)
    text = help_text.stdout + help_text.stderr
    missing = [x for x in required if x not in text and x != "inspect"]
    if missing:
        warn(f"Unusual Docker CLI help output; verify manually: {', '.join(missing)}")
    else:
        ok("Docker run command is available")
    if "--privileged" not in text:
        warn("Docker CLI does not advertise --privileged; native systemd guest mode may be unavailable.")
        return True
    if deep:
        try:
            probe = subprocess.run(
                [docker, "run", "--rm", "--privileged",
                 "--security-opt", "seccomp=unconfined",
                 "--security-opt", "apparmor=unconfined",
                 "ubuntu:24.04", "/bin/sh", "-lc", "echo RGNODES-RUNTIME-PREFLIGHT-OK"],
                text=True, capture_output=True, timeout=120,
            )
        except Exception as exc:
            fail(f"Docker runtime preflight exception: {exc}")
            return False
        if probe.returncode != 0:
            detail=(probe.stderr or probe.stdout).strip()
            fail("Docker can answer `info` but cannot launch a privileged child container.")
            if detail:
                print(detail)
            return False
        ok("Privileged Docker child-container runtime works")
    return True


def check_ports() -> None:
    for port in (247, 5665):
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            warn(f"Port {port} is already in use.")
        else:
            ok(f"Port {port} is locally available.")
        finally:
            sock.close()


def check_sqlite() -> bool:
    try:
        db = ROOT / ".selfcheck.sqlite3"
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS selfcheck(value TEXT)")
        conn.execute("INSERT INTO selfcheck(value) VALUES('ok')")
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                (Path(str(db) + suffix)).unlink()
            except FileNotFoundError:
                pass
        ok("SQLite read/write works in the project directory.")
        return True
    except Exception as exc:
        fail(f"SQLite filesystem test failed: {exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--deep", action="store_true", help="Run an actual privileged Docker child-container probe")
    args = ap.parse_args()
    if args.quiet:
        # Keep only failure signals for installer use.
        original = globals()["ok"]
        globals()["ok"] = lambda _msg: None

    results = []
    results += [compile_file(ROOT / "vm.py")]
    results += [compile_file(ROOT / "app" / "main.py")]
    for mod in ("discord", "aiohttp", "dotenv", "psutil"):
        results.append(check_import(mod))
    results.append(check_sqlite())
    if shutil.which("docker"):
        results.append(check_docker(deep=args.deep))
    else:
        results.append(False)
    check_ports()

    failures = sum(not bool(x) for x in results)
    if failures:
        print(f"\nSelf-check failed: {failures} required check(s).", file=sys.stderr)
        return 1
    print("\nSelf-check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
