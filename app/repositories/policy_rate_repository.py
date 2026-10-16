from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.policy_rate import PolicyRate


class PolicyRateRepository:
    def __init__(self, session: Session):
        self.session = session

    def latest(self, country: str) -> PolicyRate | None:
        """The rate in force now: the newest change stored."""
        stmt = (
            select(PolicyRate)
            .where(PolicyRate.country == country)
            .order_by(PolicyRate.effective_date.desc())
            .limit(1)
        )
        return self.session.scalar(stmt)

    def before(self, country: str, day: date) -> PolicyRate | None:
        """The change in force the day before `day` — what a change on `day` replaced."""
        stmt = (
            select(PolicyRate)
            .where(PolicyRate.country == country, PolicyRate.effective_date < day)
            .order_by(PolicyRate.effective_date.desc())
            .limit(1)
        )
        return self.session.scalar(stmt)

    def add_all(self, country: str, changes: list[tuple[date, float]]) -> int:
        if not changes:
            return 0
        self.session.add_all(
            PolicyRate(country=country, effective_date=d, rate=r) for d, r in changes
        )
        self.session.commit()
        return len(changes)
