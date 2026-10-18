from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cpi_base_factor import CpiBaseFactor


class CpiBaseFactorRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, index_id: int, base: str) -> CpiBaseFactor | None:
        return self.session.scalar(
            select(CpiBaseFactor).where(
                CpiBaseFactor.index_id == index_id, CpiBaseFactor.base == base
            )
        )

    def add(self, **fields) -> CpiBaseFactor:
        row = CpiBaseFactor(**fields)
        self.session.add(row)
        self.session.commit()
        return row
