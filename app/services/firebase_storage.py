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
    from app.config import settings
    import firebase_admin
    from firebase_admin import credentials, storage

    bucket_name = getattr(settings, "FIREBASE_STORAGE_BUCKET", None)
    if not bucket_name:
        return None

    try:
        app = firebase_admin.get_app()
    except ValueError:
        cred = credentials.ApplicationDefault()
        app = firebase_admin.initialize_app(cred, {"storageBucket": bucket_name})

    return storage.bucket(app=app)


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
                logger.warning("Could not parse Storage path from URL: %s", url)
                continue
            try:
                bucket.blob(path).delete()
                logger.info("Deleted Storage file: %s", path)
            except Exception as exc:
                logger.warning("Failed to delete Storage file %s: %s", path, exc)

    except Exception as exc:
        logger.warning("Firebase Storage cleanup failed: %s", exc)
