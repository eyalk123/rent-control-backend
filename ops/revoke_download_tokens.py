"""Revoke the download token on every uploaded Storage file.

A Firebase download URL carries a token, stored on the file as the
``firebaseStorageDownloadTokens`` metadata key, that opens the file for anyone holding the
link, with no sign-in and no expiry (PLATFORM.md §18). Once the apps read files as the
signed-in user and the database holds bare paths (migration 070), the tokens serve no one;
removing the key makes every link ever handed out stop working.

Run it on production after the deploy carrying migration 070 is live. It reads the
environment the API runs with, and is self-contained so it does not depend on the
deployed code being current:

    MSYS_NO_PATHCONV=1 railway ssh --service rent-control-backend -i ~/.ssh/railway-rentvance \\
        -- /opt/venv/bin/python - < ops/revoke_download_tokens.py              # report only
    MSYS_NO_PATHCONV=1 railway ssh --service rent-control-backend -i ~/.ssh/railway-rentvance \\
        -- /opt/venv/bin/python - --apply < ops/revoke_download_tokens.py      # revoke

Safety:

* Report-only unless ``--apply`` is passed. Counts only — a path carries the uploaded file
  name, which may name a tenant.
* Refuses to run while any database row still holds a download URL: that file would stop
  opening for its owner.
* Only the three client prefixes are listed — nothing else in the bucket is touched.
* Re-runnable. Firebase may still give a new upload a token, so a later run can find some;
  those were never handed out, since no client asks for a download URL any more.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import firebase_admin
from firebase_admin import credentials, storage
from sqlalchemy import create_engine, text

PREFIXES = ("properties/", "renters/", "transactions/")
TOKEN_KEY = "firebaseStorageDownloadTokens"
COLUMNS = [
    ("properties", "image_url"),
    ("properties", "basic_contract_url"),
    ("properties", "land_registry_url"),
    ("renters", "full_contract_url"),
    ("renters", "id_image_url"),
    ("transactions", "receipt_image_url"),
    ("property_files", "url"),
    ("document_extraction_logs", "contract_url"),
]


def _urls_left_in_database() -> int:
    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
    engine = create_engine(url)
    total = 0
    with engine.connect() as conn:
        for table, column in COLUMNS:
            total += conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE {column} LIKE :prefix"),
                {"prefix": "https://firebasestorage.googleapis.com/%"},
            ).scalar_one()
    return total


def _bucket():
    bucket_name = os.environ.get("FIREBASE_STORAGE_BUCKET")
    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not bucket_name or not sa_json:
        return None
    app = firebase_admin.initialize_app(
        credentials.Certificate(json.loads(sa_json)), {"storageBucket": bucket_name}
    )
    return storage.bucket(app=app)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="revoke the tokens it finds")
    args = parser.parse_args()

    left = _urls_left_in_database()
    if left:
        print(f"{left} database value(s) are still download URLs — run migration 070 first.")
        return 1

    bucket = _bucket()
    if bucket is None:
        print("FIREBASE_STORAGE_BUCKET / FIREBASE_SERVICE_ACCOUNT_JSON not set", file=sys.stderr)
        return 1

    scanned = tokened = revoked = failed = 0
    for prefix in PREFIXES:
        for blob in bucket.list_blobs(prefix=prefix):
            scanned += 1
            if not (blob.metadata or {}).get(TOKEN_KEY):
                continue
            tokened += 1
            if not args.apply:
                continue
            # A key set to None is deleted by the patch; the rest of the metadata is kept.
            blob.metadata = {TOKEN_KEY: None}
            try:
                blob.patch()
                revoked += 1
            except Exception as exc:
                # The type only: a GCS error message quotes the object path.
                print(f"failed: {type(exc).__name__}", file=sys.stderr)
                failed += 1

    print(f"files scanned: {scanned}")
    print(f"files with a download token: {tokened}")
    if args.apply:
        print(f"revoked: {revoked}, failed: {failed}")
    else:
        print("report only — pass --apply to revoke")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
