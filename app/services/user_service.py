import hashlib
import logging

import sentry_sdk
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog
from app.models.deleted_account import DeletedAccount
from app.models.device_token import DeviceToken
from app.models.document_extraction_log import DocumentExtractionLog
from app.models.expense_category import ExpenseCategory
from app.models.notification import Notification
from app.models.notification_rule import NotificationRule
from app.models.notification_settings import NotificationSettings
from app.models.owner import Owner
from app.models.property import Property
from app.models.renter import Renter
from app.models.report_export import ReportExport
from app.models.supplier import Supplier
from app.models.transaction import Transaction
from app.repositories.agent_repository import AgentRepository

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, db: Session):
        self.db = db

    def delete_account(self, owner_id: str) -> None:
        """Erase everything belonging to owner_id, then attempt Firebase Storage cleanup.

        Deletion is scoped by `owner_id`, not by property. Scoping to the owner's properties
        used to miss renters and transactions that were never attached to one, which then
        survived the deletion of the account they belonged to.

        A single anonymous row is left behind — see `DeletedAccount`.
        """
        counts = self._count_owner_data(owner_id)

        # Transactions and renters carry their own owner_id, so this reaches the unattached
        # ones as well as those hanging off a property.
        self.db.execute(delete(Transaction).where(Transaction.owner_id == owner_id))
        self.db.execute(delete(Renter).where(Renter.owner_id == owner_id))
        self.db.execute(delete(Supplier).where(Supplier.owner_id == owner_id))
        self.db.execute(delete(ExpenseCategory).where(ExpenseCategory.owner_id == owner_id))
        self.db.execute(delete(Property).where(Property.owner_id == owner_id))

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

        # Chat-agent data: conversations, messages, usage logs. Portfolio PII is stored
        # verbatim in agent_messages, so this must go too.
        AgentRepository(self.db).delete_owner_data(owner_id)

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
            from app.services.firebase_storage import _get_bucket
            bucket = _get_bucket()
            if bucket is None:
                logger.info("FIREBASE_STORAGE_BUCKET not set — skipping Storage cleanup for %s", owner_id)
                return
            blobs = list(bucket.list_blobs(prefix=f"{owner_id}/"))
            for blob in blobs:
                blob.delete()
            logger.info("Deleted %d Storage files for user %s", len(blobs), owner_id)
        except Exception as exc:
            logger.warning("Firebase Storage cleanup failed for %s: %s", owner_id, exc)
            # Swallowed so a Storage outage cannot block the erasure of the DB rows —
            # but this is the account-deletion path, so "the files may still be there"
            # has to be answerable later. The log line alone is not enough.
            sentry_sdk.capture_exception(exc)
