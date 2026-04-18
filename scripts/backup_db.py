#!/usr/bin/env python
"""Daily Postgres backup script.

Dumps the database via pg_dump and uploads to S3/Backblaze B2 (if
`BACKUP_S3_BUCKET`/`BACKUP_S3_ENDPOINT` are set), or drops in a local
folder otherwise.

Invocation:
    uv run python scripts/backup_db.py

Recommended schedule: daily cron on Railway (separate "backup" service
type) or a plain `crontab` on a local Mac.

Reads config from the same `.scratch/deploy-secrets.env` the rest of
the stack uses, plus:
    BACKUP_S3_BUCKET=...
    BACKUP_S3_ENDPOINT=https://s3.us-east-005.backblazeb2.com
    BACKUP_S3_ACCESS_KEY=...
    BACKUP_S3_SECRET_KEY=...

Note: the Railway-internal database isn't accessible; we use the public
TCP-proxy DSN the bot already uses.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_secrets = ROOT / ".scratch" / "deploy-secrets.env"
if _secrets.exists():
    for line in _secrets.read_text().splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _psql_dsn() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if raw.startswith(prefix):
            return raw.replace(prefix, "postgresql://", 1)
    return raw


def dump_to(path: Path) -> None:
    dsn = _psql_dsn()
    if not dsn:
        raise SystemExit("DATABASE_URL not set")
    # pg_dump is part of postgresql-client; assume the developer mac has it.
    cmd = [
        "pg_dump",
        "--no-owner",
        "--no-privileges",
        "--format=custom",
        "--file",
        str(path),
        dsn,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"pg_dump failed: {result.stderr}")


def upload_s3(path: Path) -> None:
    """Upload to S3-compatible if creds are present; otherwise skip."""
    bucket = os.environ.get("BACKUP_S3_BUCKET")
    endpoint = os.environ.get("BACKUP_S3_ENDPOINT")
    ak = os.environ.get("BACKUP_S3_ACCESS_KEY")
    sk = os.environ.get("BACKUP_S3_SECRET_KEY")
    if not all((bucket, endpoint, ak, sk)):
        print("no S3 creds — backup stays local at", path)
        return

    try:
        import boto3
    except ImportError:
        print("boto3 not installed. Install with: uv add --dev boto3")
        sys.exit(1)

    session = boto3.session.Session()
    client = session.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
    )
    key = f"sber26-bot/{path.name}"
    with open(path, "rb") as f:
        client.upload_fileobj(f, bucket, key)
    print(f"uploaded s3://{bucket}/{key}")


def main() -> None:
    backups = ROOT / "backups"
    backups.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dump_path = backups / f"sber26_{ts}.dump"
    print(f"dumping to {dump_path}...")
    dump_to(dump_path)
    print(f"ok, {dump_path.stat().st_size // 1024} KB")
    upload_s3(dump_path)


if __name__ == "__main__":
    main()
