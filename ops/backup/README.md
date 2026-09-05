# rent-control — daily database backup

A Railway cron service that dumps the production Postgres once a day,
encrypts the dump, and uploads it to a Cloud Storage bucket in the EU.

Railway's own scheduled backups and PITR are Pro-plan features. This is the
Hobby-plan substitute: roughly $0.15/month instead of $15.

    02:00 UTC ──> pg_dump ──> AES-256 ──> gs://…-eu/daily/rent-control-<timestamp>.dump.enc
                                              └─ 1st of month also ──> monthly/…

## Design notes

**The backup lives at a different provider than the database.** A copy sitting
on Railway dies with the Railway account. That is the whole point.

**The job can create objects but cannot read, replace or delete them.** The
service account gets `roles/storage.objectCreator` only. Replacing an existing
object needs `storage.objects.create` *and* `storage.objects.delete`, so
whoever compromises the backend can neither read the backups nor destroy the
history on the way out. Old copies are expired by the bucket's own lifecycle
rules instead — see `lifecycle.json` (35 days for `daily/`, 400 days for
`monthly/`).

Two consequences worth knowing:

* Object names carry a full timestamp rather than just a date, so a second run
  on the same day writes a new object instead of failing on a name that is
  already taken.
* The job does not read the object back to check it. It cannot — that would be
  a read. Transfer integrity is enforced by a crc32c checksum the API verifies
  server-side; whether the backup is *usable* is what the restore drill answers.

**The base image and the PGDG suite name have to change together.** The
Dockerfile pins `python:3.12-slim-bookworm` and asks apt for `bookworm-pgdg`,
and those two must always name the same Debian release. The first build broke
exactly here: `python:3.12-slim` rolled from bookworm to trixie, apt went on
installing the Debian 12 build of `postgresql-client-18`, and its `libpq5`
wanted a package trixie does not ship at all. Moving to a newer base means
editing both lines in the same commit — there is a comment on them saying so.

**The dump is encrypted before it leaves the container.** It contains renter
names, addresses and payment amounts in plain text. Cloud Storage encrypts at
rest anyway; this protects against the bucket itself being exposed.

> **Losing `BACKUP_ENCRYPTION_KEY` means losing every backup.** Store it in a
> password manager before you set it. It is not recoverable from anywhere else.

**A failed run exits non-zero and pings the dead-man's switch as failed.** The
job is not tied to any one monitoring provider — it reports to whatever
`HEARTBEAT_URL` points at, and this deployment points it at healthchecks.io
(step 5). A backup that stops running silently is worse than no backup, because
you think you have one.

## One-time setup

### 1. Bucket

```bash
PROJECT=rent-control-5c5da
BUCKET=rent-control-backups-eu   # must be globally unique; add a suffix if taken

gcloud storage buckets create "gs://$BUCKET" \
  --project="$PROJECT" \
  --location=EUROPE-WEST1 \
  --default-storage-class=STANDARD \
  --uniform-bucket-level-access \
  --public-access-prevention

gcloud storage buckets update "gs://$BUCKET" --lifecycle-file=lifecycle.json
```

### 2. Service account, create-only

```bash
gcloud iam service-accounts create rent-control-backup \
  --project="$PROJECT" --display-name="rent-control daily backup"

SA="rent-control-backup@$PROJECT.iam.gserviceaccount.com"

# objectCreator: may write new objects, may not read, overwrite or delete them
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" --role="roles/storage.objectCreator"

gcloud iam service-accounts keys create key.json \
  --iam-account="$SA" --project="$PROJECT"
```

`key.json` is a credential. Paste it into Railway, then delete the local file.

### 3. Railway service

New service in the `production` environment, pointed at the repo, with
**Settings → Source → Root Directory** set to the folder holding this
Dockerfile (`ops/backup` if you keep it in the backend repo). Then:

* **Settings → Deploy → Cron Schedule**: `0 2 * * *` — an hour before the app's own
  `run-cpi-indexing`, so a full dump and the indexing job do not hit the database together
* **Settings → Deploy → Region**: same region as the database
* **Settings → Deploy → Restart Policy**: `Never` (a cron job that restarts on
  failure will hammer the database)
* **Settings → Source → Watch Paths**: `/ops/backup/**` — **with the leading
  slash** — so application deploys do not rebuild this job and vice versa

  The slash is not decoration. Railway evaluates a watch path **relative to the
  service's Root Directory**, which for this service is already `ops/backup` —
  so a bare `ops/backup/**` looks for `ops/backup/ops/backup/` and matches
  nothing. Every push then lands as *"SKIPPED — No changes to watched files"*
  and the job quietly keeps running the old image: a deploy that looks like it
  happened and did not. The PR #8 merge was skipped exactly this way and had to
  be deployed by hand from the command palette's **Deploy latest commit**. A
  leading slash anchors the pattern at the repo root instead. Do not
  "simplify" it away.

### 4. Variables

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` — the private URL, so the dump never leaves Railway's network |
| `GCS_BUCKET` | the bucket name from step 1 |
| `GCP_SERVICE_ACCOUNT_JSON` | the full contents of `key.json` |
| `BACKUP_ENCRYPTION_KEY` | a long random passphrase — **save it in a password manager first** |
| `HEARTBEAT_URL` | ping URL from step 5 (optional but recommended) |

### 5. Dead-man's-switch

A backup that silently stops running is the failure this is meant to prevent,
so something outside this service has to notice when a run does not happen.

Any ping service works — the job just calls a URL. Two shapes are supported and
the right one is picked automatically:

* **healthchecks.io / Cronitor** (`BASE/start`, `BASE`, `BASE/fail`) — the
  default, and free for a single check.
* **Sentry Crons** (`BASE/?status=ok`) — used automatically when the URL is on
  `sentry.io`. **Not what this deployment uses:** each Sentry cron monitor
  counts against the plan's monitor quota, and the organisation's single seat is
  already spent on `nightly-jobs`. Displacing that one to watch this job would
  just move the blind spot, so this job runs on healthchecks.io instead.

Set the URL in `HEARTBEAT_URL`, schedule `0 2 * * *`, grace period 30 minutes.
The grace period doubles as a run-duration limit — the clock starts at the
`in_progress` ping — so keep it at or above `PGDUMP_TIMEOUT` (1800s). Set it
shorter and a slow-but-successful dump is reported as a failure.
`HEARTBEAT_STYLE` (`path` or `query`) overrides the auto-detection if needed.

Deliberately **not** wired into the application's own job-tracking table: this
job must be able to report a failure caused by the database being unreachable,
and it cannot do that by writing to that database.

## Restoring

**Check the client version before anything else.** The dump is written by
pg_dump 18, so restoring it needs **pg_restore 18 or newer**. An older client
rejects the archive, and a perfectly good backup then looks broken — the most
expensive way to fail this drill is to conclude the backup is bad when it is the
tool that is out of date.

```bash
pg_restore --version   # must be 18.x or newer
```

**Google Cloud Shell is a workable place to run the drill** — gcloud is already
authenticated, there is no local Docker to set up, and nothing has to be
installed on your own machine. One catch: its preinstalled client is **16.15**,
and installing `postgresql-client-18` is not enough on its own. The
`pg_restore` on `PATH` is a wrapper that keeps resolving to 16 until the
version 18 bin directory comes first.

```bash
sudo apt-get install -y postgresql-client-18
export PATH=/usr/lib/postgresql/18/bin:$PATH
pg_restore --version   # confirm this says 18 before going any further
```

Then the restore itself, always against a **scratch** database:

```bash
export BACKUP_ENCRYPTION_KEY='…'
./restore.sh gs://rent-control-backups-eu/daily/rent-control-2026-09-04.dump.enc \
             postgresql://user:pass@host:port/restore_check
```

Run this once now, and once a quarter after that. **A backup you have never
restored is not a backup.** The first drill is part of the setup, not an
optional extra.

### Drill log

| Date | Object restored | Result |
|---|---|---|
| 2026-09-05 | `daily/rent-control-2026-09-05T163708Z.dump.enc` | **Pass.** Restored clean into an empty Postgres 18, exit 0, all 23 tables populated — 665 transactions, 63 renters, 59 properties, 5 owners. |

## What this does not cover

Daily backups mean you can lose up to 24 hours of writes. That is a large
improvement on the current state and an unacceptable one for a product holding
people's payment records at scale. When there are real users, move to Railway
Pro for point-in-time recovery and keep this as the offsite second copy.
