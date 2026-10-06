import logging
import re

logger = logging.getLogger(__name__)


# A stored file value: the bare storage path the client uploaded to (see ENTITY_TYPES below).
# Anything else in a file column — a house preset sentinel — is not one of our files.
_STORAGE_PATH = re.compile(r"^(properties|renters|transactions)/[^/]+/[^/]+/.+")


def _blob_path(value: str) -> str | None:
    """The GCS blob path a stored file value names, or None when it is not one of our files.

    Columns hold bare storage paths. They once held Firebase download URLs, whose token
    opened the file for anyone with the link; migration 069 converted them and the request
    schemas refuse new ones (PLATFORM.md §18).
    """
    return value if _STORAGE_PATH.match(value) else None


def _get_bucket():
    import json
    import firebase_admin
    from firebase_admin import credentials, storage
    from app.config import settings

    bucket_name = settings.FIREBASE_STORAGE_BUCKET
    sa_json = settings.FIREBASE_SERVICE_ACCOUNT_JSON
    if not bucket_name or not sa_json:
        return None

    try:
        app = firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(json.loads(sa_json))
        app = firebase_admin.initialize_app(cred, {"storageBucket": bucket_name})

    return storage.bucket(app=app)


# Clients upload to `{entity_type}/{owner_id}/{uuid}/{filename}` — storage.rules (in the web
# repo) is the authority on that shape. An owner's files therefore sit under one prefix per
# entity type, never under a bare `{owner_id}/`.
ENTITY_TYPES = ("properties", "renters", "transactions")


def owner_prefixes(owner_id: str) -> list[str]:
    return [f"{entity_type}/{owner_id}/" for entity_type in ENTITY_TYPES]


def owner_blobs(bucket, owner_id: str) -> list:
    """Every blob under the owner's prefixes. Raises on Storage errors — callers decide
    whether that degrades or gets reported."""
    blobs = []
    for prefix in owner_prefixes(owner_id):
        blobs.extend(bucket.list_blobs(prefix=prefix))
    return blobs


def list_owner_blobs(owner_id: str) -> list:
    """Every Storage blob the owner uploaded — the same set `user_service` deletes on
    account removal. Returns [] when Storage isn't configured or fails, so callers can
    degrade instead of failing."""
    try:
        bucket = _get_bucket()
        if bucket is None:
            logger.info("FIREBASE_STORAGE_BUCKET not set — skipping Storage file listing")
            return []
        return owner_blobs(bucket, owner_id)
    except Exception as exc:
        logger.warning("Failed to list Storage files for %s: %s", owner_id, exc)
        return []


def _referenced_urls(db, owner_id: str, urls: set[str]) -> set[str]:
    """The subset of `urls` some record of this owner still points at."""
    from sqlalchemy import select
    from app.models.property import Property
    from app.models.property_file import PropertyFile
    from app.models.renter import Renter
    from app.models.transaction import Transaction

    columns = [
        (Property, Property.image_url),
        (Property, Property.basic_contract_url),
        (Property, Property.land_registry_url),
        (Renter, Renter.full_contract_url),
        (Renter, Renter.id_image_url),
        (Transaction, Transaction.receipt_image_url),
    ]
    found: set[str] = set()
    for model, column in columns:
        found.update(
            db.scalars(select(column).where(model.owner_id == owner_id, column.in_(urls)))
        )
    found.update(
        db.scalars(
            select(PropertyFile.url)
            .join(Property, Property.id == PropertyFile.property_id)
            .where(Property.owner_id == owner_id, PropertyFile.url.in_(urls))
        )
    )
    return found


def release_file_urls(db, owner_id: str, urls: list[str | None]) -> None:
    """Delete the Storage files behind `urls` once nothing of this owner references them.

    Call after the change that dropped the reference has been committed: a failed write
    then keeps its file, and a file some other record still points at is left alone.
    Only paths under the owner's own prefixes are touched — a URL is client-supplied, and
    the Admin SDK would otherwise delete another account's file on request.
    """
    candidates = {u for u in urls if u}
    if not candidates:
        return
    try:
        candidates -= _referenced_urls(db, owner_id, candidates)
    except Exception as exc:
        logger.warning("Could not check Storage file references for %s: %s", owner_id, exc)
        return
    prefixes = tuple(owner_prefixes(owner_id))
    owned = []
    for url in candidates:
        path = _blob_path(url)
        if path and path.startswith(prefixes):
            owned.append(url)
        else:
            # Never the path: the file name may name a tenant. Logs become Sentry breadcrumbs.
            logger.warning("Not deleting a Storage file outside %s's prefixes", owner_id)
    delete_file_urls(owned)


def delete_file_urls(urls: list[str | None]) -> None:
    """Best-effort delete of the Firebase Storage blobs these stored values name."""
    valid = [u for u in urls if u]
    if not valid:
        return

    try:
        bucket = _get_bucket()
        if bucket is None:
            logger.info("FIREBASE_STORAGE_BUCKET not set — skipping Storage file cleanup")
            return

        for url in valid:
            path = _blob_path(url)
            if not path:
                logger.warning("Not deleting a stored value that is not a Storage path")
                continue
            try:
                bucket.blob(path).delete()
            except Exception as exc:
                # The type only: a GCS error message quotes the object path.
                logger.warning("Failed to delete a Storage file: %s", type(exc).__name__)

    except Exception as exc:
        logger.warning("Firebase Storage cleanup failed: %s", exc)


def download_owner_file(owner_id: str, url: str | None) -> bytes | None:
    """The bytes of one of the owner's Storage files, or None — never raises.

    Only paths under the owner's own prefixes are read, for the reason `release_file_urls`
    gives: the stored value is client-supplied, and the Admin SDK would read another
    account's file on request.
    """
    path = _blob_path(url) if url else None
    if not path or not path.startswith(tuple(owner_prefixes(owner_id))):
        return None
    try:
        bucket = _get_bucket()
        if bucket is None:
            return None
        return bucket.blob(path).download_as_bytes()
    except Exception as exc:
        # The type only: a GCS error message quotes the object path.
        logger.warning("Failed to download a Storage file: %s", type(exc).__name__)
        return None
