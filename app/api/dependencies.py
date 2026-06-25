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
from app.repositories.property_file_repository import PropertyFileRepository
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.repositories.report_export_repository import ReportExportRepository
from app.repositories.supplier_repository import SupplierRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.device_token_service import DeviceTokenService
from app.services.document_extraction_service import DocumentExtractionService
from app.services.expense_category_service import ExpenseCategoryService
from app.services.notification_engine import NotificationEngine
from app.services.notification_preferences_service import NotificationPreferencesService
from app.services.property_service import PropertyService
from app.services.push_service import PushService
from app.services.reminder_service import ReminderService
from app.services.renter_service import RenterService
from app.services.supplier_service import SupplierService
from app.services.transaction_service import TransactionService
from app.services.user_service import UserService

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

    return {"user_id": user_id, "role": "owner"}


def get_document_extraction_service() -> DocumentExtractionService:
    return DocumentExtractionService(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.EXTRACTION_MODEL,
    )


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


def get_property_service(
    property_repository: Annotated[PropertyRepository, Depends(get_property_repository)],
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
) -> PropertyService:
    return PropertyService(property_repository, renter_repository)


def get_renter_service(
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
    property_repository: Annotated[PropertyRepository, Depends(get_property_repository)],
) -> RenterService:
    return RenterService(renter_repository, property_repository)


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
) -> TransactionService:
    return TransactionService(
        transaction_repository=transaction_repository,
        property_repository=property_repository,
        renter_repository=renter_repository,
        expense_category_repository=expense_category_repository,
        supplier_repository=supplier_repository,
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
