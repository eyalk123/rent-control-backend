import time
from typing import Annotated

import sentry_sdk
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.dependencies import (
    get_current_user,
    get_document_extraction_log_repository,
    get_document_extraction_service,
    get_entitlement_gate,
    get_expense_category_repository,
    get_owner_repository,
    get_property_repository,
    get_supplier_repository,
)
from app.models.document_extraction_log import DocumentExtractionLog
from app.repositories.expense_category_repository import ExpenseCategoryRepository
from app.repositories.owner_repository import OwnerRepository
from app.repositories.property_repository import PropertyRepository
from app.repositories.supplier_repository import SupplierRepository
from app.repositories.document_extraction_log_repository import (
    DocumentExtractionLogRepository,
)
from app.services.entitlement_gate import EntitlementGate
from app.schemas.document_extraction import (
    ExtractionLogUpdate,
    LeaseExtractionResponse,
    ReceiptExtractionResponse,
)
from app.services.document_extraction_service import DocumentExtractionService
from app.services.receipt_extraction import (
    CategoryOption,
    PropertyOption,
    ReceiptCatalog,
    SupplierOption,
)

router = APIRouter()


@router.post("/lease", response_model=LeaseExtractionResponse)
async def extract_lease(
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[DocumentExtractionService, Depends(get_document_extraction_service)],
    log_repo: Annotated[DocumentExtractionLogRepository, Depends(get_document_extraction_log_repository)],
    owner_repository: Annotated[OwnerRepository, Depends(get_owner_repository)],
    entitlement_gate: Annotated[EntitlementGate, Depends(get_entitlement_gate)],
    file: Annotated[UploadFile, File()],
):
    """Extract a property + renter draft from an uploaded lease (PDF / DOCX / image).

    The file is processed in-memory and is not persisted by this service. It is sent to
    Anthropic to be read, and is retained there for up to 30 days under their retention
    policy. An audit-log row is written for the call (telemetry only — filename, size,
    model, cost); the user updates it on submit via PATCH.
    """
    # Before the file is read, and long before it reaches Anthropic: the free plan's
    # monthly allowance exists to bound spend, and a check that runs after the model call
    # has already spent it. 402 with the reset date, so a client can say when it returns.
    entitlement_gate.require_lease_scan(current_user["user_id"])

    file_bytes = await file.read()
    started = time.monotonic()

    def _log_failure(status_str: str, detail: str) -> None:
        # Best-effort failure log; never let logging mask the original error.
        try:
            log_repo.create(
                DocumentExtractionLog(
                    owner_id=current_user["user_id"],
                    filename=file.filename,
                    content_type=file.content_type,
                    file_size_bytes=len(file_bytes),
                    model=service.model_name,
                    status=status_str,
                    error_detail=detail,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            )
        except Exception as exc:
            # Never let the audit write mask the original error — but don't lose it.
            sentry_sdk.capture_exception(exc)

    try:
        # The owner's country decides which digit-separator convention the model is told
        # to expect. A missing owner row passes None, which resolves to Israel.
        owner = owner_repository.get(current_user["user_id"])
        result = service.extract_lease(
            file_bytes, file.content_type, owner.country if owner else None
        )
    except HTTPException as exc:
        _log_failure("unsupported" if exc.status_code == 415 else "error", str(exc.detail))
        raise
    except Exception as exc:
        # Convert any unexpected error (e.g. an Anthropic SDK error) into a proper
        # response so it goes through CORS middleware and the client sees a real message
        # instead of an opaque "CORS"/network failure.
        # The real cause is discarded below in favour of a clean 502, and the 502
        # itself is not reported (failed_request_status_codes is disabled), so this is
        # the only chance to capture it.
        sentry_sdk.capture_exception(exc)
        _log_failure("error", f"{type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=502, detail="Document extraction failed. Please try again."
        )

    meta = result.meta
    log = log_repo.create(
        DocumentExtractionLog(
            owner_id=current_user["user_id"],
            filename=file.filename,
            content_type=file.content_type,
            file_size_bytes=len(file_bytes),
            model=meta.model,
            status="success",
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=meta.input_tokens,
            output_tokens=meta.output_tokens,
            cache_read_tokens=meta.cache_read_tokens,
            cache_creation_tokens=meta.cache_creation_tokens,
            estimated_cost_usd=meta.estimated_cost_usd,
            fields_extracted=meta.fields_extracted,
            low_confidence_count=meta.low_confidence_count,
            medium_confidence_count=meta.medium_confidence_count,
        )
    )
    return LeaseExtractionResponse(log_id=log.id, extraction=result.extraction)


@router.post("/receipt", response_model=ReceiptExtractionResponse)
async def extract_receipt(
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[DocumentExtractionService, Depends(get_document_extraction_service)],
    log_repo: Annotated[DocumentExtractionLogRepository, Depends(get_document_extraction_log_repository)],
    owner_repository: Annotated[OwnerRepository, Depends(get_owner_repository)],
    category_repository: Annotated[ExpenseCategoryRepository, Depends(get_expense_category_repository)],
    supplier_repository: Annotated[SupplierRepository, Depends(get_supplier_repository)],
    property_repository: Annotated[PropertyRepository, Depends(get_property_repository)],
    entitlement_gate: Annotated[EntitlementGate, Depends(get_entitlement_gate)],
    file: Annotated[UploadFile, File()],
    match_property: Annotated[bool, Form()] = True,
):
    """Read an expense draft from a receipt photo or PDF.

    Category, supplier and property come back as ids of the owner's **existing** records or
    not at all — the scanner never proposes a new one. ``match_property`` is false when the
    client already knows the property (the form was opened from it, or one is picked), and
    then the owner's properties are not sent to the model at all.

    Same handling as ``/lease``: in memory here, retained by Anthropic for up to 30 days, one
    audit-log row per call, and a monthly allowance of its own checked before the upload.
    """
    owner_id = current_user["user_id"]
    entitlement_gate.require_receipt_scan(owner_id)

    file_bytes = await file.read()
    started = time.monotonic()

    def _log(**fields) -> DocumentExtractionLog:
        return log_repo.create(
            DocumentExtractionLog(
                owner_id=owner_id,
                kind="receipt",
                filename=file.filename,
                content_type=file.content_type,
                file_size_bytes=len(file_bytes),
                latency_ms=int((time.monotonic() - started) * 1000),
                **fields,
            )
        )

    def _log_failure(status_str: str, detail: str) -> None:
        try:
            _log(model=service.model_name, status=status_str, error_detail=detail)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)

    # Only the owner's active records, and no locked property — the model may only answer
    # with an id from these lists, and clean_receipt holds it to that.
    hidden = entitlement_gate.hidden_property_ids(owner_id)
    catalog = ReceiptCatalog(
        categories=tuple(
            CategoryOption(id=c.id, label=c.key or c.name or "")
            for c in category_repository.get_all_active_ordered(owner_id)
        ),
        suppliers=tuple(
            SupplierOption(
                id=s.id,
                name=s.name,
                phone=s.phone,
                email=s.email,
                category_ids=tuple(c.id for c in s.categories),
            )
            for s in supplier_repository.get_all(owner_id)
        ),
        properties=tuple(
            PropertyOption(
                id=p.id,
                label=", ".join(
                    part for part in (p.address, p.apartment and f"apt {p.apartment}", p.city) if part
                ),
            )
            for p in property_repository.get_all_by_owner(owner_id)
            if p.id not in hidden
        )
        if match_property
        else (),
    )

    try:
        owner = owner_repository.get(owner_id)
        result = service.extract_receipt(
            file_bytes, file.content_type, catalog, owner.country if owner else None
        )
    except HTTPException as exc:
        _log_failure("unsupported" if exc.status_code == 415 else "error", str(exc.detail))
        raise
    except Exception as exc:
        # See extract_lease: the 502 is not reported, so this is the only capture.
        sentry_sdk.capture_exception(exc)
        _log_failure("error", f"{type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=502, detail="Receipt extraction failed. Please try again."
        )

    meta = result.meta
    log = _log(
        model=meta.model,
        status="success",
        input_tokens=meta.input_tokens,
        output_tokens=meta.output_tokens,
        cache_read_tokens=meta.cache_read_tokens,
        cache_creation_tokens=meta.cache_creation_tokens,
        estimated_cost_usd=meta.estimated_cost_usd,
        fields_extracted=meta.fields_extracted,
        low_confidence_count=meta.low_confidence_count,
        medium_confidence_count=meta.medium_confidence_count,
    )
    return ReceiptExtractionResponse(log_id=log.id, extraction=result.extraction)


@router.patch("/logs/{log_id}", status_code=204)
def update_extraction_log(
    log_id: int,
    data: ExtractionLogUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    log_repo: Annotated[DocumentExtractionLogRepository, Depends(get_document_extraction_log_repository)],
):
    """Record the outcome of one submitted form (property, renter or expense) against an extraction log."""
    log = log_repo.get_by_id(log_id, owner_id=current_user["user_id"])
    if log is None:
        raise HTTPException(status_code=404, detail="Extraction log not found")
    log_repo.apply_submit_update(
        log,
        entity_type=data.entity_type,
        created_id=data.created_id,
        contract_url=data.contract_url,
        fields_given_count=data.fields_given_count,
        edits=[e.model_dump() for e in data.field_edits],
    )
    return None
