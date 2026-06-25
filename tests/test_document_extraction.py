"""Router tests for /extract/lease + /extract/logs (auth, response shape, audit log)."""
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_document_extraction_service
from app.database import get_db
from app.main import app
from app.models.document_extraction_log import DocumentExtractionLog
from app.schemas.document_extraction import LeaseExtraction
from app.services.document_extraction_service import ExtractionMeta, ExtractionResult
from tests.conftest import OWNER_A, OWNER_B


def _meta():
    return ExtractionMeta(
        model="claude-sonnet-4-6",
        input_tokens=1200,
        output_tokens=800,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        estimated_cost_usd=0.0156,
        fields_extracted=2,
        low_confidence_count=0,
        medium_confidence_count=1,
    )


class _StubService:
    """Stands in for DocumentExtractionService — returns a fixed result, no network."""

    model_name = "claude-sonnet-4-6"

    def __init__(self, draft):
        self._result = ExtractionResult(extraction=draft, meta=_meta())

    def extract_lease(self, file_bytes, content_type):
        return self._result


def test_extract_lease_requires_auth(db_session):
    """No token -> rejected (only the DB is overridden; auth runs for real)."""
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        bare = TestClient(app)
        resp = bare.post("/extract/lease", files={"file": ("x.pdf", b"%PDF", "application/pdf")})
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides.clear()


def test_extract_lease_returns_draft_and_creates_log(client, db_session):
    draft = LeaseExtraction()
    draft.property.city = "Tel Aviv"
    draft.renter.first_name = "Dana"

    app.dependency_overrides[get_document_extraction_service] = lambda: _StubService(draft)
    try:
        resp = client.post(
            "/extract/lease",
            files={"file": ("lease.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "log_id" in body
        assert body["extraction"]["property"]["city"] == "Tel Aviv"
        assert body["extraction"]["renter"]["first_name"] == "Dana"

        # An audit-log row was created with the call telemetry.
        log = db_session.get(DocumentExtractionLog, body["log_id"])
        assert log is not None
        assert log.owner_id == OWNER_A
        assert log.status == "success"
        assert log.model == "claude-sonnet-4-6"
        assert log.input_tokens == 1200
        assert log.filename == "lease.pdf"
        assert log.forms_submitted_count == 0
    finally:
        app.dependency_overrides.pop(get_document_extraction_service, None)


def test_patch_log_records_submit_outcome(client, db_session):
    from tests.factories import make_property, make_renter

    prop = make_property(db_session)
    renter = make_renter(db_session)
    log = DocumentExtractionLog(owner_id=OWNER_A, status="success", model="claude-sonnet-4-6")
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)

    # Property submit: one field changed, two given, a property created.
    resp = client.patch(
        f"/extract/logs/{log.id}",
        json={
            "entity_type": "property",
            "created_id": prop.id,
            "fields_given_count": 2,
            "field_edits": [
                {"field": "property.city", "prefilled_value": "Tel Aviv", "submitted_value": "Haifa", "source_text": "עיר"}
            ],
        },
    )
    assert resp.status_code == 204

    # Renter submit: contract kept, renter created, no changes.
    resp = client.patch(
        f"/extract/logs/{log.id}",
        json={
            "entity_type": "renter",
            "created_id": renter.id,
            "contract_url": "https://firebase/lease.pdf",
            "fields_given_count": 3,
            "field_edits": [],
        },
    )
    assert resp.status_code == 204

    db_session.refresh(log)
    assert log.forms_submitted_count == 2
    assert log.created_property_id == prop.id
    assert log.created_renter_id == renter.id
    assert log.contract_url == "https://firebase/lease.pdf"
    assert log.fields_given_count == 5
    assert log.fields_changed_count == 1
    assert log.submitted_at is not None
    assert '"submitted_value": "Haifa"' in log.field_edits


def test_patch_log_rejects_other_owner(client_factory, db_session):
    log = DocumentExtractionLog(owner_id=OWNER_B, status="success")
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)

    client = client_factory(OWNER_A)  # authenticated as A, log belongs to B
    resp = client.patch(
        f"/extract/logs/{log.id}",
        json={"entity_type": "property", "created_id": 1, "fields_given_count": 0, "field_edits": []},
    )
    assert resp.status_code == 404
