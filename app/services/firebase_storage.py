import logging
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)


def _blob_path_from_url(url: str) -> str | None:
    """Extract the GCS blob path from a Firebase Storage download URL.

    Firebase download URLs look like:
    https://firebasestorage.googleapis.com/v0/b/{bucket}/o/{encoded%2Fpath}?alt=media&token=...
    """
    try:
        path = urlparse(url).path  # /v0/b/{bucket}/o/{encoded_path}
        _, encoded = path.split("/o/", 1)
        return unquote(encoded)
    except Exception:
        return None


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
        path = _blob_path_from_url(url)
        if path and path.startswith(prefixes):
            owned.append(url)
        else:
            # Never the URL: it carries a non-expiring access token, and the file name may
            # name a tenant. Logs become Sentry breadcrumbs.
            logger.warning("Not deleting a Storage file outside %s's prefixes", owner_id)
    delete_file_urls(owned)


def delete_file_urls(urls: list[str | None]) -> None:
    """Best-effort delete of Firebase Storage blobs referenced by download URLs."""
    valid = [u for u in urls if u]
    if not valid:
        return

    try:
        bucket = _get_bucket()
        if bucket is None:
            logger.info("FIREBASE_STORAGE_BUCKET not set — skipping Storage file cleanup")
            return

        for url in valid:
            path = _blob_path_from_url(url)
            if not path:
                logger.warning("Could not parse a Storage path from a download URL")
                continue
            try:
                bucket.blob(path).delete()
            except Exception as exc:
                # The type only: a GCS error message quotes the object path.
                logger.warning("Failed to delete a Storage file: %s", type(exc).__name__)

    except Exception as exc:
        logger.warning("Firebase Storage cleanup failed: %s", exc)
