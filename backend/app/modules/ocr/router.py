"""Scanned document endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status

from app.core.config import settings
from app.core.pagination import Page, PageParams
from app.core.schemas import MessageResponse, with_computed
from app.modules.auth.dependencies import (
    ActiveOrganizationId,
    CurrentUser,
    DbSession,
    OrganizationToday,
    RequestCtx,
    require_permission,
)
from app.modules.ocr import engines
from app.modules.ocr.models import Document, DocumentKind, DocumentStatus
from app.modules.ocr.schemas import (
    ConfirmDocumentRequest,
    ConfirmResult,
    DocumentFieldsUpdate,
    DocumentRead,
    DocumentSummary,
    DocumentText,
    DuplicateWarning,
    OcrCapabilities,
    RejectDocumentRequest,
    UploadResult,
)
from app.modules.ocr.service import DocumentService, DuplicateMatch
from app.modules.ocr.storage import read_within_limit
from app.modules.purchasing.router import bill_response
from app.modules.rbac.permissions import Permission

router = APIRouter(prefix="/documents", tags=["Documents"])


def get_documents(session: DbSession) -> DocumentService:
    return DocumentService(session)


DocumentsDep = Annotated[DocumentService, Depends(get_documents)]


# ---------------------------------------------------------------------------
# Response assembly
# ---------------------------------------------------------------------------
def _summary(document: Document) -> DocumentSummary:
    return with_computed(
        DocumentSummary,
        document,
        needs_review=document.needs_review,
        is_duplicate=document.is_duplicate,
        matched_supplier_name=(
            document.matched_supplier.name if document.matched_supplier is not None else None
        ),
    )


def _detail(document: Document) -> DocumentRead:
    return with_computed(
        DocumentRead,
        document,
        needs_review=document.needs_review,
        is_duplicate=document.is_duplicate,
        low_confidence_fields=document.low_confidence_fields,
        matched_supplier_name=(
            document.matched_supplier.name if document.matched_supplier is not None else None
        ),
        bill_number=document.bill.bill_number if document.bill is not None else None,
    )


def _duplicate_warning(match: DuplicateMatch | None) -> DuplicateWarning | None:
    if match is None:
        return None
    other = match.document
    return DuplicateWarning(
        document_id=other.id,
        status=other.status,
        bill_id=other.bill_id,
        bill_number=other.bill.bill_number if other.bill is not None else None,
        uploaded_at=other.created_at,
        reason=match.reason,
    )


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
@router.get(
    "/capabilities",
    response_model=OcrCapabilities,
    summary="What this server can read",
)
async def capabilities(
    _: Annotated[None, Depends(require_permission(Permission.DOCUMENT_READ))],
) -> OcrCapabilities:
    """Report the engines actually available.

    Unauthenticated clients get nothing here - the response names installed
    software, which is reconnaissance. Any member with `document:read` can see it,
    because the UI needs it to decide whether to offer an upload button at all.
    """
    available = engines.available_engines()
    return OcrCapabilities(
        enabled=settings.ocr_enabled,
        engines=available,
        formats=engines.supported_formats(),
        max_bytes=settings.max_upload_bytes,
        any_engine_available=bool(available),
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=UploadResult,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document for reading",
)
async def upload_document(
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: DocumentsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.DOCUMENT_WRITE))],
    file: Annotated[UploadFile, File(description="PDF, PNG, JPEG, TIFF, or WebP")],
    kind: Annotated[DocumentKind, Form()] = DocumentKind.PURCHASE_INVOICE,
) -> UploadResult:
    """Store a file, read it, and return candidate field values.

    Recognition happens inline, so this request takes as long as the engine does -
    typically under a second for a digital PDF and a few seconds for a photograph.
    That is a deliberate trade for a self-hosted product: a job queue would mean
    Celery, a broker, and a worker process to supervise, and the honest response to
    "this takes four seconds" is a spinner, not three more moving parts.

    A file that cannot be read still succeeds, with `status: failed` and a reason.
    The upload is kept: the user needs the scan attached to the bill they are about
    to type in by hand.
    """
    data = await read_within_limit(file)

    document, duplicate, already = await service.upload(
        organization_id,
        filename=file.filename or "upload",
        data=data,
        kind=kind,
        actor=user,
        ctx=ctx,
    )
    return UploadResult(
        document=_detail(document),
        duplicate=_duplicate_warning(duplicate),
        already_uploaded=already,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
@router.get("", response_model=Page[DocumentSummary], summary="List documents")
async def list_documents(
    organization_id: ActiveOrganizationId,
    service: DocumentsDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.DOCUMENT_READ))],
    document_status: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    kind: Annotated[DocumentKind | None, Query()] = None,
    needs_review: Annotated[bool, Query(description="Only low-confidence documents")] = False,
    q: Annotated[str | None, Query(description="Search filename, supplier, or invoice no.")] = None,
) -> Page[DocumentSummary]:
    rows, total = await service.paginate(
        organization_id,
        params,
        status=document_status,
        kind=kind,
        needs_review=needs_review,
        q=q,
    )
    return Page.create([_summary(row) for row in rows], total=total, params=params)


@router.get("/{document_id}", response_model=DocumentRead, summary="Get a document")
async def get_document(
    document_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: DocumentsDep,
    _: Annotated[None, Depends(require_permission(Permission.DOCUMENT_READ))],
) -> DocumentRead:
    return _detail(await service.get(organization_id, document_id))


@router.get(
    "/{document_id}/text",
    response_model=DocumentText,
    summary="Get the recognised text",
)
async def get_document_text(
    document_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: DocumentsDep,
    _: Annotated[None, Depends(require_permission(Permission.DOCUMENT_READ))],
) -> DocumentText:
    """The full text the engine read.

    Separate from the document because it is kilobytes per page, and it is the only
    honest answer to "where did this number come from?" when a figure is questioned
    months later.
    """
    document = await service.get(organization_id, document_id)
    return DocumentText(
        document_id=document.id,
        engine=document.engine,
        engine_confidence=document.engine_confidence,
        page_count=document.page_count,
        text=document.recognised_text or "",
    )


@router.get(
    "/{document_id}/file",
    summary="Download the original file",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_document(
    document_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: DocumentsDep,
    _: Annotated[None, Depends(require_permission(Permission.DOCUMENT_READ))],
) -> Response:
    """Serve the stored file back.

    Three headers earn their place here, because this endpoint returns bytes a
    stranger uploaded:

    * ``Content-Disposition: attachment`` - the browser saves rather than renders.
      Without it, an uploaded file that a viewer chooses to interpret as HTML runs
      script on this origin, with the user's session cookie.
    * ``X-Content-Type-Options: nosniff`` - stops the browser from second-guessing
      the declared type and reaching that conclusion anyway.
    * ``Content-Security-Policy: sandbox`` - a final backstop for viewers that
      inline the response regardless.

    The declared content type is the *sniffed* one recorded at upload, never the one
    the client announced.
    """
    data, document = await service.file_bytes(organization_id, document_id)

    # Quote the filename: it is user-supplied text, and a bare `"` or newline in a
    # header value is a header-injection primitive.
    safe_name = document.original_filename.replace('"', "").replace("\\", "").replace("\r", "")
    safe_name = safe_name.replace("\n", "") or f"document.{document.content_type.extension}"

    return Response(
        content=data,
        media_type=document.content_type.value,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            # Content-addressed, so the bytes at this id can never change.
            "Cache-Control": "private, max-age=3600",
        },
    )


# ---------------------------------------------------------------------------
# Review actions
# ---------------------------------------------------------------------------
@router.post(
    "/{document_id}/reextract",
    response_model=DocumentRead,
    summary="Re-read the stored text",
)
async def reextract_document(
    document_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: DocumentsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.DOCUMENT_WRITE))],
) -> DocumentRead:
    """Re-run field extraction on text already on file.

    Cheap - no engine, no file read. Exists so an improvement to the parser can be
    applied to documents uploaded before it, which is otherwise impossible once the
    original files have been archived.
    """
    document = await service.reextract(organization_id, document_id, user, ctx)
    return _detail(document)


@router.patch(
    "/{document_id}/extracted",
    response_model=DocumentRead,
    summary="Correct what was read",
)
async def correct_document(
    document_id: uuid.UUID,
    data: DocumentFieldsUpdate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: DocumentsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.DOCUMENT_WRITE))],
) -> DocumentRead:
    """Overwrite machine-read fields with values a human checked.

    OCR misreads: a smudged ``8`` becomes ``3``, a GSTIN loses a character, a total is
    read off the wrong line. The extracted values are candidates, so the fix is to let
    the reviewer correct them rather than to make them re-upload a better scan.

    **`exclude_unset` is what makes this a PATCH.** Only the fields the client actually
    sent are forwarded, so omitting a field leaves it untouched while sending it as
    `null` clears it - two requests that a plain `model_dump()` would render identical,
    silently blanking every field the caller did not mention.

    `document:write`, the same permission as re-reading and rejecting. Notably *not*
    `document:confirm`: correcting what a scan says is clerical work, and nothing here
    reaches the ledger - the corrected values still have to be submitted, in full, on the
    confirm form that creates the bill.
    """
    document = await service.correct(
        organization_id,
        document_id,
        data.model_dump(exclude_unset=True),
        user,
        ctx,
    )
    return _detail(document)


@router.post(
    "/{document_id}/confirm",
    response_model=ConfirmResult,
    status_code=status.HTTP_201_CREATED,
    summary="Enter a reviewed document as a bill",
)
async def confirm_document(
    document_id: uuid.UUID,
    data: ConfirmDocumentRequest,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: DocumentsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.DOCUMENT_CONFIRM))],
    __: Annotated[None, Depends(require_permission(Permission.PURCHASE_WRITE))],
) -> ConfirmResult:
    """Create a bill from the values the reviewer submitted.

    **Two permissions, deliberately.** `document:confirm` says you may act on a
    scanned document; `purchase:write` says you may create a payable. Uploading and
    reading a supplier's PDF is clerical work that a junior can do all day; turning a
    machine-read total into money owed is not, and one permission covering both would
    quietly grant the second to everyone who has the first.

    The posted bill uses the submitted values, not the extracted ones - extraction
    fills a form, a human approves it. Everything else, including the refusal to
    accept the same supplier invoice number twice, is `BillService`'s existing
    behaviour rather than a second implementation of it.
    """
    document, bill = await service.confirm(organization_id, document_id, data.bill, user, ctx)

    # Re-read through the purchasing service and assemble with `bill_response`, the
    # same function that serves `POST /bills` - one shape for a bill, not two.
    return ConfirmResult(
        document=_detail(document),
        bill=bill_response(await service.bills.get(organization_id, bill.id), today),
    )


@router.post(
    "/{document_id}/reject",
    response_model=DocumentRead,
    summary="Reject a document",
)
async def reject_document(
    document_id: uuid.UUID,
    data: RejectDocumentRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: DocumentsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.DOCUMENT_WRITE))],
) -> DocumentRead:
    """Mark a document as not usable.

    **Rejecting is not deleting**, and the difference is the point of having both. A
    rejected document keeps its file, its recognised text and its place in the audit
    trail - it is a decision on the record, reversible by uploading the file again, and
    the answer to "what happened to that invoice?" months later. Deleting removes it from
    the inbox altogether and is refused outright once the document is behind a posted
    bill, because that file is the evidence for a ledger entry.

    A reason is optional (see :class:`RejectDocumentRequest`). Who rejected it and when
    are recorded either way.
    """
    document = await service.reject(organization_id, document_id, data.reason, user, ctx)
    return _detail(document)


@router.delete(
    "/{document_id}",
    response_model=MessageResponse,
    summary="Delete a document",
)
async def delete_document(
    document_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: DocumentsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.DOCUMENT_WRITE))],
) -> MessageResponse:
    """Soft-delete a document. Refused once it has become a bill - the file is the
    evidence behind an immutable ledger entry."""
    await service.delete(organization_id, document_id, user, ctx)
    return MessageResponse(message="Document deleted")


__all__ = ["router"]
