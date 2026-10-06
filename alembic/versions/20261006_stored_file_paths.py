"""stored_file_paths — file columns hold storage paths, not download URLs

A Firebase download URL carries a token that opens the file for anyone holding it, with no
sign-in and no expiry (PLATFORM.md §18). Both apps now read files through the SDK as the
signed-in user and upload as a bare path, so each stored URL is rewritten to the path it
points at: `{entity_type}/{owner_id}/{uuid}/{filename}`. The tokens themselves live in the
bucket and are revoked separately, by ops/revoke_download_tokens.py.

Anything that is not a download URL (a path already, a house preset) is left alone.

Revision ID: 070
Revises: 069
Create Date: 2026-10-06

"""
from typing import Sequence, Union
from urllib.parse import unquote, urlparse

import sqlalchemy as sa
from alembic import op

revision: str = "070"
down_revision: Union[str, None] = "069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOWNLOAD_URL = "https://firebasestorage.googleapis.com/%"

_COLUMNS = [
    ("properties", "image_url"),
    ("properties", "basic_contract_url"),
    ("properties", "land_registry_url"),
    ("renters", "full_contract_url"),
    ("renters", "id_image_url"),
    ("transactions", "receipt_image_url"),
    ("property_files", "url"),
    ("document_extraction_logs", "contract_url"),
]


def _path_of(url: str) -> str | None:
    # https://firebasestorage.googleapis.com/v0/b/{bucket}/o/{encoded%2Fpath}?alt=media&token=...
    path = urlparse(url).path
    if "/o/" not in path:
        return None
    return unquote(path.split("/o/", 1)[1]) or None


def upgrade() -> None:
    conn = op.get_bind()
    for table, column in _COLUMNS:
        rows = conn.execute(
            sa.text(f"SELECT id, {column} FROM {table} WHERE {column} LIKE :prefix"),
            {"prefix": _DOWNLOAD_URL},
        ).all()
        for row_id, url in rows:
            path = _path_of(url)
            if path is None:
                # Not one we can read, and still a live link: fail the deploy rather than
                # leave it behind for the token revocation to break silently.
                raise RuntimeError(f"{table}.{column} id={row_id}: unparseable download URL")
            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = :path WHERE id = :id"),
                {"path": path, "id": row_id},
            )


def downgrade() -> None:
    # The URLs cannot be rebuilt: their tokens are revoked once this has run.
    pass
