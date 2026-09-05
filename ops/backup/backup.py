#!/usr/bin/env python3
"""
Daily Postgres backup for rent-control.

Dumps the production database, optionally encrypts the dump client-side,
and uploads it to a Cloud Storage bucket in the EU.

Retention is NOT handled here on purpose: the service account this runs as
can create objects but cannot delete them, so a compromised host cannot
destroy the backup history. Old objects are removed by the bucket's own
lifecycle rules (see lifecycle.json).

Required environment
  DATABASE_URL              postgres://...  (use Railway private networking)
  GCS_BUCKET                bucket name, e.g. rent-control-backups-eu
  GCP_SERVICE_ACCOUNT_JSON  full JSON key of the create-only service account

Optional environment
  BACKUP_ENCRYPTION_KEY     passphrase; when set the dump is AES-256 encrypted
                            before it leaves this container
  HEARTBEAT_URL             dead-man's-switch ping URL (healthchecks.io, Sentry
                            Crons, Cronitor - anything that expects a ping)
  HEARTBEAT_STYLE           "path" (…/start, …/fail) or "query" (…/?status=ok).
                            Auto-detected from the URL when unset.
  BACKUP_PREFIX             object name prefix (default: rent-control)
  PGDUMP_TIMEOUT            seconds before the dump is abandoned (default: 1800)
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.cloud import storage
from google.oauth2 import service_account

PREFIX = os.getenv("BACKUP_PREFIX", "rent-control")
DUMP_TIMEOUT = int(os.getenv("PGDUMP_TIMEOUT", "1800"))

# SENTRY_CRON_URL is the older name for the same thing; both are accepted.
HEARTBEAT_URL = (os.getenv("HEARTBEAT_URL") or os.getenv("SENTRY_CRON_URL") or "").rstrip("/")


class ConfigError(Exception):
    """Something is missing or malformed in the environment."""


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def _heartbeat_url(status: str) -> str:
    """Build the ping URL for this run's status.

    Two shapes are in use in the wild and both are supported, because which
    service watches this job should not dictate how the job is written:

      path   healthchecks.io / Cronitor:  BASE/start, BASE, BASE/fail
      query  Sentry Crons:                BASE/?status=in_progress|ok|error
    """
    style = os.getenv("HEARTBEAT_STYLE") or ("query" if "sentry.io" in HEARTBEAT_URL else "path")
    if style == "query":
        return f"{HEARTBEAT_URL}/?status={status}"
    return HEARTBEAT_URL + {"in_progress": "/start", "ok": "", "error": "/fail"}[status]


def checkin(status: str) -> None:
    """Report that this run started, finished, or failed. Never fatal.

    A monitoring outage must not turn a good backup into a failed one, so
    every error here is logged and swallowed.
    """
    if not HEARTBEAT_URL:
        return
    try:
        requests.get(_heartbeat_url(status), timeout=10)
    except Exception as exc:
        log(f"warning: heartbeat ({status}) failed: {exc}")


def require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value


def run_pg_dump(database_url: str, target: Path) -> None:
    """Custom-format dump. Compressed by pg_dump itself, restored with pg_restore."""
    log("starting pg_dump")
    with target.open("wb") as fh:
        proc = subprocess.run(
            ["pg_dump", "--format=custom", "--compress=9", "--no-owner",
             "--no-privileges", "--dbname", database_url],
            stdout=fh,
            stderr=subprocess.PIPE,
            timeout=DUMP_TIMEOUT,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed ({proc.returncode}): {proc.stderr.decode(errors='replace')[:2000]}")
    size = target.stat().st_size
    if size == 0:
        raise RuntimeError("pg_dump produced an empty file")
    log(f"pg_dump finished, {size / 1_048_576:.1f} MB")


def encrypt(source: Path, target: Path, passphrase: str) -> None:
    log("encrypting dump (AES-256)")
    proc = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-salt",
         "-in", str(source), "-out", str(target), "-pass", "env:BACKUP_ENCRYPTION_KEY"],
        env={**os.environ, "BACKUP_ENCRYPTION_KEY": passphrase},
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"openssl failed ({proc.returncode}): {proc.stderr.decode(errors='replace')[:2000]}")
    log(f"encrypted, {target.stat().st_size / 1_048_576:.1f} MB")


def object_names(now: datetime, encrypted: bool) -> list[str]:
    """One daily copy always; an extra monthly copy on the first of the month.

    The two prefixes exist so the bucket's lifecycle rules can expire them on
    different schedules without any delete permission on our side.

    The name carries a full timestamp, not just a date, because the service
    account may create objects but not replace them. A second run on the same
    day writes a new object instead of failing on a name that already exists.
    """
    ext = "dump.enc" if encrypted else "dump"
    stem = f"{PREFIX}-{now:%Y-%m-%dT%H%M%SZ}.{ext}"
    names = [f"daily/{stem}"]
    if now.day == 1:
        names.append(f"monthly/{stem}")
    return names


def upload(client: storage.Client, bucket_name: str, path: Path, names: list[str]) -> None:
    """Upload with a crc32c checksum so a corrupted transfer is rejected server-side.

    We deliberately do not read the object back to verify it: the service
    account has create permission only, so any read would 403. Integrity is
    the checksum's job, and confirming the backup is usable is the restore
    drill's job - see README.
    """
    bucket = client.bucket(bucket_name)
    local_size = path.stat().st_size
    for name in names:
        log(f"uploading gs://{bucket_name}/{name} ({local_size / 1_048_576:.1f} MB)")
        blob = bucket.blob(name)
        blob.upload_from_filename(str(path), timeout=900, checksum="crc32c")
        log(f"uploaded {name}")


def main() -> int:
    database_url = require("DATABASE_URL")
    bucket_name = require("GCS_BUCKET")
    sa_json = require("GCP_SERVICE_ACCOUNT_JSON")
    passphrase = os.getenv("BACKUP_ENCRYPTION_KEY") or ""

    try:
        credentials = service_account.Credentials.from_service_account_info(json.loads(sa_json))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ConfigError(f"GCP_SERVICE_ACCOUNT_JSON is not a valid service account key: {exc}")

    client = storage.Client(credentials=credentials, project=credentials.project_id)
    now = datetime.now(timezone.utc)

    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / "dump"
        run_pg_dump(database_url, dump_path)

        upload_path = dump_path
        if passphrase:
            upload_path = Path(tmp) / "dump.enc"
            encrypt(dump_path, upload_path, passphrase)
        else:
            log("warning: BACKUP_ENCRYPTION_KEY is not set - the dump is uploaded unencrypted "
                "(still encrypted at rest by Cloud Storage)")

        upload(client, bucket_name, upload_path, object_names(now, bool(passphrase)))

    log("backup complete")
    return 0


if __name__ == "__main__":
    checkin("in_progress")
    try:
        code = main()
    except Exception as exc:
        log(f"BACKUP FAILED: {exc}")
        checkin("error")
        sys.exit(1)
    checkin("ok")
    sys.exit(code)
