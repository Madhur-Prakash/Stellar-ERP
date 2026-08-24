"""Scanned documents over HTTP - upload, review, and confirm into a bill.

The whole safety claim of this module is that **OCR cannot post to the ledger**: it
fills a form, and a human approves it. That claim is only worth anything if it is
tested, so the confirm path here asserts both halves - the bill carries the
*reviewer's* figures, and the books still balance afterwards.

Digital PDFs are used as fixtures rather than images. They exercise the same
endpoints, the same extraction, and the same storage, but the recognition step is
exact, so a failure means the API is wrong rather than that Tesseract had a bad day.
The engine itself is covered against a real rendered image in
``test_ocr_engines.py``.
"""

from __future__ import annotations

import datetime as dt
import io
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.modules.accounting.service import ChartOfAccountsService, FiscalCalendarService
from app.modules.organizations.models import MemberStatus, Organization, OrganizationMember
from app.modules.rbac.models import Role
from app.modules.rbac.permissions import SystemRole
from app.modules.rbac.repository import RoleRepository
from app.modules.users.models import User
from tests.conftest import TEST_PASSWORD
from tests.test_ocr_engines import _write_simple_pdf

pytestmark = pytest.mark.integration

D = Decimal
SUPPLIER_GSTIN = "27AABCU9603R1ZM"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def database_backed_storage() -> Iterator[None]:
    """Force the database storage backend for every test in this file.

    ``document_storage`` is derived from whether object-storage credentials are configured,
    and a developer's ``.env`` may have them - so without this the suite would upload every
    fixture document into a real bucket and leave it there. A test that depends on a running
    object store is a test that fails for reasons that have nothing to do with the code.

    Note what this fixture *no longer* has to do. It used to also redirect a temp upload
    directory, because the outer transaction rollback undid the database row but could not
    undo a filesystem write - so the suite left real files behind on every run. Blobs now
    live in ``document_blob``, inside the same transaction as everything else, so the
    rollback takes them with it and the asymmetry is gone.
    """
    original_secret = settings.minio_secret_key
    settings.minio_secret_key = SecretStr("")
    assert settings.document_storage == "database", "storage must be the database under test"

    try:
        yield
    finally:
        settings.minio_secret_key = original_secret


def invoice_pdf(
    *,
    gstin: str = SUPPLIER_GSTIN,
    number: str = "MW-2026-0142",
    date: str = "15/07/2026",
    subtotal: str = "51000.00",
    tax: str = "9180.00",
    total: str = "60180.00",
    supplier: str = "MUMBAI WHOLESALE TRADERS",
    marker: str = "",
) -> bytes:
    """A digital PDF invoice with a text layer.

    ``marker`` changes the bytes without changing the invoice, which is how the
    duplicate-detection tests produce two *different files* describing the *same
    invoice* - the case that actually causes a supplier to be paid twice.
    """
    lines = [
        supplier,
        f"GSTIN: {gstin}",
        "TAX INVOICE",
        f"Invoice No: {number}",
        f"Date: {date}",
        f"Taxable Value: {subtotal}",
        f"Total Tax: {tax}",
        f"Grand Total: {total}",
    ]
    if marker:
        lines.append(f"Ref: {marker}")

    buffer = io.BytesIO()
    _write_simple_pdf(buffer, "\n".join(lines))
    return buffer.getvalue()


@pytest.fixture
async def ready_books(db: AsyncSession, organization: Organization) -> Organization:
    """Chart of accounts, a fiscal year, and a GSTIN so tax resolves."""
    await ChartOfAccountsService(db).seed_defaults(organization.id)
    await FiscalCalendarService(db).ensure_year_for(organization.id, fiscal_year_start_month=4)
    organization.gstin = "27AAAAA0000A1Z5"
    await db.flush()
    return organization


async def upload(
    client: AsyncClient,
    api: str,
    data: bytes,
    *,
    filename: str = "invoice.pdf",
    kind: str | None = None,
) -> dict:
    files = {"file": (filename, data, "application/pdf")}
    payload = {"kind": kind} if kind else None
    response = await client.post(f"{api}/documents", files=files, data=payload)
    assert response.status_code == 201, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
class TestCapabilities:
    async def test_reports_what_the_server_can_read(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        response = await authed_client.get(f"{api}/documents/capabilities")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["enabled"] is True
        assert body["any_engine_available"] is True
        assert "pdf-text-layer" in body["engines"]
        assert "application/pdf" in body["formats"]
        assert body["max_bytes"] == settings.max_upload_bytes

    async def test_requires_authentication(self, client: AsyncClient, api: str) -> None:
        """It names installed software, which is reconnaissance."""
        assert (await client.get(f"{api}/documents/capabilities")).status_code == 401


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
class TestUpload:
    async def test_extracts_every_field_from_a_digital_pdf(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        body = await upload(authed_client, api, invoice_pdf())
        document = body["document"]

        assert document["status"] == "extracted"
        assert document["engine"] == "pdf-text-layer"
        assert document["extracted_supplier_gstin"] == SUPPLIER_GSTIN
        assert document["extracted_invoice_number"] == "MW-2026-0142"
        assert document["extracted_invoice_date"] == "2026-07-15"
        assert D(document["extracted_total_amount"]) == D("60180.00")
        assert document["totals_reconcile"] is True
        assert body["already_uploaded"] is False
        assert body["duplicate"] is None

    async def test_money_crosses_the_wire_as_a_string(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """A float total would round-trip a ledger figure through binary floating
        point on the way to the screen that approves it."""
        document = (await upload(authed_client, api, invoice_pdf()))["document"]
        assert isinstance(document["extracted_total_amount"], str)
        assert isinstance(document["field_confidence"]["total_amount"], str)

    async def test_reconciling_totals_lift_confidence_above_the_review_line(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        document = (await upload(authed_client, api, invoice_pdf()))["document"]
        assert D(document["field_confidence"]["total_amount"]) >= D("0.85")
        assert "total_amount" not in document["low_confidence_fields"]
        # The supplier name is always a heuristic, so it always wants a human.
        assert "supplier_name" in document["low_confidence_fields"]

    async def test_the_same_file_twice_returns_the_first_document(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """Content-addressed: identical bytes cannot become two documents."""
        data = invoice_pdf()
        first = await upload(authed_client, api, data)
        second = await upload(authed_client, api, data, filename="renamed.pdf")

        assert second["already_uploaded"] is True
        assert second["document"]["id"] == first["document"]["id"]

        listing = await authed_client.get(f"{api}/documents")
        assert listing.json()["meta"]["total_items"] == 1

    async def test_accepts_a_pdf_that_is_not_an_invoice(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """A readable PDF with none of the fields an invoice has.

        People try things. A resume, a bank statement, a scanned letter - the extractor
        should find nothing and say so, not fail the upload: the file is stored, readable,
        and simply has no invoice in it. Reported as a 409 integrity violation from
        production, which is what this reproduces.
        """
        buffer = io.BytesIO()
        _write_simple_pdf(
            buffer,
            "\n".join(
                [
                    "MADHUR PRAKASH MANGAL",
                    "Software Engineer",
                    "madhurprakash2005@gmail.com | +91 98765 43210",
                    "EXPERIENCE",
                    "Built a self-hosted ERP with double-entry accounting.",
                    "EDUCATION",
                    "B.Tech Computer Science, 2023 - 2027",
                    "SKILLS",
                    "Python, TypeScript, PostgreSQL",
                ]
            ),
        )

        response = await authed_client.post(
            f"{api}/documents",
            files={"file": ("resume.pdf", buffer.getvalue(), "application/pdf")},
        )
        assert response.status_code == 201, response.text

        body = response.json()
        document = body["document"]
        # Stored and read, with nothing found - which is the honest outcome.
        assert document["status"] in {"extracted", "needs_review"}
        assert document["extracted_invoice_number"] is None
        assert document["extracted_supplier_gstin"] is None
        assert body["duplicate"] is None

        # And it appears in the queue rather than vanishing.
        listing = await authed_client.get(f"{api}/documents")
        assert any(row["id"] == document["id"] for row in listing.json()["items"])

    async def test_the_same_non_invoice_twice_is_still_deduplicated(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """The path the production failure took: upload, then upload again."""
        buffer = io.BytesIO()
        _write_simple_pdf(buffer, "MADHUR PRAKASH MANGAL\nSoftware Engineer\nSKILLS\nPython")
        data = buffer.getvalue()

        first = await authed_client.post(
            f"{api}/documents", files={"file": ("resume.pdf", data, "application/pdf")}
        )
        assert first.status_code == 201, first.text

        second = await authed_client.post(
            f"{api}/documents", files={"file": ("resume.pdf", data, "application/pdf")}
        )
        assert second.status_code == 201, second.text
        assert second.json()["already_uploaded"] is True

    async def test_a_second_upload_of_the_same_bytes_never_inserts_twice(
        self, authed_client: AsyncClient, api: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What the advisory lock buys, and what happens if the check is ever bypassed.

        Two uploads of the same file used to be able to interleave: check, check, insert,
        insert - and the loser hit `uq_document_org_sha256`. A failed flush cannot be
        recovered from, so that request died with "A database error occurred" instead of the
        "you already uploaded this" answer the endpoint is built to give.

        The lock closes the gap, which makes the failure unreachable by the normal path.
        This forces it anyway - by making the pre-check miss - and asserts the remaining
        guard reports a conflict rather than a 503, because an upload that collides is a
        conflict about a document, not a broken database.
        """
        data = invoice_pdf()
        await upload(authed_client, api, data)

        from app.modules.ocr.service import DocumentService

        async def blind(self: DocumentService, organization_id: uuid.UUID, digest: str) -> Any:
            return None

        monkeypatch.setattr(DocumentService, "_by_digest", blind)
        response = await authed_client.post(
            f"{api}/documents",
            files={"file": ("invoice.pdf", data, "application/pdf")},
        )
        monkeypatch.undo()

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "document_upload_conflict"
        # Nothing is queried after this. The flush that failed leaves *this* session
        # unusable, and these tests share one session across requests where production
        # gives each request its own - so a follow-up call here would be testing the
        # fixture. That the insert did not land is guaranteed by the aborted transaction.

    async def test_rejects_a_file_that_is_not_a_document(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """HTML named `.pdf` is HTML. The declared type is never consulted."""
        files = {
            "file": ("invoice.pdf", b"<html><script>alert(1)</script></html>", "application/pdf")
        }
        response = await authed_client.post(f"{api}/documents", files=files)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_document"

    async def test_rejects_an_empty_file(self, authed_client: AsyncClient, api: str) -> None:
        files = {"file": ("empty.pdf", b"", "application/pdf")}
        response = await authed_client.post(f"{api}/documents", files=files)
        assert response.status_code == 422

    async def test_refuses_an_oversized_upload(self, authed_client: AsyncClient, api: str) -> None:
        """The limit is checked while streaming, so the body is never fully buffered."""
        original = settings.max_upload_bytes
        settings.max_upload_bytes = 64 * 1024
        try:
            oversized = b"%PDF-1.4\n" + b"\x00" * (128 * 1024)
            files = {"file": ("big.pdf", oversized, "application/pdf")}
            response = await authed_client.post(f"{api}/documents", files=files)

            assert response.status_code == 422
            assert response.json()["error"]["code"] == "document_too_large"
        finally:
            settings.max_upload_bytes = original

    async def test_a_scanned_pdf_is_recorded_as_failed_not_discarded(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """The upload still succeeds.

        The user needs the file attached to the bill they are about to key in by hand.
        Throwing it away because the engine could not read it destroys the more useful
        half of the feature.
        """
        buffer = io.BytesIO()
        _write_simple_pdf(buffer, "x")  # a text layer too sparse to be real text

        body = await upload(authed_client, api, buffer.getvalue(), filename="scan.pdf")
        document = body["document"]

        assert document["status"] == "failed"
        assert document["failure_code"] == "pdf_has_no_text_layer"
        assert document["failure_message"]
        assert document["needs_review"] is True

    async def test_matches_the_supplier_by_gstin(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """GSTIN, not name - it is unique and government-issued."""
        created = await authed_client.post(
            f"{api}/suppliers", json={"name": "Mumbai Wholesale Traders", "gstin": SUPPLIER_GSTIN}
        )
        assert created.status_code == 201, created.text
        supplier_id = created.json()["id"]

        document = (await upload(authed_client, api, invoice_pdf()))["document"]
        assert document["matched_supplier_id"] == supplier_id
        assert document["matched_supplier_name"] == "Mumbai Wholesale Traders"

    async def test_no_match_leaves_the_supplier_blank(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """Rather than guessing from the company name."""
        document = (await upload(authed_client, api, invoice_pdf()))["document"]
        assert document["matched_supplier_id"] is None


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------
class TestDuplicateDetection:
    async def test_warns_when_the_same_invoice_arrives_as_a_different_file(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """The case that causes a supplier to be paid twice.

        A re-sent invoice is rarely byte-identical - it is re-generated, re-scanned, or
        forwarded - so the file hash does not catch it. Matching on
        ``(GSTIN, invoice number)`` does.
        """
        first = await upload(authed_client, api, invoice_pdf(marker="A"), filename="first.pdf")
        second = await upload(authed_client, api, invoice_pdf(marker="B"), filename="second.pdf")

        assert second["already_uploaded"] is False  # different bytes
        assert second["duplicate"] is not None
        assert second["duplicate"]["document_id"] == first["document"]["id"]
        assert "MW-2026-0142" in second["duplicate"]["reason"]
        assert second["document"]["is_duplicate"] is True

    async def test_it_warns_rather_than_refusing(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """A flagged document is still fully usable.

        The values compared were read by an OCR engine. Blocking on them would mean a
        misread digit could refuse a genuine invoice, which is worse than the manual
        entry this replaces.
        """
        await upload(authed_client, api, invoice_pdf(marker="A"), filename="first.pdf")
        second = await upload(authed_client, api, invoice_pdf(marker="B"), filename="second.pdf")

        assert second["document"]["status"] == "extracted"

    async def test_a_different_invoice_number_is_not_a_duplicate(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        await upload(authed_client, api, invoice_pdf(number="MW-1"), filename="a.pdf")
        second = await upload(authed_client, api, invoice_pdf(number="MW-2"), filename="b.pdf")
        assert second["duplicate"] is None

    async def test_the_same_number_from_a_different_supplier_is_not_a_duplicate(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """Every supplier numbers from 1, so the number alone means nothing."""
        await upload(authed_client, api, invoice_pdf(number="INV-001"), filename="a.pdf")
        second = await upload(
            authed_client,
            api,
            invoice_pdf(number="INV-001", gstin="29AABCU9603R1ZM"),
            filename="b.pdf",
        )
        assert second["duplicate"] is None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
class TestReads:
    async def test_lists_and_filters(self, authed_client: AsyncClient, api: str) -> None:
        await upload(authed_client, api, invoice_pdf(number="A-1"), filename="a.pdf")
        buffer = io.BytesIO()
        _write_simple_pdf(buffer, "x")
        await upload(authed_client, api, buffer.getvalue(), filename="scan.pdf")

        everything = await authed_client.get(f"{api}/documents")
        assert everything.json()["meta"]["total_items"] == 2

        extracted = await authed_client.get(f"{api}/documents", params={"status": "extracted"})
        assert extracted.json()["meta"]["total_items"] == 1

        failed = await authed_client.get(f"{api}/documents", params={"status": "failed"})
        assert failed.json()["meta"]["total_items"] == 1

    async def test_searches_by_invoice_number_and_supplier(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        await upload(authed_client, api, invoice_pdf(number="FINDME-9"), filename="a.pdf")

        hit = await authed_client.get(f"{api}/documents", params={"q": "FINDME"})
        assert hit.json()["meta"]["total_items"] == 1

        miss = await authed_client.get(f"{api}/documents", params={"q": "nothing-like-this"})
        assert miss.json()["meta"]["total_items"] == 0

    async def test_returns_the_recognised_text(self, authed_client: AsyncClient, api: str) -> None:
        """The only honest answer to "where did this number come from?"."""
        document = (await upload(authed_client, api, invoice_pdf()))["document"]

        response = await authed_client.get(f"{api}/documents/{document['id']}/text")
        assert response.status_code == 200, response.text
        body = response.json()

        assert SUPPLIER_GSTIN in body["text"]
        assert body["engine"] == "pdf-text-layer"

    async def test_downloads_the_original_with_defensive_headers(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """These bytes were uploaded by a stranger.

        Without `attachment` and `nosniff`, a file a browser decides to render runs
        script on this origin with the user's session.
        """
        data = invoice_pdf()
        document = (await upload(authed_client, api, data))["document"]

        response = await authed_client.get(f"{api}/documents/{document['id']}/file")
        assert response.status_code == 200
        assert response.content == data
        assert response.headers["content-type"].startswith("application/pdf")
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"

    async def test_the_stricter_per_route_csp_survives_the_middleware(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """`sandbox` must reach the browser.

        `SecurityHeadersMiddleware` sets an app-wide CSP on every response, and it
        used to overwrite this one - silently removing the directive that neutralises
        script in a document a browser decides to render inline. A JSON endpoint is
        checked alongside it, so the app-wide default is not weakened in the process.
        """
        from app.core.middleware import SecurityHeadersMiddleware

        document = (await upload(authed_client, api, invoice_pdf()))["document"]

        download = await authed_client.get(f"{api}/documents/{document['id']}/file")
        assert "sandbox" in download.headers["content-security-policy"]
        # The route's own policy, not the app-wide one - and specifically the narrower of
        # the two, since `default-src 'none'` here is scoped by `sandbox`.
        assert download.headers["content-security-policy"] == "sandbox; default-src 'none'"

        # Compared against the constant rather than a copy of its text. The literal
        # spelling was duplicated here, so tightening the app-wide policy failed this
        # test for no behavioural reason - which trains people to update the expectation
        # without reading it.
        listing = await authed_client.get(f"{api}/documents")
        assert listing.headers["content-security-policy"] == SecurityHeadersMiddleware.API_CSP
        assert "default-src 'none'" in listing.headers["content-security-policy"]

    async def test_a_filename_cannot_inject_a_header(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        hostile = 'in"voice\r\nX-Evil: yes.pdf'
        document = (await upload(authed_client, api, invoice_pdf(), filename=hostile))["document"]

        response = await authed_client.get(f"{api}/documents/{document['id']}/file")
        assert response.status_code == 200
        assert "x-evil" not in {key.lower() for key in response.headers}
        assert '"' not in response.headers["content-disposition"].split("filename=")[1][1:-1]

    async def test_unknown_document_is_a_404(self, authed_client: AsyncClient, api: str) -> None:
        response = await authed_client.get(f"{api}/documents/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_documents_do_not_leak_across_organizations(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """The tenant boundary, on the endpoint that serves raw uploaded files."""
        alice = await _make_user(db, "alice-doc@example.com")
        alice_org = await _make_org(db, alice, "Alice Co")
        bob = await _make_user(db, "bob-doc@example.com")
        await _make_org(db, bob, "Bob Co")
        await db.commit()

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, alice)}"
        document = (await upload(client, api, invoice_pdf()))["document"]
        assert alice_org is not None

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, bob)}"
        assert (await client.get(f"{api}/documents/{document['id']}")).status_code == 404
        assert (await client.get(f"{api}/documents/{document['id']}/file")).status_code == 404
        assert (await client.get(f"{api}/documents")).json()["meta"]["total_items"] == 0


# ---------------------------------------------------------------------------
# Re-extract
# ---------------------------------------------------------------------------
class TestReextract:
    async def test_rereads_the_stored_text(self, authed_client: AsyncClient, api: str) -> None:
        """No engine, no file read - the point is that a parser improvement can be
        applied to documents that predate it."""
        document = (await upload(authed_client, api, invoice_pdf()))["document"]

        response = await authed_client.post(f"{api}/documents/{document['id']}/reextract")
        assert response.status_code == 200, response.text
        again = response.json()

        assert again["extracted_invoice_number"] == "MW-2026-0142"
        assert D(again["extracted_total_amount"]) == D("60180.00")

    async def test_refused_on_a_rejected_document(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        document = (await upload(authed_client, api, invoice_pdf()))["document"]
        await authed_client.post(
            f"{api}/documents/{document['id']}/reject", json={"reason": "Wrong file"}
        )

        response = await authed_client.post(f"{api}/documents/{document['id']}/reextract")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "document_terminal"

    async def test_refused_when_there_is_no_text(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        buffer = io.BytesIO()
        _write_simple_pdf(buffer, "x")
        document = (await upload(authed_client, api, buffer.getvalue()))["document"]

        response = await authed_client.post(f"{api}/documents/{document['id']}/reextract")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "document_no_text"


# ---------------------------------------------------------------------------
# Confirm - the path that touches money
# ---------------------------------------------------------------------------
class TestConfirm:
    async def _prepare(self, client: AsyncClient, api: str) -> tuple[str, str]:
        """A supplier on file and an uploaded, extracted document."""
        created = await client.post(
            f"{api}/suppliers", json={"name": "Mumbai Wholesale", "gstin": SUPPLIER_GSTIN}
        )
        assert created.status_code == 201, created.text
        document = (await upload(client, api, invoice_pdf()))["document"]
        return created.json()["id"], document["id"]

    @staticmethod
    def _bill_payload(supplier_id: str, **overrides) -> dict:
        payload: dict = {
            "supplier_id": supplier_id,
            "supplier_invoice_number": "MW-2026-0142",
            "bill_date": "2026-07-15",
            "lines": [
                {
                    "description": "Widget Assembly A",
                    "quantity": "100",
                    "unit_price": "450",
                    "tax_rate": "18",
                },
                {
                    "description": "Mounting Bracket",
                    "quantity": "50",
                    "unit_price": "120",
                    "tax_rate": "18",
                },
            ],
            "post": True,
        }
        payload.update(overrides)
        return payload

    async def test_creates_a_posted_bill_and_links_it(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        supplier_id, document_id = await self._prepare(authed_client, api)

        response = await authed_client.post(
            f"{api}/documents/{document_id}/confirm",
            json={"bill": self._bill_payload(supplier_id)},
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["document"]["status"] == "confirmed"
        assert body["document"]["bill_id"] == body["bill"]["id"]
        assert body["document"]["bill_number"] == body["bill"]["bill_number"]
        assert body["document"]["reviewed_at"] is not None
        assert body["bill"]["status"] == "posted"
        assert body["bill"]["journal_entry_id"] is not None

    async def test_the_bill_carries_the_reviewers_figures_not_the_extracted_ones(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """The core safety property of the whole module.

        The document says the total is 60,180. The reviewer corrects it - because the
        supplier's own arithmetic was wrong, or the engine misread a digit. What lands
        in the books must be the corrected figure, and the extracted one must survive
        only as a record of what the machine thought.
        """
        supplier_id, document_id = await self._prepare(authed_client, api)

        corrected = self._bill_payload(
            supplier_id,
            lines=[
                {"description": "Widget", "quantity": "1", "unit_price": "1000", "tax_rate": "18"}
            ],
        )
        response = await authed_client.post(
            f"{api}/documents/{document_id}/confirm", json={"bill": corrected}
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert D(body["bill"]["grand_total"]) == D("1180.00")
        # The machine's reading is preserved, unchanged, for the audit trail.
        assert D(body["document"]["extracted_total_amount"]) == D("60180.00")

    async def test_the_books_still_balance_afterwards(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """The property that matters after any sequence of real operations."""
        supplier_id, document_id = await self._prepare(authed_client, api)
        await authed_client.post(
            f"{api}/documents/{document_id}/confirm",
            json={"bill": self._bill_payload(supplier_id)},
        )

        response = await authed_client.get(f"{api}/reports/trial-balance")
        assert response.status_code == 200, response.text
        report = response.json()
        assert D(report["total_debit"]) == D(report["total_credit"])
        assert D(report["total_debit"]) > 0  # something was actually posted
        assert report["is_balanced"] is True

    async def test_confirming_twice_is_a_conflict(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """Otherwise one scanned invoice becomes two payables."""
        supplier_id, document_id = await self._prepare(authed_client, api)
        payload = {"bill": self._bill_payload(supplier_id)}

        first = await authed_client.post(f"{api}/documents/{document_id}/confirm", json=payload)
        assert first.status_code == 201, first.text

        second = await authed_client.post(f"{api}/documents/{document_id}/confirm", json=payload)
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "document_already_confirmed"

    async def test_inherits_the_duplicate_invoice_number_refusal(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """Because confirming goes through `BillService`, not around it.

        Two different scans of the same invoice cannot both become bills - and the
        rule enforcing that is the one `POST /bills` already had, not a second copy.
        """
        supplier_id, first_id = await self._prepare(authed_client, api)
        second_id = (
            await upload(authed_client, api, invoice_pdf(marker="B"), filename="again.pdf")
        )["document"]["id"]

        payload = {"bill": self._bill_payload(supplier_id)}
        assert (
            await authed_client.post(f"{api}/documents/{first_id}/confirm", json=payload)
        ).status_code == 201

        response = await authed_client.post(f"{api}/documents/{second_id}/confirm", json=payload)
        assert response.status_code == 409, response.text

    async def test_a_receipt_cannot_become_a_bill(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        created = await authed_client.post(
            f"{api}/suppliers", json={"name": "Mumbai Wholesale", "gstin": SUPPLIER_GSTIN}
        )
        document = (await upload(authed_client, api, invoice_pdf(), kind="receipt"))["document"]
        assert document["kind"] == "receipt"

        response = await authed_client.post(
            f"{api}/documents/{document['id']}/confirm",
            json={"bill": self._bill_payload(created.json()["id"])},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "document_kind_not_actionable"

    async def test_a_rejected_document_cannot_be_confirmed(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        supplier_id, document_id = await self._prepare(authed_client, api)
        await authed_client.post(
            f"{api}/documents/{document_id}/reject", json={"reason": "Duplicate of MW-140"}
        )

        response = await authed_client.post(
            f"{api}/documents/{document_id}/confirm",
            json={"bill": self._bill_payload(supplier_id)},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "document_rejected"

    async def test_confirming_is_recorded_in_the_audit_trail(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """The moment a machine-read figure became money owed.

        It records both numbers, so an auditor can tell whether the figure in the
        books came from OCR or from a human correcting it.
        """
        supplier_id, document_id = await self._prepare(authed_client, api)
        await authed_client.post(
            f"{api}/documents/{document_id}/confirm",
            json={"bill": self._bill_payload(supplier_id)},
        )

        trail = await authed_client.get(f"{api}/audit", params={"action": "document.confirmed"})
        assert trail.status_code == 200, trail.text
        entries = trail.json()["items"]
        assert len(entries) == 1

        entry = entries[0]
        assert entry["severity"] == "warning"
        assert entry["changes"]["extracted_total"] == "60180.0000"
        assert entry["changes"]["posted_total"]


# ---------------------------------------------------------------------------
# Reject and delete
# ---------------------------------------------------------------------------
class TestRejectAndDelete:
    async def test_rejecting_records_the_reason(self, authed_client: AsyncClient, api: str) -> None:
        document = (await upload(authed_client, api, invoice_pdf()))["document"]

        response = await authed_client.post(
            f"{api}/documents/{document['id']}/reject",
            json={"reason": "Already entered manually as BILL-0031"},
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["status"] == "rejected"
        assert "BILL-0031" in body["notes"]
        assert body["reviewed_at"] is not None

    async def test_a_reason_is_required(self, authed_client: AsyncClient, api: str) -> None:
        """ "Rejected" with no explanation tells the next person nothing."""
        document = (await upload(authed_client, api, invoice_pdf()))["document"]
        response = await authed_client.post(
            f"{api}/documents/{document['id']}/reject", json={"reason": ""}
        )
        assert response.status_code == 422

    async def test_deleting_removes_it_from_the_queue(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        document = (await upload(authed_client, api, invoice_pdf()))["document"]

        response = await authed_client.delete(f"{api}/documents/{document['id']}")
        assert response.status_code == 200, response.text
        assert (await authed_client.get(f"{api}/documents")).json()["meta"]["total_items"] == 0

    async def test_a_confirmed_document_cannot_be_deleted(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """It is the evidence behind an immutable ledger entry."""
        created = await authed_client.post(
            f"{api}/suppliers", json={"name": "Mumbai Wholesale", "gstin": SUPPLIER_GSTIN}
        )
        document = (await upload(authed_client, api, invoice_pdf()))["document"]
        await authed_client.post(
            f"{api}/documents/{document['id']}/confirm",
            json={"bill": TestConfirm._bill_payload(created.json()["id"])},
        )

        response = await authed_client.delete(f"{api}/documents/{document['id']}")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "document_has_bill"


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
class TestPermissions:
    async def test_a_viewer_can_read_but_not_upload(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        owner = await _make_user(db, "doc-owner@example.com")
        org = await _make_org(db, owner, "Acme Co")
        viewer = await _make_user(db, "doc-viewer@example.com")
        db.add(
            OrganizationMember(
                organization_id=org.id,
                user_id=viewer.id,
                role_id=await _role_id(db, org, "viewer"),
                status=MemberStatus.ACTIVE,
                joined_at=dt.datetime.now(dt.UTC),
            )
        )
        viewer.last_organization_id = org.id
        await db.commit()

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, viewer)}"

        assert (await client.get(f"{api}/documents")).status_code == 200

        files = {"file": ("invoice.pdf", invoice_pdf(), "application/pdf")}
        response = await client.post(f"{api}/documents", files=files)
        assert response.status_code == 403
        assert response.json()["error"]["details"]["required_permission"] == "document:write"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _make_user(db: AsyncSession, email: str, name: str = "Test Person") -> User:
    user = User(
        email=email,
        full_name=name,
        password_hash=hash_password(TEST_PASSWORD),
        email_verified_at=dt.datetime.now(dt.UTC),
    )
    db.add(user)
    await db.flush()
    return user


async def _make_org(db: AsyncSession, owner: User, name: str) -> Organization:
    org = Organization(name=name, slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}")
    db.add(org)
    await db.flush()

    seeded = await RoleRepository(db).seed_system_roles(org.id)
    db.add(
        OrganizationMember(
            organization_id=org.id,
            user_id=owner.id,
            role_id=seeded[SystemRole.OWNER].id,
            is_owner=True,
            status=MemberStatus.ACTIVE,
            joined_at=dt.datetime.now(dt.UTC),
        )
    )
    owner.last_organization_id = org.id
    await db.flush()
    return org


async def _sign_in(client: AsyncClient, api: str, user: User) -> str:
    response = await client.post(
        f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


async def _role_id(db: AsyncSession, org: Organization, slug: str) -> uuid.UUID:
    role = await db.scalar(select(Role).where(Role.organization_id == org.id, Role.slug == slug))
    assert role is not None
    return role.id
