from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tax_year_input import TaxYearInput


class TaxYearInputRepository:
    def __init__(self, session: Session):
        self.session = session

    def for_year(self, owner_id: str, year: int) -> dict[str, TaxYearInput]:
        stmt = select(TaxYearInput).where(
            TaxYearInput.owner_id == owner_id, TaxYearInput.year == year
        )
        return {row.group_key: row for row in self.session.scalars(stmt)}

    def get_all(self, owner_id: str) -> list[TaxYearInput]:
        stmt = (
            select(TaxYearInput)
            .where(TaxYearInput.owner_id == owner_id)
            .order_by(TaxYearInput.year, TaxYearInput.group_key)
        )
        return list(self.session.scalars(stmt).all())

    def save(
        self,
        owner_id: str,
        group_key: str,
        year: int,
        other_income: float | None,
        credit_points: float | None,
    ) -> TaxYearInput:
        row = self.for_year(owner_id, year).get(group_key) or TaxYearInput(
            owner_id=owner_id, group_key=group_key, year=year
        )
        row.other_income = other_income
        row.credit_points = credit_points
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def delete_for_group(self, owner_id: str, group_key: str) -> None:
        self.session.execute(
            delete(TaxYearInput).where(
                TaxYearInput.owner_id == owner_id, TaxYearInput.group_key == group_key
            )
        )
        self.session.commit()
