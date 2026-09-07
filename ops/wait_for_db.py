#!/usr/bin/env python3
"""
Block until Postgres accepts connections, then exit 0 so the migration can run.

`alembic upgrade head` is the first thing the API container does, and it runs in the
first second of the container's life - before Railway's private network is reliably
resolvable, and while a database that is mid-restart still refuses connections. The
restart policy (`ON_FAILURE` in railway.toml) does recover from that on its own, but
only by crash-looping the container until the blip passes. A few seconds of waiting
here replaces the loop.

This is the same wait as `ops/backup/backup.py:wait_for_database` - same attempt count,
same interval, same "give up loudly rather than continue" ending - with one forced
difference: the backup runs in its own Dockerfile which installs postgresql-client, so
it can probe with `pg_isready`. The API is built by Nixpacks and has no Postgres client
binary, so the probe is a psycopg2 connection that is opened and immediately closed.
psycopg2 is already a dependency (requirements.txt) because SQLAlchemy needs it.

Exhausting the attempts exits non-zero, which short-circuits the `&&` chain in the
start command and leaves the restart policy to do what it does today - a database that
is genuinely down must still fail the start loudly.

Required environment
  DATABASE_URL              postgres://...  (the same URL alembic will use)
"""

import os
import sys
import time
from datetime import datetime, timezone

import psycopg2

# Five attempts three seconds apart, i.e. up to ~12 seconds of waiting plus whatever
# each probe itself spends. Same numbers as ops/backup/backup.py.
READY_ATTEMPTS = 5
READY_INTERVAL_SECONDS = 3
READY_PROBE_TIMEOUT = 5


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def wait_for_database(database_url: str) -> None:
    detail = ""
    for attempt in range(1, READY_ATTEMPTS + 1):
        try:
            conn = psycopg2.connect(database_url, connect_timeout=READY_PROBE_TIMEOUT)
        except Exception as exc:
            # psycopg2 reports host and port but never the password, so this is safe to
            # log; the URL itself is never printed.
            detail = " ".join(str(exc).split()) or exc.__class__.__name__
            log(f"database not ready (attempt {attempt}/{READY_ATTEMPTS}): {detail}")
            if attempt < READY_ATTEMPTS:
                time.sleep(READY_INTERVAL_SECONDS)
            continue
        conn.close()
        log(f"database is accepting connections (attempt {attempt})")
        return
    raise RuntimeError(
        f"database did not accept connections after {READY_ATTEMPTS} attempts "
        f"{READY_INTERVAL_SECONDS}s apart: {detail}"
    )


def main() -> int:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        log("missing required environment variable: DATABASE_URL")
        return 1
    wait_for_database(database_url)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"DATABASE WAIT FAILED: {exc}")
        sys.exit(1)
