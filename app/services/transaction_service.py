from datetime import date
from decimal import Decimal

from fastapi import HTTPException

from app.config import settings
from app.models.transaction import PaymentMethodEnum, Transaction, TransactionTypeEnum
from app.repositories.expense_category_repository import ExpenseCategoryRepository
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.repositories.supplier_repository import SupplierRepository
from app.repositories.transaction_repository import TransactionRepository
from app.schemas.transaction import (
    PaymentMethod,
    TransactionCreateExpense,
    TransactionCreateRevenue,
    TransactionRead,
    TransactionType,
)


class TransactionService:
    def __init__(
        self,
        transaction_repository: TransactionRepository,
        property_repository: PropertyRepository,
        renter_repository: RenterRepository,
        expense_category_repository: ExpenseCategoryRepository,
        supplier_repository: SupplierRepository,
    ):
        self.transaction_repository = transaction_repository
        self.property_repository = property_repository
        self.renter_repository = renter_repository
        self.expense_category_repository = expense_category_repository
        self.supplier_repository = supplier_repository

    def _transaction_to_read(self, t: Transaction) -> TransactionRead:
        property_name = None
        if t.property:
            property_name = f"{t.property.address}, {t.property.city}" if t.property.city else t.property.address
        renter_name = None
        if t.renter:
            renter_name = f"{t.renter.first_name} {t.renter.last_name}".strip()
        category_name = (t.category.key or t.category.name) if t.category else None
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
            category_id=t.category_id,
            supplier_id=t.supplier_id,
            notes=t.notes,
            created_at=t.created_at,
            updated_at=t.updated_at,
            property_name=property_name,
            renter_name=renter_name,
            category_name=category_name,
            supplier_name=supplier_name,
        )

    def list_transactions(
        self,
        owner_id: int,
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

    def get_transaction(self, transaction_id: int, owner_id: int) -> TransactionRead | None:
        t = self.transaction_repository.get_by_id(transaction_id, owner_id)
        if t is None:
            return None
        return self._transaction_to_read(t)

    def create_revenue(self, data: TransactionCreateRevenue, owner_id: int) -> TransactionRead:
        property = self.property_repository.get_by_id(data.property_id, owner_id)
        if property is None:
            raise HTTPException(status_code=404, detail="Property not found")
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
        transaction = Transaction(
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
        )
        created = self.transaction_repository.create(transaction)
        return self.get_transaction(created.id, owner_id)

    def create_expense(self, data: TransactionCreateExpense, owner_id: int) -> TransactionRead:
        property = self.property_repository.get_by_id(data.property_id, owner_id)
        if property is None:
            raise HTTPException(status_code=404, detail="Property not found")
        category = self.expense_category_repository.get_by_id(data.category_id)
        if category is None:
            raise HTTPException(status_code=400, detail="Expense category not found")
        if data.supplier_id is not None:
            supplier = self.supplier_repository.get_by_id(data.supplier_id, owner_id)
            if supplier is None:
                raise HTTPException(status_code=400, detail="Supplier not found")
            if not supplier.is_active:
                raise HTTPException(status_code=400, detail="Supplier is inactive")
            category_ids = [c.id for c in supplier.categories]
            if data.category_id not in category_ids:
                raise HTTPException(
                    status_code=400,
                    detail="Supplier does not belong to the selected category",
                )
        currency_code = property.currency_code or settings.DEFAULT_CURRENCY
        transaction = Transaction(
            type=TransactionTypeEnum.EXPENSE,
            property_id=data.property_id,
            renter_id=data.renter_id,
            payment_method=PaymentMethodEnum(data.payment_method.value),
            date_of_payment=data.date_of_payment,
            month_for=None,
            amount=Decimal(str(data.amount)),
            currency_code=currency_code,
            category_id=data.category_id,
            supplier_id=data.supplier_id,
            notes=data.notes,
        )
        created = self.transaction_repository.create(transaction)
        return self.get_transaction(created.id, owner_id)
