"""The in-app "report a bug / ask a question / suggest something" endpoint.

One route. It writes the row, then emails the product owner, and reports the send
honestly: there is no admin screen behind this, so a failure that returned 201
would be a message the sender believes we have and nobody ever reads.
"""
import logging
from datetime import timedelta
from typing import Annotated

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import (
    get_current_user,
    get_owner_repository,
    get_support_message_repository,
)
from app.clock import utc_now_naive
from app.config import settings
from app.repositories.owner_repository import OwnerRepository
from app.repositories.support_message_repository import SupportMessageRepository
from app.schemas.support_message import SupportMessageCreate, SupportMessageRead
from app.services.client_usage_service import (
    CLIENT_APP_HEADER,
    CLIENT_PLATFORM_HEADER,
    CLIENT_VERSION_HEADER,
)
from app.services.support_message_service import SupportEmailError, send_support_email

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", response_model=SupportMessageRead, status_code=201)
def create_support_message(
    data: SupportMessageCreate,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    repository: Annotated[SupportMessageRepository, Depends(get_support_message_repository)],
    owner_repository: Annotated[OwnerRepository, Depends(get_owner_repository)],
):
    """Record a support submission and email it to the product owner."""
    owner_id = current_user["user_id"]

    since = utc_now_naive() - timedelta(hours=1)
    if repository.count_since(owner_id, since) >= settings.SUPPORT_MESSAGE_HOURLY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many messages sent recently. Please try again later.",
            headers={"Retry-After": "3600"},
        )

    owner = owner_repository.get(owner_id)

    # The client identification headers are already sent on every request for the
    # client-usage middleware, so the body never repeats what the transport knows.
    entry = repository.create(
        owner_id=owner_id,
        type_=data.type,
        message=data.message,
        screenshot_count=len(data.screenshots),
        client_app=request.headers.get(CLIENT_APP_HEADER),
        client_platform=request.headers.get(CLIENT_PLATFORM_HEADER),
        client_version=request.headers.get(CLIENT_VERSION_HEADER),
        language=owner.language if owner else None,
        country=owner.country if owner else None,
    )

    try:
        send_support_email(
            entry,
            submitter_name=(owner.display_name if owner else None) or current_user.get("name"),
            submitter_email=(owner.email if owner else None) or current_user.get("email"),
            screenshots=data.screenshots,
        )
    except SupportEmailError as exc:
        # The row is committed and keeps the text, so nothing is lost on our side —
        # but the sender is told, because they are the only one who will retry.
        sentry_sdk.capture_exception(exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't send your message right now. Please try again.",
        ) from exc

    return repository.mark_email_sent(entry)
