import logging
from typing import Annotated

import requests as http_requests
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.exceptions import TransportError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.repositories.activity_log_repository import ActivityLogRepository
from app.repositories.agent_repository import AgentRepository
from app.repositories.device_token_repository import DeviceTokenRepository
from app.repositories.document_extraction_log_repository import (
    DocumentExtractionLogRepository,
)
from app.repositories.expense_category_repository import ExpenseCategoryRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.notification_rule_repository import NotificationRuleRepository
from app.repositories.notification_settings_repository import (
    NotificationSettingsRepository,
)
from app.repositories.cpi_index_repository import CpiIndexRepository
from app.repositories.owner_repository import OwnerRepository
from app.repositories.property_file_repository import PropertyFileRepository
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.repositories.report_export_repository import ReportExportRepository
from app.repositories.supplier_repository import SupplierRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.agent_service import AgentService
from app.services.boi_index_service import BoiIndexService
from app.services.cbs_index_service import CbsIndexService
from app.services.cpi_indexing_service import CpiIndexingService
from app.services.device_token_service import DeviceTokenService
from app.services.document_extraction_service import DocumentExtractionService
from app.services.expense_category_service import ExpenseCategoryService
from app.services.index_source import IndexSource
from app.services.notification_engine import NotificationEngine
from app.services.notification_preferences_service import NotificationPreferencesService
from app.services.property_service import PropertyService
from app.services.push_service import PushService
from app.services.reminder_service import ReminderService
from app.services.retention_service import RetentionService
from app.services.renter_service import RenterService
from app.services.supplier_service import SupplierService
from app.services.transaction_service import TransactionService
from app.services.user_service import UserService

logger = logging.getLogger(__name__)

_bearer = HTTPBearer()
_google_request = google_requests.Request(session=http_requests.Session())


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> dict:
    token = credentials.credentials
    try:
        payload = id_token.verify_firebase_token(
            token,
            _google_request,
            audience=settings.FIREBASE_PROJECT_ID,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    except TransportError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    user_id: str | None = payload.get("sub") or payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing uid claim")

    # These claims are already present in the verified token — no extra network call.
    return {
        "user_id": user_id,
        "role": "owner",
        "email": payload.get("email"),
        "name": payload.get("name"),
        "picture": payload.get("picture"),
    }


def get_owner_repository(db: Annotated[Session, Depends(get_db)]) -> OwnerRepository:
    return OwnerRepository(db)


def get_current_owner(
    current_user: Annotated[dict, Depends(get_current_user)],
    owner_repository: Annotated[OwnerRepository, Depends(get_owner_repository)],
) -> dict:
    """Refresh the owner's profile row from the token claims (throttled) and return the
    same ``current_user`` dict. The upsert is best-effort telemetry — a write failure must
    never break an otherwise-valid authenticated request.
    """
    try:
        owner_repository.upsert(
            uid=current_user["user_id"],
            email=current_user.get("email"),
            display_name=current_user.get("name"),
            picture_url=current_user.get("picture"),
        )
    except Exception as exc:
        logger.warning("Owner profile upsert failed for %s: %s", current_user["user_id"], exc)
    return current_user


def get_document_extraction_service() -> DocumentExtractionService:
    return DocumentExtractionService(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.EXTRACTION_MODEL,
    )


def get_agent_service(db: Annotated[Session, Depends(get_db)]) -> AgentService:
    return AgentService(db, api_key=settings.ANTHROPIC_API_KEY, model=settings.AGENT_MODEL)


def get_agent_repository(db: Annotated[Session, Depends(get_db)]) -> AgentRepository:
    """Repo-only access to agent data (no API key needed) — used by the retention cron."""
    return AgentRepository(db)


def get_document_extraction_log_repository(
    db: Annotated[Session, Depends(get_db)],
) -> DocumentExtractionLogRepository:
    return DocumentExtractionLogRepository(db)


def get_property_repository(db: Annotated[Session, Depends(get_db)]) -> PropertyRepository:
    return PropertyRepository(db)


def get_property_file_repository(db: Annotated[Session, Depends(get_db)]) -> PropertyFileRepository:
    return PropertyFileRepository(db)


def get_renter_repository(db: Annotated[Session, Depends(get_db)]) -> RenterRepository:
    return RenterRepository(db)


def get_cpi_index_repository(db: Annotated[Session, Depends(get_db)]) -> CpiIndexRepository:
    return CpiIndexRepository(db)


def get_activity_log_repository(
    db: Annotated[Session, Depends(get_db)],
) -> ActivityLogRepository:
    return ActivityLogRepository(db)


def get_retention_service(db: Annotated[Session, Depends(get_db)]) -> RetentionService:
    return RetentionService(db)


def get_property_service(
    property_repository: Annotated[PropertyRepository, Depends(get_property_repository)],
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
    activity_log_repository: Annotated[
        ActivityLogRepository, Depends(get_activity_log_repository)
    ],
) -> PropertyService:
    return PropertyService(property_repository, renter_repository, activity_log_repository)


def get_renter_service(
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
    property_repository: Annotated[PropertyRepository, Depends(get_property_repository)],
    cpi_index_repository: Annotated[CpiIndexRepository, Depends(get_cpi_index_repository)],
    activity_log_repository: Annotated[
        ActivityLogRepository, Depends(get_activity_log_repository)
    ],
) -> RenterService:
    return RenterService(
        renter_repository, property_repository, cpi_index_repository, activity_log_repository
    )


def get_cbs_index_service() -> CbsIndexService:
    return CbsIndexService(
        base_url=settings.CBS_API_BASE_URL, index_id=settings.CPI_INDEX_ID
    )


def get_boi_index_service() -> BoiIndexService:
    return BoiIndexService(
        base_url=settings.BOI_API_BASE_URL, series_code=settings.BOI_CPI_SERIES_CODE
    )


def get_index_sources(
    cbs_service: Annotated[CbsIndexService, Depends(get_cbs_index_service)],
    boi_service: Annotated[BoiIndexService, Depends(get_boi_index_service)],
) -> list[IndexSource]:
    """CPI feeds in order of authority. CBS first — it is the publisher lease contracts
    reference; BOI only answers when CBS cannot."""
    return [cbs_service, boi_service]


def get_cpi_indexing_service(
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
    cpi_index_repository: Annotated[CpiIndexRepository, Depends(get_cpi_index_repository)],
    sources: Annotated[list[IndexSource], Depends(get_index_sources)],
) -> CpiIndexingService:
    return CpiIndexingService(
        renter_repository=renter_repository,
        cpi_index_repository=cpi_index_repository,
        sources=sources,
    )


def get_expense_category_repository(
    db: Annotated[Session, Depends(get_db)],
) -> ExpenseCategoryRepository:
    return ExpenseCategoryRepository(db)


def get_supplier_repository(
    db: Annotated[Session, Depends(get_db)],
) -> SupplierRepository:
    return SupplierRepository(db)


def get_transaction_repository(
    db: Annotated[Session, Depends(get_db)],
) -> TransactionRepository:
    return TransactionRepository(db)


def get_expense_category_service(
    expense_category_repository: Annotated[
        ExpenseCategoryRepository, Depends(get_expense_category_repository)
    ],
) -> ExpenseCategoryService:
    return ExpenseCategoryService(expense_category_repository)


def get_supplier_service(
    supplier_repository: Annotated[SupplierRepository, Depends(get_supplier_repository)],
    expense_category_repository: Annotated[
        ExpenseCategoryRepository, Depends(get_expense_category_repository)
    ],
) -> SupplierService:
    return SupplierService(supplier_repository, expense_category_repository)


def get_report_export_repository(db: Annotated[Session, Depends(get_db)]) -> ReportExportRepository:
    return ReportExportRepository(db)


def get_user_service(db: Annotated[Session, Depends(get_db)]) -> UserService:
    return UserService(db)


def get_transaction_service(
    transaction_repository: Annotated[
        TransactionRepository, Depends(get_transaction_repository)
    ],
    property_repository: Annotated[PropertyRepository, Depends(get_property_repository)],
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
    expense_category_repository: Annotated[
        ExpenseCategoryRepository, Depends(get_expense_category_repository)
    ],
    supplier_repository: Annotated[SupplierRepository, Depends(get_supplier_repository)],
    activity_log_repository: Annotated[
        ActivityLogRepository, Depends(get_activity_log_repository)
    ],
) -> TransactionService:
    return TransactionService(
        transaction_repository=transaction_repository,
        property_repository=property_repository,
        renter_repository=renter_repository,
        expense_category_repository=expense_category_repository,
        supplier_repository=supplier_repository,
        activity_log_repository=activity_log_repository,
    )


def get_device_token_repository(
    db: Annotated[Session, Depends(get_db)],
) -> DeviceTokenRepository:
    return DeviceTokenRepository(db)


def get_notification_repository(
    db: Annotated[Session, Depends(get_db)],
) -> NotificationRepository:
    return NotificationRepository(db)


def get_notification_settings_repository(
    db: Annotated[Session, Depends(get_db)],
) -> NotificationSettingsRepository:
    return NotificationSettingsRepository(db)


def get_notification_rule_repository(
    db: Annotated[Session, Depends(get_db)],
) -> NotificationRuleRepository:
    return NotificationRuleRepository(db)


def get_notification_engine(
    rule_repository: Annotated[NotificationRuleRepository, Depends(get_notification_rule_repository)],
    settings_repository: Annotated[
        NotificationSettingsRepository, Depends(get_notification_settings_repository)
    ],
    renter_service: Annotated[RenterService, Depends(get_renter_service)],
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
) -> NotificationEngine:
    return NotificationEngine(
        rule_repository=rule_repository,
        settings_repository=settings_repository,
        renter_service=renter_service,
        renter_repository=renter_repository,
    )


def get_notification_preferences_service(
    rule_repository: Annotated[NotificationRuleRepository, Depends(get_notification_rule_repository)],
    settings_repository: Annotated[
        NotificationSettingsRepository, Depends(get_notification_settings_repository)
    ],
    engine: Annotated[NotificationEngine, Depends(get_notification_engine)],
) -> NotificationPreferencesService:
    return NotificationPreferencesService(
        rule_repository=rule_repository,
        settings_repository=settings_repository,
        engine=engine,
    )


def get_device_token_service(
    device_token_repository: Annotated[
        DeviceTokenRepository, Depends(get_device_token_repository)
    ],
) -> DeviceTokenService:
    return DeviceTokenService(device_token_repository)


def get_push_service(
    device_token_repository: Annotated[
        DeviceTokenRepository, Depends(get_device_token_repository)
    ],
) -> PushService:
    return PushService(device_token_repository)


def get_reminder_service(
    engine: Annotated[NotificationEngine, Depends(get_notification_engine)],
    notification_repository: Annotated[NotificationRepository, Depends(get_notification_repository)],
    settings_repository: Annotated[
        NotificationSettingsRepository, Depends(get_notification_settings_repository)
    ],
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
    push_service: Annotated[PushService, Depends(get_push_service)],
    device_token_repository: Annotated[
        DeviceTokenRepository, Depends(get_device_token_repository)
    ],
) -> ReminderService:
    return ReminderService(
        engine=engine,
        notification_repository=notification_repository,
        settings_repository=settings_repository,
        renter_repository=renter_repository,
        push_service=push_service,
        device_token_repository=device_token_repository,
    )
