import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document_extraction_log import DocumentExtractionLog


class DocumentExtractionLogRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, log: DocumentExtractionLog) -> DocumentExtractionLog:
        self.session.add(log)
        self.session.commit()
        self.session.refresh(log)
        return log

    def get_by_id(self, log_id: int, owner_id: str) -> DocumentExtractionLog | None:
        stmt = select(DocumentExtractionLog).where(
            DocumentExtractionLog.id == log_id,
            DocumentExtractionLog.owner_id == owner_id,
        )
        return self.session.scalar(stmt)

    def apply_submit_update(
        self,
        log: DocumentExtractionLog,
        *,
        entity_type: str,
        created_id: int | None,
        contract_url: str | None,
        fields_given_count: int,
        edits: list[dict],
    ) -> DocumentExtractionLog:
        """Record one form submission against the log (called per property/renter save)."""
        existing = json.loads(log.field_edits) if log.field_edits else []
        existing.extend({**e, "section": entity_type} for e in edits)
        log.field_edits = json.dumps(existing, ensure_ascii=False)
        log.fields_changed_count = len(existing)
        log.fields_given_count = (log.fields_given_count or 0) + fields_given_count
        log.forms_submitted_count = (log.forms_submitted_count or 0) + 1
        if log.submitted_at is None:
            log.submitted_at = datetime.utcnow()
        if entity_type == "property" and created_id is not None:
            log.created_property_id = created_id
        elif entity_type == "renter" and created_id is not None:
            log.created_renter_id = created_id
        if contract_url:
            log.contract_url = contract_url
        self.session.commit()
        self.session.refresh(log)
        return log
