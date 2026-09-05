# Daily Postgres backup job for rent-control.
# Runs once, uploads one dump, exits. Railway runs it on a cron schedule.

FROM python:3.12-slim

# pg_dump must be at least as new as the server (Railway runs Postgres 18),
# so we take the client from the official PostgreSQL apt repository rather
# than Debian's older packages.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg openssl; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-18; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backup.py .

# Fail loudly rather than hang forever if the database is unreachable.
ENV PGCONNECT_TIMEOUT=30

CMD ["python", "-u", "backup.py"]
