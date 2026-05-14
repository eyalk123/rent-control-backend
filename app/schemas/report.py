from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class MonthCell(BaseModel):
    revenue: Decimal = Decimal("0")
    expenses: Decimal = Decimal("0")
    net: Decimal = Decimal("0")


class PropertyIncomeData(BaseModel):
    property_address: str
    months: dict[int, MonthCell]  # key: 1-12
    total: MonthCell


class OwnerIncomeData(BaseModel):
    owner_name: str  # empty string if no property_owner
    properties: list[PropertyIncomeData]
    total: MonthCell


class IncomeExpenseReportResponse(BaseModel):
    year: int
    owners: list[OwnerIncomeData]
    grand_total: MonthCell


class ExpenseLogRow(BaseModel):
    date: str
    property_address: str
    property_owner: str
    category_name: str
    supplier_name: str
    payment_method: str
    amount: Decimal
    notes: str


class PivotCell(BaseModel):
    amount: Decimal = Decimal("0")


class PropertyPivotData(BaseModel):
    property_address: str
    categories: dict[str, Decimal]  # category_name → sum
    total: Decimal


class OwnerPivotData(BaseModel):
    owner_name: str
    properties: list[PropertyPivotData]
    categories: dict[str, Decimal]
    total: Decimal


class ExpenseLogReportResponse(BaseModel):
    year: int
    rows: list[ExpenseLogRow]
    owners: list[OwnerPivotData]
    categories: list[str]  # ordered list of all category names
    grand_total_by_category: dict[str, Decimal]
    grand_total: Decimal


class ReportExportRead(BaseModel):
    id: int
    report_type: str
    year: int
    format: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
