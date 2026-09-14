from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.device_token import DevicePlatformEnum
from app.models.legal_acceptance import LegalAcceptance, LegalDocumentEnum


class LegalAcceptanceRepository:
    def __init__(self, session: Session):
        self.session = session

    def record(
        self,
        owner_id: str,
        document: LegalDocumentEnum,
        version: str,
        locale: str,
        platform: DevicePlatformEnum,
    ) -> LegalAcceptance:
        """Insert one acceptance. Always inserts — see the model docstring for why an
        already-recorded version is not deduplicated away."""
        acceptance = LegalAcceptance(
            owner_id=owner_id,
            document=document,
            version=version,
            locale=locale,
            platform=platform,
        )
        self.session.add(acceptance)
        self.session.commit()
        self.session.refresh(acceptance)
        return acceptance

    def latest_per_document(self, owner_id: str) -> dict[LegalDocumentEnum, LegalAcceptance]:
        """The most recent acceptance of each document, keyed by document.

        Absent keys mean "never accepted", which is the honest answer for every account
        created before this table existed. Ordered oldest-first so the later row wins the
        dict slot; ``id`` breaks ties, because two acceptances written in the same gesture
        can share a timestamp.
        """
        stmt = (
            select(LegalAcceptance)
            .where(LegalAcceptance.owner_id == owner_id)
            .order_by(LegalAcceptance.accepted_at.asc(), LegalAcceptance.id.asc())
        )
        return {row.document: row for row in self.session.scalars(stmt).all()}

    def delete_owner_data(self, owner_id: str) -> None:
        self.session.execute(
            delete(LegalAcceptance).where(LegalAcceptance.owner_id == owner_id)
        )
