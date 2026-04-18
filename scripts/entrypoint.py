#!/usr/bin/env python
"""Container entrypoint with aggressive diagnostics.

Writes progress markers through plain `print(..., flush=True)` so Railway
log collectors see them even under Docker stdout-buffering. Replaces the
previous `alembic upgrade && python -m src.bot.main` shell chain.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse


def mark(msg: str) -> None:
    print(f"[entrypoint] {msg}", flush=True)


def probe_db() -> None:
    """Check we can even open a TCP socket to the DB host:port."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        mark("DATABASE_URL not set")
        return
    # Strip SQLAlchemy driver prefix for urlparse to work.
    stripped = dsn.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )
    try:
        u = urlparse(stripped)
        host, port = u.hostname or "", u.port or 5432
    except Exception as e:  # noqa: BLE001
        mark(f"DSN parse failed: {e}")
        return
    if not host:
        mark("DSN has no host")
        return
    mark(f"DNS resolving {host}...")
    t0 = time.monotonic()
    try:
        ipv4 = socket.gethostbyname(host)
        mark(f"DNS ok: {host} -> {ipv4} ({time.monotonic()-t0:.2f}s)")
    except Exception as e:  # noqa: BLE001
        mark(f"DNS FAIL for {host}: {e}")
        return
    mark(f"TCP connecting to {host}:{port}...")
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=8) as s:
            mark(f"TCP ok ({time.monotonic()-t0:.2f}s) local={s.getsockname()}")
    except Exception as e:  # noqa: BLE001
        mark(f"TCP FAIL {host}:{port} after {time.monotonic()-t0:.2f}s: {e}")


def run_alembic() -> None:
    mark("alembic upgrade head ...")
    t0 = time.monotonic()
    r = subprocess.run(
        ["alembic", "upgrade", "head"],
        stdout=sys.stdout, stderr=sys.stderr,
    )
    mark(f"alembic exit={r.returncode} in {time.monotonic()-t0:.2f}s")
    if r.returncode != 0:
        sys.exit(r.returncode)


def run_bot() -> None:
    mark("importing bot main ...")
    # Late import so prior markers fire even if the bot imports heavy modules.
    from src.bot import main as bot_main
    mark("calling bot main() ...")
    bot_main.main()


def main() -> None:
    mark(f"entrypoint.py starting (pid={os.getpid()})")
    mark(
        "env snapshot: APP_ENV=%s, HAS_TOKEN=%s, HAS_ANTHROPIC=%s"
        % (
            os.environ.get("APP_ENV"),
            "yes" if os.environ.get("TELEGRAM_BOT_TOKEN") else "no",
            "yes" if os.environ.get("ANTHROPIC_API_KEY") else "no",
        )
    )
    probe_db()
    run_alembic()
    run_bot()


if __name__ == "__main__":
    main()
