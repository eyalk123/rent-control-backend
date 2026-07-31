from datetime import date
from decimal import Decimal

from fastapi import HTTPException

from app.config import settings
from app.models.transaction import PaymentMethodEnum, Transaction, TransactionTypeEnum
from app.repositories.activity_log_repository import ActivityLogRepository
from app.repositories.expense_category_repository import ExpenseCategoryRepository
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.repositories.supplier_repository import SupplierRepository
from app.repositories.transaction_repository import TransactionRepository
from app.schemas.transaction import (
    MonthSummaryItem,
    PaymentMethod,
    TransactionCreateExpense,
    TransactionCreateRevenue,
    TransactionRead,
    TransactionSummaryResponse,
    TransactionType,
    TransactionUpdateExpense,
    TransactionUpdateRevenue,
)


class TransactionService:
    def __init__(
        self,
        transaction_repository: TransactionRepository,
        property_repository: PropertyRepository,
        renter_repository: RenterRepository,
        expense_category_repository: ExpenseCategoryRepository,
        supplier_repository: SupplierRepository,
        activity_log_repository: ActivityLogRepository | None = None,
    ):
        self.activity_log_repository = activity_log_repository
        self.transaction_repository = transaction_repository
        self.property_repository = property_repository
        self.renter_repository = renter_repository
        self.expense_category_repository = expense_category_repository
        self.supplier_repository = supplier_repository

    def _transaction_to_read(self, t: Transaction) -> TransactionRead:
        if t.property:
            property_name = f"{t.property.address}, {t.property.city}" if t.property.city else t.property.address
        else:
            property_name = t.property_address
        renter_name = (
            f"{t.renter.first_name} {t.renter.last_name}".strip()
            if t.renter
            else t.renter_name
        )
        category_ids = [c.id for c in t.categories] if t.categories else []
        category_id = category_ids[0] if category_ids else t.category_id
        first_cat = t.categories[0] if t.categories else t.category
        category_name = (first_cat.key or first_cat.name) if first_cat else None
        supplier_name = t.supplier.name if t.supplier else None
        return TransactionRead(
            id=t.id,
            type=TransactionType(t.type.value),
            property_id=t.property_id,
            renter_id=t.renter_id,
            payment_method=PaymentMethod(t.payment_method.value) if t.payment_method else None,
            date_of_payment=t.date_of_payment,
            month_for=t.month_for,
            amount=t.amount,
            currency_code=t.currency_code,
            category_id=category_id,
            category_ids=category_ids,
            supplier_id=t.supplier_id,
            notes=t.notes,
            receipt_image_url=t.receipt_image_url,
            created_at=t.created_at,
            updated_at=t.updated_at,
            property_name=property_name,
            renter_name=renter_name,
            category_name=category_name,
            supplier_name=supplier_name,
        )

    def list_transactions(
        self,
        owner_id: str,
        type_filter: str | None = None,
        property_id: int | None = None,
        renter_id: int | None = None,
        q: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TransactionRead]:
        type_enum = None
        if type_filter is not None:
            if type_filter not in ("revenue", "expense"):
                return []
            type_enum = TransactionTypeEnum(type_filter)
        rows = self.transaction_repository.list(
            owner_id=owner_id,
            type_filter=type_enum,
            property_id=property_id,
            renter_id=renter_id,
            q=q,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
        )
        return [self._transaction_to_read(t) for t in rows]

    def get_transaction(self, transaction_id: int, owner_id: str) -> TransactionRead | None:
        t = self.transaction_repository.get_by_id(transaction_id, owner_id)
        if t is None:
            return None
        return self._transaction_to_read(t)

    def create_revenue(self, data: TransactionCreateRevenue, owner_id: str) -> TransactionRead:
        property = self.property_repository.get_by_id(data.property_id, owner_id)
        if property is None:
            raise HTTPException(status_code=404, detail="Property not found")
        renter = None
        if data.renter_id is not None:
            renter = self.renter_repository.get_by_id(data.renter_id)
            if renter is None or renter.property_id != data.property_id:
                raise HTTPException(
                    status_code=400,
                    detail="Renter not found or does not belong to the selected property",
                )
        currency_code = property.currency_code or settings.DEFAULT_CURRENCY
        date_of_payment = data.date_of_payment or date.today()
        payment_method = (
            PaymentMethodEnum(data.payment_method.value) if data.payment_method else None
        )
        property_address = f"{property.address}, {property.city}" if property.city else property.address
        renter_name_snap = f"{renter.first_name} {renter.last_name}".strip() if renter else None
        transaction = Transaction(
            owner_id=owner_id,
            type=TransactionTypeEnum.REVENUE,
            property_id=data.property_id,
            renter_id=data.renter_id,
            payment_method=payment_method,
            date_of_payment=date_of_payment,
            month_for=data.month_for,
            amount=Decimal(str(data.amount)),
            currency_code=currency_code,
            category_id=None,
            supplier_id=None,
            notes=data.notes,
            property_address=property_address,
            renter_name=renter_name_snap,
        )
        created = self.transaction_repository.create(transaction)
        return self.get_transaction(created.id, owner_id)

    def get_summary(self, owner_id: str) -> TransactionSummaryResponse:
        today = date.today()
        # First day of the month 5 months ago (covers 6 months total including current)
        year, month = today.year, today.month - 5
        if month <= 0:
            month += 12
            year -= 1
        from_date = date(year, month, 1)

        rows = self.transaction_repository.get_monthly_summary(owner_id, from_date)
        by_key = {
            f"{int(row.year):04d}-{int(row.month):02d}": row
            for row in rows
        }

        buckets: list[MonthSummaryItem] = []
        for i in range(5, -1, -1):
            y, m = today.year, today.month - i
            if m <= 0:
                m += 12
                y -= 1
            key = f"{y:04d}-{m:02d}"
            row = by_key.get(key)
            revenue = float(row.revenue) if row else 0.0
            expenses = float(row.expenses) if row else 0.0
            buckets.append(MonthSummaryItem(
                key=key,
                year=y,
                month=m,
                revenue=revenue,
                expenses=expenses,
                profit=revenue - expenses,
            ))

        return TransactionSummaryResponse(six_month_buckets=buckets)

    def update_revenue(self, transaction_id: int, data: TransactionUpdateRevenue, owner_id: str) -> TransactionRead | None:
        fields: dict = {}
        if data.property_id is not None:
            property = self.property_repository.get_by_id(data.property_id, owner_id)
            if property is None:
                raise HTTPException(status_code=404, detail="Property not found")
            fields["property_id"] = data.property_id
            fields["property_address"] = (
                f"{property.address}, {property.city}" if property.city else property.address
            )
        if data.renter_id is not None:
            renter = self.renter_repository.get_by_id(data.renter_id)
            if renter is None:
                raise HTTPException(status_code=400, detail="Renter not found")
            fields["renter_id"] = data.renter_id
            fields["renter_name"] = f"{renter.first_name} {renter.last_name}".strip()
        elif "renter_id" in data.model_fields_set and data.renter_id is None:
            fields["renter_id"] = None
            fields["renter_name"] = None
        if data.amount is not None:
            fields["amount"] = Decimal(str(data.amount))
        if data.date_of_payment is not None:
            fields["date_of_payment"] = data.date_of_payment
        if data.month_for is not None:
            fields["month_for"] = data.month_for
        if data.payment_method is not None:
            fields["payment_method"] = PaymentMethodEnum(data.payment_method.value)
        elif "payment_method" in data.model_fields_set and data.payment_method is None:
            fields["payment_method"] = None
        if "notes" in data.model_fields_set:
            fields["notes"] = data.notes
        updated = self.transaction_repository.update(transaction_id, owner_id, fields)
        if updated is None:
            return None
        return self._transaction_to_read(updated)

    def update_expense(self, transaction_id: int, data: TransactionUpdateExpense, owner_id: str) -> TransactionRead | None:
        fields: dict = {}
        new_categories = None
        if data.property_id is not None:
            property = self.property_repository.get_by_id(data.property_id, owner_id)
            if property is None:
                raise HTTPException(status_code=404, detail="Property not found")
            fields["property_id"] = data.property_id
            fields["property_address"] = (
                f"{property.address}, {property.city}" if property.city else property.address
            )
        if data.category_ids is not None:
            if len(data.category_ids) == 0:
                raise HTTPException(status_code=400, detail="At least one category is required")
            category_objects = []
            for cid in data.category_ids:
                cat = self.expense_category_repository.get_by_id(cid)
                if cat is None:
                    raise HTTPException(status_code=400, detail=f"Expense category {cid} not found")
                category_objects.append(cat)
            new_categories = category_objects
            fields["category_id"] = data.category_ids[0]
        if data.supplier_id is not None:
            supplier = self.supplier_repository.get_by_id(data.supplier_id, owner_id)
            if supplier is None:
                raise HTTPException(status_code=400, detail="Supplier not found")
            fields["supplier_id"] = data.supplier_id
        elif "supplier_id" in data.model_fields_set and data.supplier_id is None:
            fields["supplier_id"] = None
        if data.renter_id is not None:
            renter = self.renter_repository.get_by_id(data.renter_id)
            if renter is None:
                raise HTTPException(status_code=400, detail="Renter not found")
            fields["renter_id"] = data.renter_id
            fields["renter_name"] = f"{renter.first_name} {renter.last_name}".strip()
        elif "renter_id" in data.model_fields_set and data.renter_id is None:
            fields["renter_id"] = None
            fields["renter_name"] = None
        if data.amount is not None:
            fields["amount"] = Decimal(str(data.amount))
        if data.date_of_payment is not None:
            fields["date_of_payment"] = data.date_of_payment
        if data.payment_method is not None:
            fields["payment_method"] = PaymentMethodEnum(data.payment_method.value)
        if "notes" in data.model_fields_set:
            fields["notes"] = data.notes
        if "receipt_image_url" in data.model_fields_set:
            fields["receipt_image_url"] = data.receipt_image_url
        updated = self.transaction_repository.update(transaction_id, owner_id, fields, new_categories=new_categories)
        if updated is None:
            return None
        return self._transaction_to_read(updated)

    def delete_transaction(self, transaction_id: int, owner_id: str) -> bool:
        if self.activity_log_repository is not None:
            # Read it first: the repository deletes and commits, after which there is
            # nothing left to describe.
            transaction = self.transaction_repository.get_by_id(transaction_id, owner_id)
            if transaction is not None:
                self.activity_log_repository.record_delete(
                    owner_id=owner_id,
                    entity_type="transaction",
                    entity_id=transaction.id,
                    label=transaction.property_address,
                    details={
                        "type": transaction.type.value if transaction.type else None,
                        "amount": str(transaction.amount),
                        "date_of_payment": (
                            transaction.date_of_payment.isoformat()
                            if transaction.date_of_payment
                            else None
                        ),
                        "renter_name": transaction.renter_name,
                    },
                )
        return self.transaction_repository.delete(transaction_id, owner_id)

    def create_expense(self, data: TransactionCreateExpense, owner_id: str) -> TransactionRead:
        property = self.property_repository.get_by_id(data.property_id, owner_id)
        if property is None:
            raise HTTPException(status_code=404, detail="Property not found")
        category_objects = []
        for cid in data.category_ids:
            cat = self.expense_category_repository.get_by_id(cid)
            if cat is None:
                raise HTTPException(status_code=400, detail=f"Expense category {cid} not found")
            category_objects.append(cat)
        if data.supplier_id is not None:
            supplier = self.supplier_repository.get_by_id(data.supplier_id, owner_id)
            if supplier is None:
                raise HTTPException(status_code=400, detail="Supplier not found")
            if not supplier.is_active:
                raise HTTPException(status_code=400, detail="Supplier is inactive")
            supplier_cat_ids = [c.id for c in supplier.categories]
            if not any(cid in supplier_cat_ids for cid in data.category_ids):
                raise HTTPException(
                    status_code=400,
                    detail="Supplier does not belong to any of the selected categories",
                )
        currency_code = property.currency_code or settings.DEFAULT_CURRENCY
        property_address = f"{property.address}, {property.city}" if property.city else property.address
        renter_name_snap = None
        if data.renter_id is not None:
            renter = self.renter_repository.get_by_id(data.renter_id)
            if renter:
                renter_name_snap = f"{renter.first_name} {renter.last_name}".strip()
        transaction = Transaction(
            owner_id=owner_id,
            type=TransactionTypeEnum.EXPENSE,
            property_id=data.property_id,
            renter_id=data.renter_id,
            payment_method=PaymentMethodEnum(data.payment_method.value),
            date_of_payment=data.date_of_payment,
            month_for=None,
            amount=Decimal(str(data.amount)),
            currency_code=currency_code,
            category_id=data.category_ids[0],
            supplier_id=data.supplier_id,
            notes=data.notes,
            receipt_image_url=data.receipt_image_url,
            property_address=property_address,
            renter_name=renter_name_snap,
        )
        transaction.categories = category_objects
        created = self.transaction_repository.create(transaction)
        return self.get_transaction(created.id, owner_id)
