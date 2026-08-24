import time
from typing import Annotated

import sentry_sdk
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.dependencies import (
    get_current_user,
    get_document_extraction_log_repository,
    get_document_extraction_service,
)
from app.models.document_extraction_log import DocumentExtractionLog
from app.repositories.document_extraction_log_repository import (
    DocumentExtractionLogRepository,
)
from app.schemas.document_extraction import (
    ExtractionLogUpdate,
    LeaseExtractionResponse,
)
from app.services.document_extraction_service import DocumentExtractionService

router = APIRouter()


@router.post("/lease", response_model=LeaseExtractionResponse)
async def extract_lease(
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[DocumentExtractionService, Depends(get_document_extraction_service)],
    log_repo: Annotated[DocumentExtractionLogRepository, Depends(get_document_extraction_log_repository)],
    file: Annotated[UploadFile, File()],
):
    """Extract a property + renter draft from an uploaded lease (PDF / DOCX / image).

    The file is processed in-memory and discarded — nothing is stored. An audit-log row
    is written for the call (telemetry only); the user updates it on submit via PATCH.
    """
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
        result = service.extract_lease(file_bytes, file.content_type)
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


@router.patch("/logs/{log_id}", status_code=204)
def update_extraction_log(
    log_id: int,
    data: ExtractionLogUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    log_repo: Annotated[DocumentExtractionLogRepository, Depends(get_document_extraction_log_repository)],
):
    """Record the outcome of one submitted form (property or renter) against an extraction log."""
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
