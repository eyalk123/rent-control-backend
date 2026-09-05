#!/usr/bin/env bash
# Restore a backup into a database. Use this for the quarterly restore drill
# and, one day, for real.
#
#   ./restore.sh gs://rent-control-backups-eu/daily/rent-control-2026-09-04.dump.enc \
#                postgresql://user:pass@host:port/restore_check
#
# Never point this at the production database unless you mean it.
set -euo pipefail

OBJECT="${1:?usage: restore.sh gs://bucket/path/to.dump[.enc] TARGET_DATABASE_URL}"
TARGET="${2:?usage: restore.sh gs://bucket/path/to.dump[.enc] TARGET_DATABASE_URL}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> downloading $OBJECT"
gcloud storage cp "$OBJECT" "$WORK/backup"

DUMP="$WORK/backup"
if [[ "$OBJECT" == *.enc ]]; then
  : "${BACKUP_ENCRYPTION_KEY:?set BACKUP_ENCRYPTION_KEY to decrypt this backup}"
  echo "==> decrypting"
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
    -in "$WORK/backup" -out "$WORK/backup.dump" -pass env:BACKUP_ENCRYPTION_KEY
  DUMP="$WORK/backup.dump"
fi

echo "==> restoring into $TARGET"
pg_restore --no-owner --no-privileges --exit-on-error --dbname "$TARGET" "$DUMP"

echo "==> row counts"
psql "$TARGET" -qtAc "
  SELECT relname || ': ' || n_live_tup
  FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 20;"

echo "==> restore finished"
