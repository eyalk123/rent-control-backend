"""Billing webhooks.

Mounted without the shared auth dependency: RevenueCat has no Firebase user and carries
its own proof in a signature header. The guard is declared on the router rather than on
the endpoint, following `internal.py` — mounted from anywhere, a test app or a second
mount, every route in this file stays verified, and there is no way to add one here that
quietly isn't.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories.subscription_event_repository import SubscriptionEventRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.revenuecat_service import (
    RevenueCatService,
    parse_body,
    verify_request,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/revenuecat")
async def revenuecat_webhook(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    x_revenuecat_webhook_signature: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
):
    """Ingest one RevenueCat event.

    The body is read as **raw bytes and verified before anything parses it**. Declaring a
    Pydantic model as the parameter instead would hand FastAPI a parsed object and leave
    the signature computed over bytes we never received — the single most common way to
    lose a day to webhook signing.

    Always answers 200 once the signature checks out, including for events deliberately
    ignored. RevenueCat retries anything that is not a 200, up to five times, and retrying
    an event correctly identified as meaningless just repeats the decision.
    """
    raw = await request.body()
    verify_request(x_revenuecat_webhook_signature, authorization, raw)

    body = parse_body(raw)
    service = RevenueCatService(
        SubscriptionEventRepository(db), SubscriptionRepository(db)
    )
    result = service.ingest(body, raw.decode("utf-8", errors="replace"))

    if result.applied != "applied":
        # Not an error, but the reason an account's access did not change is worth
        # finding in a log later. Owner id only — never the payload, which names prices
        # and store identifiers.
        logger.info(
            "RevenueCat event not applied (%s: %s) for owner %s",
            result.applied,
            result.detail,
            result.owner_id,
        )
    return {"status": result.applied}
