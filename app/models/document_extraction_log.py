from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text

from app.models.base import Base


class DocumentExtractionLog(Base):
    """One row per document-scan extraction call — telemetry created at extraction time
    and the outcome filled in when the user submits the reviewed form(s). Holds no
    extracted content except the before/after values of fields the user *changed*."""

    __tablename__ = "document_extraction_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Uploaded file — metadata only (the file itself is processed in-memory and discarded).
    filename = Column(String, nullable=True)
    content_type = Column(String, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)

    # The Claude call.
    model = Column(String, nullable=True)
    status = Column(String, nullable=False)  # success | error | unsupported
    error_detail = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cache_read_tokens = Column(Integer, nullable=True)
    cache_creation_tokens = Column(Integer, nullable=True)
    estimated_cost_usd = Column(Numeric(10, 6), nullable=True)

    # Extraction quality (computed at extraction time).
    fields_extracted = Column(Integer, nullable=True)
    low_confidence_count = Column(Integer, nullable=True)
    medium_confidence_count = Column(Integer, nullable=True)

    # Outcome (filled in when the user submits the reviewed form(s)).
    forms_submitted_count = Column(Integer, nullable=False, default=0)
    submitted_at = Column(DateTime, nullable=True)
    created_property_id = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"), nullable=True)
    created_renter_id = Column(Integer, ForeignKey("renters.id", ondelete="SET NULL"), nullable=True)
    contract_url = Column(Text, nullable=True)
    fields_given_count = Column(Integer, nullable=False, default=0)
    fields_changed_count = Column(Integer, nullable=False, default=0)
    # JSON list of {section, field, prefilled_value, submitted_value, source_text} for
    # fields the user overrode (the only extracted content stored).
    field_edits = Column(Text, nullable=True)
