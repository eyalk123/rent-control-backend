import hashlib
import logging

import sentry_sdk
from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog
from app.models.deleted_account import DeletedAccount
from app.models.device_token import DeviceToken
from app.models.document_extraction_log import DocumentExtractionLog
from app.models.expense_category import ExpenseCategory
from app.models.legal_acceptance import LegalAcceptance
from app.models.notification import Notification
from app.models.notification_rule import NotificationRule
from app.models.notification_settings import NotificationSettings
from app.models.owner import Owner
from app.models.owner_client_day import OwnerClientDay
from app.models.property import Property
from app.models.property_owner import PropertyOwner
from app.models.tax_group import TaxGroup
from app.models.tax_year_input import TaxYearInput
from app.models.renter import Renter
from app.models.report_export import ReportExport
from app.models.supplier import Supplier
from app.models.support_message import SupportMessage
from app.models.transaction import Transaction
from app.repositories.agent_repository import AgentRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services import paddle_api
from app.services.firebase_auth import delete_auth_user

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, db: Session):
        self.db = db

    def delete_account(self, owner_id: str) -> None:
        """Erase everything belonging to owner_id, attempt Firebase Storage cleanup, then
        delete the Firebase Auth user.

        Deletion is scoped by `owner_id`, not by property. Scoping to the owner's properties
        used to miss renters and transactions that were never attached to one, which then
        survived the deletion of the account they belonged to.

        A single anonymous row is left behind — see `DeletedAccount`.

        A web (Paddle) subscription is cancelled first, and a failure to cancel stops the
        deletion: an account erased while Paddle keeps charging for it is the worst
        outcome available, and the landlord can simply try again. App Store and Play
        subscriptions cannot be cancelled by us; the clients tell the landlord so before
        they confirm.

        The `subscriptions` row is deliberately kept. It holds no name or contact
        detail — an account id, a store purchase id, a plan and a price — and it is what
        lets a subscription that is still being paid for follow the landlord to a new
        account (`RevenueCatService._transfer`).
        """
        self._cancel_web_subscription(owner_id)
        counts = self._count_owner_data(owner_id)

        # Transactions and renters carry their own owner_id, so this reaches the unattached
        # ones as well as those hanging off a property.
        self.db.execute(delete(Transaction).where(Transaction.owner_id == owner_id))
        self.db.execute(delete(Renter).where(Renter.owner_id == owner_id))
        self.db.execute(delete(Supplier).where(Supplier.owner_id == owner_id))
        self.db.execute(delete(ExpenseCategory).where(ExpenseCategory.owner_id == owner_id))
        self.db.execute(delete(Property).where(Property.owner_id == owner_id))
        self.db.execute(delete(PropertyOwner).where(PropertyOwner.owner_id == owner_id))
        self.db.execute(delete(TaxGroup).where(TaxGroup.owner_id == owner_id))
        self.db.execute(delete(TaxYearInput).where(TaxYearInput.owner_id == owner_id))

        # Notifications and the rules that generate them, plus the devices they are pushed to.
        self.db.execute(delete(Notification).where(Notification.owner_id == owner_id))
        self.db.execute(delete(NotificationRule).where(NotificationRule.owner_id == owner_id))
        self.db.execute(
            delete(NotificationSettings).where(NotificationSettings.owner_id == owner_id)
        )
        self.db.execute(delete(DeviceToken).where(DeviceToken.owner_id == owner_id))

        # Report export history and lease-extraction logs.
        self.db.execute(delete(ReportExport).where(ReportExport.owner_id == owner_id))
        self.db.execute(
            delete(DocumentExtractionLog).where(DocumentExtractionLog.owner_id == owner_id)
        )

        # The activity log's labels are renter names and property addresses — personal data,
        # so it cannot outlive the account it describes.
        self.db.execute(delete(ActivityLog).where(ActivityLog.owner_id == owner_id))

        # The record of which terms they accepted goes too. It is owner-scoped personal
        # data like everything else here, and once the account is erased there is nothing
        # left for it to evidence.
        self.db.execute(delete(LegalAcceptance).where(LegalAcceptance.owner_id == owner_id))

        # Which client they worked in, per day. Counts only, no PII — but it is
        # owner-scoped and must not outlive the account any more than the rest of this does.
        self.db.execute(
            delete(OwnerClientDay).where(OwnerClientDay.owner_id == owner_id)
        )

        # Chat-agent data: conversations, messages, usage logs. Portfolio PII is stored
        # verbatim in agent_messages, so this must go too.
        AgentRepository(self.db).delete_owner_data(owner_id)

        self.db.execute(delete(SupportMessage).where(SupportMessage.owner_id == owner_id))
        self.db.execute(delete(Owner).where(Owner.id == owner_id))

        # What survives: counts and a one-way hash. Nothing that identifies anyone.
        self.db.add(
            DeletedAccount(
                owner_id_hash=hashlib.sha256(owner_id.encode("utf-8")).hexdigest(),
                properties_count=counts["properties"],
                renters_count=counts["renters"],
                transactions_count=counts["transactions"],
            )
        )

        self.db.commit()

        # Firebase Storage cleanup (optional — requires FIREBASE_STORAGE_BUCKET env var)
        self._delete_firebase_storage(owner_id)

        # Last, and allowed to raise: the sign-in goes only once the data is gone, so a
        # failure here leaves an empty account the user can sign back into and delete again,
        # never data with no way left to reach it.
        delete_auth_user(owner_id)

    def _cancel_web_subscription(self, owner_id: str) -> None:
        subscription = SubscriptionRepository(self.db).get_for_owner(owner_id)
        if (
            subscription is None
            or subscription.source != "paddle"
            or subscription.status == "expired"
            or not paddle_api.is_subscription_id(subscription.external_id)
        ):
            return
        try:
            # Paddle's state, not ours: the row can lag a webhook, and cancelling a
            # subscription Paddle already ended is an error that would block deletion.
            current = paddle_api.get_subscription(subscription.external_id)
            if current.get("status") == "canceled":
                return
            paddle_api.cancel_now(subscription.external_id)
        except (paddle_api.NotConfigured, paddle_api.PaddleError) as exc:
            sentry_sdk.capture_exception(exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="subscription_cancel_failed",
            ) from exc
        logger.info("Cancelled web subscription for owner %s before deletion", owner_id)

    def _count_owner_data(self, owner_id: str) -> dict[str, int]:
        """Volume of the portfolio, read before it is deleted, for the tombstone."""
        def count(model) -> int:
            return int(
                self.db.scalar(
                    select(func.count()).select_from(model).where(model.owner_id == owner_id)
                )
                or 0
            )

        return {
            "properties": count(Property),
            "renters": count(Renter),
            "transactions": count(Transaction),
        }

    def _delete_firebase_storage(self, owner_id: str) -> None:
        try:
            from app.services.firebase_storage import _get_bucket, owner_blobs
            bucket = _get_bucket()
            if bucket is None:
                logger.info("FIREBASE_STORAGE_BUCKET not set — skipping Storage cleanup for %s", owner_id)
                return
            blobs = owner_blobs(bucket, owner_id)
            for blob in blobs:
                blob.delete()
            logger.info("Deleted %d Storage files for user %s", len(blobs), owner_id)
        except Exception as exc:
            logger.warning("Firebase Storage cleanup failed for %s: %s", owner_id, exc)
            # Swallowed so a Storage outage cannot block the erasure of the DB rows —
            # but this is the account-deletion path, so "the files may still be there"
            # has to be answerable later. The log line alone is not enough.
            sentry_sdk.capture_exception(exc)
