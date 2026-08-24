"""Document intelligence service - upload, read, review, confirm.

The pipeline, and why each step is where it is:

1. **Sniff the format from the bytes.** Never from the client's declared type.
2. **Hash and deduplicate.** The same bytes are the same document; the existing row
   is returned rather than a second copy created.
3. **Store the blob, then create the row.** In that order: a row pointing at a file
   that was never written is a broken link every reader has to defend against,
   whereas a blob with no row is unreferenced garbage that harms nothing.
4. **Recognise and extract.** Failures are *recorded on the row*, not raised. An
   upload that Tesseract cannot read is still a document the user needs - they will
   type it in by hand and attach the scan to the bill - and throwing away their file
   because the engine struggled is the worst possible response.
5. **Match the supplier by GSTIN**, which is unique and government-issued, rather
   than by fuzzy name comparison.
6. **Flag likely duplicates.** Advisory only. Paying a supplier invoice twice is the
   most expensive clerical error in payables, so it must be surfaced; but the values
   compared were read by an OCR engine, and refusing a genuine invoice over a
   misread digit would make this feature worse than manual entry.

**Confirming delegates to :class:`~app.modules.purchasing.receiving.BillService`.**
Nothing in this module writes to the ledger. Accepting machine-read values has to be
exactly as safe as typing them in, which means one code path, not two.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.context import RequestContext
from app.core.exceptions import AppError, BusinessRuleError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.pagination import PageParams
from app.modules.audit.models import AuditAction
from app.modules.audit.service import AuditService
from app.modules.ocr.engines import UnsupportedDocumentError, recognise, sniff_format
from app.modules.ocr.extraction import (
    HIGH_CONFIDENCE,
    ExtractedDocument,
    extract_document,
    mean_confidence,
    totals_reconcile,
)
from app.modules.ocr.models import Document, DocumentKind, DocumentStatus
from app.modules.ocr.storage import (
    DocumentStore,
    document_store,
    relative_path_for,
    sha256_of,
)
from app.modules.organizations.clock import organization_today
from app.modules.purchasing.models import Bill, Supplier
from app.modules.purchasing.receiving import BillService
from app.modules.purchasing.schemas import BillCreate
from app.modules.users.models import User

log = get_logger(__name__)

#: Filename length kept for display. Truncated rather than rejected - an
#: inconveniently long name is not a reason to refuse someone's invoice.
MAX_FILENAME_LENGTH = 255

#: Fields whose extracted values are copied onto the row, in the order a reviewer
#: reads them.
EXTRACTED_FIELDS: tuple[str, ...] = (
    "supplier_name",
    "supplier_gstin",
    "invoice_number",
    "invoice_date",
    "subtotal",
    "tax_amount",
    "total_amount",
)


#: The confidence recorded for a field a human typed. Not a measurement, and not a
#: pretence of one - it is the number that stops the review UI flagging a value someone
#: has already checked, which is the entire meaning of "corrected".
CERTAIN: Final = Decimal("1")


def _as_text(value: object) -> str | None:
    """Render an extracted value for the audit log, which is JSON.

    Decimals and dates have no JSON representation that survives a round trip unchanged -
    a Decimal would become a float - and the audit trail's job is to still be readable
    years later, so both go in as the strings they print as.
    """
    return None if value is None else str(value)


def _scores(confidence: Mapping[str, Any]) -> dict[str, Decimal]:
    """Parse the stored confidence map back into Decimals, skipping anything malformed.

    Defensive on purpose: this is JSONB written by earlier versions of this code, and one
    unparseable entry should cost that field's contribution to the mean rather than fail
    a reviewer's edit.
    """
    parsed: dict[str, Decimal] = {}
    for name, raw in confidence.items():
        try:
            parsed[name] = Decimal(str(raw))
        except ArithmeticError:
            continue
    return parsed


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    """An earlier document that looks like the same invoice."""

    document: Document
    reason: str


@dataclass(frozen=True, slots=True)
class UploadMetadata:
    """What the storage backend records alongside the bytes.

    Satisfies :class:`~app.modules.ocr.storage.BlobMetadata`. A value object rather than
    three positional arguments so adding a field later does not touch both backends and every
    call site - and so the database backend can persist all of it while the object backend
    quietly uses only the MIME type.
    """

    original_filename: str
    mime_type: str
    uploaded_by_user_id: uuid.UUID | None


class DocumentService:
    """Everything that happens to an uploaded document."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)
        self.bills = BillService(session)

    def _store(self, organization_id: uuid.UUID) -> DocumentStore:
        """The blob backend, scoped to one tenant.

        Built per call rather than once in ``__init__`` because the default backend is
        tenant-scoped: it holds the organization id so that every blob read is filtered by it
        in SQL, not merely by a key that happens to start with the right prefix. A store
        constructed before the organization is known could not do that, and "the key contains
        the tenant id" is a convention, whereas a ``WHERE organization_id = ...`` is a
        constraint.
        """
        return document_store(self.session, organization_id)

    # -----------------------------------------------------------------------
    # Reads
    # -----------------------------------------------------------------------
    async def _by_digest(self, organization_id: uuid.UUID, digest: str) -> Document | None:
        """The document with these exact bytes, if this organization already has one.

        Shared by the pre-upload check and the lost-race recovery so the two cannot
        disagree about what "already uploaded" means - and so the unique index and this
        query keep matching filters.
        """
        query = self._base_query(organization_id).where(Document.sha256 == digest)
        return (await self.session.execute(query)).scalar_one_or_none()

    def _base_query(self, organization_id: uuid.UUID) -> Select[tuple[Document]]:
        return (
            select(Document)
            .where(
                Document.organization_id == organization_id,
                Document.deleted_at.is_(None),
            )
            .options(
                # Eager, because the response carries the supplier and bill labels.
                # Document's relationships are `lazy="raise"`, so a forgotten option
                # fails loudly in tests instead of raising MissingGreenlet in
                # production.
                selectinload(Document.matched_supplier),
                selectinload(Document.bill),
            )
        )

    async def get(self, organization_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        query = self._base_query(organization_id).where(Document.id == document_id)
        document = (await self.session.execute(query)).scalar_one_or_none()
        if document is None:
            raise NotFoundError("Document")
        return document

    async def _reload(self, organization_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        """Re-read a document so the response reflects what was just written.

        Every mutating method ends here, for two distinct reasons:

        * A row built in Python (a fresh upload) has *no* loaded relationships, and
          ``Document``'s are ``lazy="raise"`` - so serialising the response would
          raise rather than silently emitting a query. Returning the queried object
          is the fix; the guard is doing its job.
        * ``populate_existing=True`` is required, not decorative. SQLAlchemy will not
          overwrite an attribute that is already loaded, so after ``confirm`` sets
          ``bill_id`` on a row whose ``bill`` was eager-loaded as ``None``, a plain
          re-query leaves it ``None`` and the response reports no bill for a document
          that has one.
        """
        query = (
            self._base_query(organization_id)
            .where(Document.id == document_id)
            .execution_options(populate_existing=True)
        )
        document = (await self.session.execute(query)).scalar_one_or_none()
        if document is None:  # pragma: no cover - it was just written
            raise NotFoundError("Document")
        return document

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        status: DocumentStatus | None = None,
        kind: DocumentKind | None = None,
        needs_review: bool | None = None,
        q: str | None = None,
    ) -> tuple[list[Document], int]:
        """The review queue.

        Ordered newest first, which is what someone clearing an inbox wants - not by
        confidence. Sorting the least trustworthy documents to the top sounds helpful
        and is not: it buries the invoice that arrived this morning under every bad
        scan ever uploaded.
        """
        query = self._base_query(organization_id)

        if status is not None:
            query = query.where(Document.status == status)
        if kind is not None:
            query = query.where(Document.kind == kind)
        if needs_review:
            query = query.where(
                Document.status == DocumentStatus.EXTRACTED,
                or_(
                    Document.overall_confidence.is_(None),
                    Document.overall_confidence < HIGH_CONFIDENCE,
                ),
            )
        if q:
            pattern = f"%{q.strip()}%"
            query = query.where(
                or_(
                    Document.original_filename.ilike(pattern),
                    Document.extracted_supplier_name.ilike(pattern),
                    Document.extracted_invoice_number.ilike(pattern),
                    Document.extracted_supplier_gstin.ilike(pattern),
                )
            )

        total = await self._count(query)
        page = query.order_by(Document.created_at.desc()).offset(params.offset).limit(params.limit)
        rows = (await self.session.execute(page)).scalars().all()
        return list(rows), total

    async def _count(self, query: Select[tuple[Document]]) -> int:
        """Count the rows a filtered query would return.

        ``options()`` with no arguments strips the eager loaders: a ``selectinload``
        inside a ``COUNT`` subquery is pure waste, fetching every supplier and bill
        only to discard them.
        """
        subquery = query.options().order_by(None).subquery()
        statement = select(func.count()).select_from(subquery)
        return (await self.session.execute(statement)).scalar_one()

    async def file_bytes(
        self, organization_id: uuid.UUID, document_id: uuid.UUID
    ) -> tuple[bytes, Document]:
        """The original file, verified against its stored digest.

        Verification is on here specifically: these bytes are shown to a human as the
        evidence behind a ledger entry, so silently serving a corrupted blob would
        undermine the one thing the document is for.
        """
        document = await self.get(organization_id, document_id)
        data = await self._store(organization_id).read(
            document.storage_path, verify=document.sha256
        )
        return data, document

    # -----------------------------------------------------------------------
    # Upload
    # -----------------------------------------------------------------------
    async def upload(
        self,
        organization_id: uuid.UUID,
        *,
        filename: str,
        data: bytes,
        kind: DocumentKind,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> tuple[Document, DuplicateMatch | None, bool]:
        """Store, read, and extract one document.

        Returns the document, a possible duplicate warning, and whether this exact
        file had already been uploaded.
        """
        if not data:
            raise UnsupportedDocumentError("That file is empty.")

        fmt = sniff_format(data)
        if fmt is None:
            raise UnsupportedDocumentError

        digest = sha256_of(data)

        # Serialise uploads of these exact bytes for this organization, so the check below
        # is authoritative rather than merely probably-right.
        #
        # Without it the check and the insert are two statements with a gap: a
        # double-clicked upload button puts two requests in that gap, both find nothing,
        # both insert, and one hits `uq_document_org_sha256`. **A failed flush cannot be
        # recovered from** - SQLAlchemy marks the session as needing rollback, so every
        # statement after it fails too, including the query that would find the row the
        # winner just made. The loser's request therefore dies with an unexplained
        # database error instead of the "you already uploaded this" answer that is sitting
        # right there. Preventing the conflict is the only fix; catching it is too late.
        #
        # A transaction-scoped advisory lock, so the second request waits for the first to
        # commit and then finds its row. Keyed on the digest, so it never blocks uploads of
        # different files.
        await self.session.execute(
            select(
                func.pg_advisory_xact_lock(func.hashtextextended(f"{organization_id}:{digest}", 0))
            )
        )

        existing = await self._by_digest(organization_id, digest)
        if existing is not None:
            log.info(
                "duplicate upload short-circuited",
                extra={"document_id": str(existing.id), "sha256": digest},
            )
            return existing, None, True

        relative = relative_path_for(organization_id, digest, fmt)
        await self._store(organization_id).write(
            relative,
            data,
            UploadMetadata(
                original_filename=(filename or "upload")[:MAX_FILENAME_LENGTH],
                mime_type=fmt.value,
                uploaded_by_user_id=actor.id,
            ),
        )

        document = Document(
            organization_id=organization_id,
            original_filename=(filename or "upload")[:MAX_FILENAME_LENGTH],
            content_type=fmt,
            byte_size=len(data),
            sha256=digest,
            storage_path=relative,
            kind=kind,
            status=DocumentStatus.UPLOADED,
            uploaded_by_user_id=actor.id,
        )
        self.session.add(document)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            # Unreachable for a duplicate file - the lock above settles those before the
            # insert. Kept for any other constraint, and it can only report: the flush has
            # already left the session unusable, so there is nothing left to query.
            log.warning(
                "document insert violated a constraint",
                extra={"sha256": digest, "error": str(getattr(exc.orig, "constraint_name", exc))},
            )
            raise ConflictError(
                "That document could not be stored.",
                code="document_upload_conflict",
            ) from exc

        await self._recognise_and_extract(document)
        await self._match_supplier(document)
        duplicate = await self._flag_duplicate(document)

        await self.audit.record(
            AuditAction.DOCUMENT_UPLOADED,
            actor=actor,
            organization_id=organization_id,
            resource_type="document",
            resource_id=document.id,
            summary=f"Uploaded {document.original_filename}",
            changes={
                "status": document.status.value,
                "engine": document.engine,
                "overall_confidence": str(document.overall_confidence),
                "duplicate_of": str(document.duplicate_of_id) if document.duplicate_of_id else None,
            },
            ip_address=ctx.ip_address if ctx else None,
            user_agent=ctx.user_agent if ctx else None,
        )
        await self.session.flush()
        return await self._reload(organization_id, document.id), duplicate, False

    async def _recognise_and_extract(self, document: Document) -> None:
        """Read the file and populate the candidate fields.

        **Engine failures are recorded, not raised.** A document Tesseract cannot
        read is still the user's invoice: they will key it in by hand and attach the
        scan to the bill. Discarding the upload because recognition struggled would
        destroy the more useful half of the feature.
        """
        data = await self._store(document.organization_id).read(document.storage_path)

        try:
            result = await recognise(data, document.content_type)
        except AppError as exc:
            document.status = DocumentStatus.FAILED
            document.failure_code = exc.code
            document.failure_message = exc.message
            log.info(
                "document recognition failed",
                extra={"document_id": str(document.id), "code": exc.code},
            )
            return

        document.engine = result.engine
        document.engine_confidence = result.mean_confidence
        document.page_count = result.page_count
        document.recognised_text = result.text

        today = await organization_today(self.session, document.organization_id)
        self._apply_extraction(document, extract_document(result.text, today=today))
        document.status = DocumentStatus.EXTRACTED

    @staticmethod
    def _apply_extraction(document: Document, parsed: ExtractedDocument) -> None:
        """Copy an :class:`ExtractedDocument` onto the row.

        Confidence is stored as a **string** per field. JSON has exactly one numeric
        type and it is a float, so storing ``0.97`` as a number would round-trip a
        Decimal through binary floating point for no benefit whatsoever.
        """
        confidence: dict[str, Any] = {}

        for name in EXTRACTED_FIELDS:
            field = getattr(parsed, name)
            setattr(document, f"extracted_{name}", None if field is None else field.value)
            if field is not None:
                confidence[name] = str(field.confidence)

        document.field_confidence = confidence
        # Every value on the row now came from the parser, so no field is a human's any
        # more. Leaving stale names here would mark a machine-read value as checked -
        # the one claim this list exists to make truthfully.
        document.corrected_fields = []
        document.overall_confidence = parsed.overall_confidence or None
        document.totals_reconcile = parsed.totals_reconcile

    async def _match_supplier(self, document: Document) -> None:
        """Resolve the extracted GSTIN to a supplier on file.

        GSTIN, not name. It is unique, government-issued, and either matches or does
        not; comparing company names means deciding whether "ACME TRADING CO." and
        "Acme Trading Company Pvt Ltd" are the same business, and being wrong in
        either direction is worse than leaving the field blank for a human.
        """
        if not document.extracted_supplier_gstin:
            document.matched_supplier_id = None
            return

        statement = select(Supplier.id).where(
            Supplier.organization_id == document.organization_id,
            Supplier.gstin == document.extracted_supplier_gstin,
            Supplier.deleted_at.is_(None),
        )
        document.matched_supplier_id = (await self.session.execute(statement)).scalar_one_or_none()

    async def _flag_duplicate(self, document: Document) -> DuplicateMatch | None:
        """Look for an earlier document that is probably the same invoice.

        Matched on ``(supplier GSTIN, invoice number)`` - the pair that uniquely
        identifies a GST invoice. Both must be present: an invoice number on its own
        collides constantly, because every supplier numbers from 1.
        """
        gstin = document.extracted_supplier_gstin
        number = document.extracted_invoice_number
        if not gstin or not number:
            return None

        query = (
            self._base_query(document.organization_id)
            .where(
                Document.id != document.id,
                Document.extracted_supplier_gstin == gstin,
                Document.extracted_invoice_number == number,
                Document.status != DocumentStatus.REJECTED,
            )
            .order_by(Document.created_at.asc())
            .limit(1)
        )
        match = (await self.session.execute(query)).scalar_one_or_none()
        if match is None:
            return None

        document.duplicate_of_id = match.id
        reason = f"Invoice {number} from GSTIN {gstin} was already uploaded"
        if match.bill_id is not None:
            reason += " and has been entered as a bill"

        log.warning(
            "possible duplicate invoice",
            extra={
                "document_id": str(document.id),
                "duplicate_of": str(match.id),
                "invoice_number": number,
            },
        )
        return DuplicateMatch(document=match, reason=reason)

    # -----------------------------------------------------------------------
    # Re-extract
    # -----------------------------------------------------------------------
    async def reextract(
        self,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Document:
        """Re-run field extraction against the text already on file.

        No engine, no blob - the recognised text is stored precisely so a parser
        improvement can be applied to every document that predates it, without
        needing the original files still to be on disk.

        Refused on a terminal document: a confirmed bill's figures were signed off by
        a person, and quietly rewriting the candidate values behind them would make
        the audit trail describe something that never happened.
        """
        document = await self.get(organization_id, document_id)

        if document.status.is_terminal:
            raise BusinessRuleError(
                f"This document is {document.status.value} and cannot be re-read.",
                code="document_terminal",
            )
        if not document.recognised_text:
            raise BusinessRuleError(
                "There is no recognised text to re-read. Upload the file again.",
                code="document_no_text",
            )

        before = {
            "invoice_number": document.extracted_invoice_number,
            "total_amount": str(document.extracted_total_amount),
        }

        today = await organization_today(self.session, organization_id)
        self._apply_extraction(document, extract_document(document.recognised_text, today=today))
        document.status = DocumentStatus.EXTRACTED
        document.failure_code = None
        document.failure_message = None
        await self._match_supplier(document)
        await self._flag_duplicate(document)

        await self.audit.record(
            AuditAction.DOCUMENT_REEXTRACTED,
            actor=actor,
            organization_id=organization_id,
            resource_type="document",
            resource_id=document.id,
            summary=f"Re-read {document.original_filename}",
            changes={
                "before": before,
                "after": {
                    "invoice_number": document.extracted_invoice_number,
                    "total_amount": str(document.extracted_total_amount),
                },
            },
            ip_address=ctx.ip_address if ctx else None,
            user_agent=ctx.user_agent if ctx else None,
        )
        await self.session.flush()
        return await self._reload(organization_id, document.id)

    # -----------------------------------------------------------------------
    # Correct
    # -----------------------------------------------------------------------
    async def correct(
        self,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        changes: Mapping[str, Any],
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Document:
        """Replace what the engine read with what a human read.

        **These values were always candidates, so a human may set them directly.** This
        writes nothing to the ledger and changes nothing about the confirm path, which
        still posts the values submitted on the confirm form through ``BillService``.
        What it fixes is everything the row is used for in between: duplicate detection
        and supplier matching both key off fields OCR can misread, the review queue
        orders by a confidence that a corrected field should no longer drag down, and the
        next person to open the document should see the checked values rather than the
        machine's guess at them.

        ``changes`` carries only the fields the caller actually sent - see
        ``exclude_unset`` in the router. That distinction is the whole contract: omitting
        a field leaves it alone, and sending ``null`` clears it. Without it, correcting
        one field would blank the other six.

        **Refused on a terminal document**, exactly as :meth:`reextract` is. A confirmed
        document's figures sit behind a posted bill, and editing the record of what was
        read after the fact makes the audit trail describe something that never happened.
        """
        document = await self.get(organization_id, document_id)

        if document.status.is_terminal:
            raise BusinessRuleError(
                f"This document is {document.status.value} and can no longer be edited.",
                code="document_terminal",
            )
        if not changes:
            return document

        before = {name: _as_text(getattr(document, f"extracted_{name}")) for name in changes}

        confidence = dict(document.field_confidence)
        corrected = set(document.corrected_fields)

        for name, value in changes.items():
            setattr(document, f"extracted_{name}", value)
            corrected.add(name)
            if value is None:
                # Nothing left to be confident about. Dropping the key rather than
                # storing a zero also keeps it out of the mean below, which is right: a
                # field the invoice does not carry should not score the document down.
                confidence.pop(name, None)
            else:
                confidence[name] = str(CERTAIN)

        document.field_confidence = confidence
        document.corrected_fields = sorted(corrected)
        document.overall_confidence = mean_confidence(_scores(confidence)) or None
        document.totals_reconcile = totals_reconcile(
            document.extracted_subtotal,
            document.extracted_tax_amount,
            document.extracted_total_amount,
        )

        if "supplier_gstin" in changes:
            await self._match_supplier(document)

        if "supplier_gstin" in changes or "invoice_number" in changes:
            # Cleared before re-checking, because `_flag_duplicate` only ever *sets* the
            # link. The pair identifying this invoice has just changed, so the existing
            # warning was made against values that no longer exist - and correcting a
            # misread digit has to be able to withdraw a duplicate flag, not only raise
            # one. A stale "already uploaded" on a genuine invoice is how a real bill
            # goes unpaid.
            document.duplicate_of_id = None
            await self._flag_duplicate(document)

        await self.audit.record(
            AuditAction.DOCUMENT_CORRECTED,
            actor=actor,
            organization_id=organization_id,
            resource_type="document",
            resource_id=document.id,
            summary=(f"Corrected {', '.join(sorted(changes))} on {document.original_filename}"),
            changes={
                # Both halves, because the question this row answers later is not "what
                # does it say now" - the document itself says that - but "what did the
                # machine read, and what did a person change it to".
                "before": before,
                "after": {
                    name: _as_text(getattr(document, f"extracted_{name}")) for name in changes
                },
            },
            ip_address=ctx.ip_address if ctx else None,
            user_agent=ctx.user_agent if ctx else None,
        )
        await self.session.flush()
        return await self._reload(organization_id, document.id)

    # -----------------------------------------------------------------------
    # Confirm / reject / delete
    # -----------------------------------------------------------------------
    async def confirm(
        self,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        data: BillCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> tuple[Document, Bill]:
        """Turn a reviewed document into a bill.

        The bill is created by :class:`BillService` from the values the *reviewer*
        submitted - not the extracted ones. Extraction pre-fills a form; what gets
        posted is what a human approved. That distinction is the entire safety story
        of this module, and it is enforced here by never reading
        ``document.extracted_*`` on this path.
        """
        document = await self.get(organization_id, document_id)

        if document.status is DocumentStatus.CONFIRMED:
            raise ConflictError(
                "This document has already been entered as a bill.",
                code="document_already_confirmed",
                details={"bill_id": str(document.bill_id)},
            )
        if document.status is DocumentStatus.REJECTED:
            raise BusinessRuleError(
                "This document was rejected. Upload it again to enter it.",
                code="document_rejected",
            )
        if not document.kind.is_actionable:
            raise BusinessRuleError(
                f"A {document.kind.value.replace('_', ' ')} cannot be entered as a bill.",
                code="document_kind_not_actionable",
            )

        bill = await self.bills.create(organization_id, data, actor, ctx)

        document.bill_id = bill.id
        document.status = DocumentStatus.CONFIRMED
        document.reviewed_by_user_id = actor.id
        document.reviewed_at = dt.datetime.now(dt.UTC)

        await self.audit.record(
            AuditAction.DOCUMENT_CONFIRMED,
            actor=actor,
            organization_id=organization_id,
            resource_type="document",
            resource_id=document.id,
            summary=f"Entered {document.original_filename} as bill {bill.bill_number}",
            changes={
                "bill_id": str(bill.id),
                "bill_number": bill.bill_number,
                # What the machine read against what the human approved. This pair is
                # the audit question worth being able to answer: it shows whether a
                # figure in the books came from OCR or from a correction.
                "extracted_total": str(document.extracted_total_amount),
                "posted_total": str(bill.grand_total),
                "extracted_invoice_number": document.extracted_invoice_number,
                "posted_invoice_number": data.supplier_invoice_number,
                "was_flagged_duplicate": document.duplicate_of_id is not None,
            },
            ip_address=ctx.ip_address if ctx else None,
            user_agent=ctx.user_agent if ctx else None,
        )
        await self.session.flush()
        return await self._reload(organization_id, document.id), bill

    async def reject(
        self,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        reason: str | None,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Document:
        document = await self.get(organization_id, document_id)

        if document.status is DocumentStatus.CONFIRMED:
            raise BusinessRuleError(
                "This document is already entered as a bill. Cancel the bill instead.",
                code="document_already_confirmed",
            )

        document.status = DocumentStatus.REJECTED
        # Only when one was given. Overwriting an existing note with `None` would erase
        # something a person wrote in favour of nothing.
        if reason is not None:
            document.notes = reason
        document.reviewed_by_user_id = actor.id
        document.reviewed_at = dt.datetime.now(dt.UTC)

        await self.audit.record(
            AuditAction.DOCUMENT_REJECTED,
            actor=actor,
            organization_id=organization_id,
            resource_type="document",
            resource_id=document.id,
            summary=f"Rejected {document.original_filename}",
            changes={"reason": reason},
            ip_address=ctx.ip_address if ctx else None,
            user_agent=ctx.user_agent if ctx else None,
        )
        await self.session.flush()
        return await self._reload(organization_id, document.id)

    async def delete(
        self,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> None:
        """Soft-delete a document.

        Refused once it has become a bill. The blob is the evidence behind a ledger
        entry, and that entry is immutable - deleting the document would leave a
        posted liability with nothing supporting it. The blob is never removed from
        disk here either, for the same reason.
        """
        document = await self.get(organization_id, document_id)

        if document.bill_id is not None:
            raise BusinessRuleError(
                "This document is the source of a bill and must be kept. "
                "Cancel the bill first if it was entered in error.",
                code="document_has_bill",
            )

        document.deleted_at = dt.datetime.now(dt.UTC)

        await self.audit.record(
            AuditAction.DOCUMENT_DELETED,
            actor=actor,
            organization_id=organization_id,
            resource_type="document",
            resource_id=document.id,
            summary=f"Deleted {document.original_filename}",
            ip_address=ctx.ip_address if ctx else None,
            user_agent=ctx.user_agent if ctx else None,
        )
        await self.session.flush()


__all__ = ["DocumentService", "DuplicateMatch"]
