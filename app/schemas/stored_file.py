from typing import Annotated, Optional

from pydantic import AfterValidator

DOWNLOAD_URL_PREFIX = "https://firebasestorage.googleapis.com/"


def _not_a_download_url(value: str) -> str:
    """A stored file is saved as its bare storage path, `{entity_type}/{owner_id}/{uuid}/{name}`.

    A Firebase download URL carries a token that opens the file for anyone holding it, with no
    sign-in and no expiry — the reason they were retired (PLATFORM.md §18). Rejected rather
    than converted: a client still sending one is out of date, and should be told so.
    """
    if value.startswith(DOWNLOAD_URL_PREFIX):
        raise ValueError("send the file's storage path, not its download URL")
    return value


# A file field on a request body: a storage path or a house preset sentinel.
StoredFilePath = Annotated[str, AfterValidator(_not_a_download_url)]
# The same, where the field may be cleared.
StoredFile = Optional[StoredFilePath]
