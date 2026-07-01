import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.expense_category import ExpenseCategory
from app.models.owner import Owner
from app.models.property import Property
from app.models.renter import Renter
from app.models.supplier import Supplier
from app.models.transaction import Transaction

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, db: Session):
        self.db = db

    def delete_account(self, owner_id: str) -> None:
        """Delete all data owned by owner_id, then attempt Firebase Storage cleanup."""
        # 1. Get all property IDs for this owner (needed for scoped deletes)
        prop_ids = list(
            self.db.scalars(
                select(Property.id).where(Property.owner_id == owner_id)
            ).all()
        )

        # 2. Delete transactions linked to owner's properties
        if prop_ids:
            self.db.execute(
                delete(Transaction).where(Transaction.property_id.in_(prop_ids))
            )

        # 3. Delete renters linked to owner's properties
        if prop_ids:
            self.db.execute(
                delete(Renter).where(Renter.property_id.in_(prop_ids))
            )

        # 4. Delete suppliers
        self.db.execute(
            delete(Supplier).where(Supplier.owner_id == owner_id)
        )

        # 5. Delete expense categories
        self.db.execute(
            delete(ExpenseCategory).where(ExpenseCategory.owner_id == owner_id)
        )

        # 6. Delete properties
        self.db.execute(
            delete(Property).where(Property.owner_id == owner_id)
        )

        # 7. Delete the owner's profile row
        self.db.execute(
            delete(Owner).where(Owner.id == owner_id)
        )

        self.db.commit()

        # 8. Firebase Storage cleanup (optional — requires FIREBASE_STORAGE_BUCKET env var)
        self._delete_firebase_storage(owner_id)

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
