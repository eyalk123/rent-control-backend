"""One-off sweep of Firebase Storage files that nothing points at any more.

Until the fix that introduced ``firebase_storage.owner_prefixes``, three paths left files
behind: account deletion listed a ``{owner_id}/`` prefix no upload ever used, so it deleted
nothing; replacing a document kept the old one; and deleting a transaction or a property
file kept its file. Those files are still in the bucket, reachable through their download
URLs. This finds them.

    cd rent-control-backend
    railway run python ops/storage_orphan_sweep.py            # report only
    railway run python ops/storage_orphan_sweep.py --apply    # delete what it reported

Two kinds of orphan, reported separately:

* **deleted account** — the ``{owner_id}`` path segment has no ``owners`` row. The
  tombstone keeps only a hash of the id, so this is the only way to find them.
* **unreferenced** — the owner exists, but no record of theirs holds the file's URL.

Safety:

* Report-only unless ``--apply`` is passed. The report prints paths, never file contents;
  the path does carry the uploaded file name, which may name a tenant.
* Files younger than ``--min-age-hours`` (default 48) are skipped: a client uploads first
  and saves the record second, so a fresh file with no record may be a save in flight.
* Only the three client prefixes are listed — nothing else in the bucket is touched.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.owner import Owner  # noqa: E402
from app.models.property import Property  # noqa: E402
from app.models.property_file import PropertyFile  # noqa: E402
from app.models.renter import Renter  # noqa: E402
from app.models.transaction import Transaction  # noqa: E402
from app.services.firebase_storage import ENTITY_TYPES, _blob_path_from_url, _get_bucket  # noqa: E402


def _referenced_paths(db) -> set[str]:
    """Every blob path some record references, across all owners."""
    columns = [
        Property.image_url,
        Property.basic_contract_url,
        Property.land_registry_url,
        Renter.full_contract_url,
        Renter.id_image_url,
        Transaction.receipt_image_url,
        PropertyFile.url,
    ]
    paths: set[str] = set()
    for column in columns:
        for url in db.scalars(select(column).where(column.is_not(None))):
            path = _blob_path_from_url(url)
            if path:
                paths.add(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="delete the orphans it finds")
    parser.add_argument("--min-age-hours", type=int, default=48)
    args = parser.parse_args()

    bucket = _get_bucket()
    if bucket is None:
        print("FIREBASE_STORAGE_BUCKET / FIREBASE_SERVICE_ACCOUNT_JSON not set", file=sys.stderr)
        return 1

    with SessionLocal() as db:
        owners = set(db.scalars(select(Owner.id)))
        referenced = _referenced_paths(db)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.min_age_hours)
    orphans: dict[str, list] = {"deleted account": [], "unreferenced": []}
    too_new = 0
    for entity_type in ENTITY_TYPES:
        for blob in bucket.list_blobs(prefix=f"{entity_type}/"):
            parts = blob.name.split("/")
            if len(parts) < 3 or blob.name.endswith("/") or blob.name in referenced:
                continue
            if blob.time_created and blob.time_created > cutoff:
                too_new += 1
                continue
            kind = "unreferenced" if parts[1] in owners else "deleted account"
            orphans[kind].append(blob)

    for kind, blobs in orphans.items():
        size = sum(b.size or 0 for b in blobs)
        print(f"\n{kind}: {len(blobs)} files, {size / 1024 / 1024:.1f} MB")
        for blob in blobs:
            print(f"  {blob.name}")
    print(f"\nskipped {too_new} files younger than {args.min_age_hours}h")

    if not args.apply:
        print("\nReport only. Re-run with --apply to delete the files listed above.")
        return 0

    failed = 0
    for blobs in orphans.values():
        for blob in blobs:
            try:
                blob.delete()
            except Exception as exc:  # keep going; report at the end
                failed += 1
                print(f"  failed: {blob.name}: {exc}", file=sys.stderr)
    deleted = sum(len(b) for b in orphans.values()) - failed
    print(f"\nDeleted {deleted} files, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
