"""OCR engine layer - format sniffing, dispatch, and the Tesseract adapter.

Split from ``test_ocr_extraction.py`` by what they need: extraction is pure, this
touches Pillow, pypdf, and a system binary. The Tesseract tests skip when the binary
is absent, because a contributor without it should still get a green suite - but
where it *is* installed they run for real, against an image generated here. Mocking
the engine would test the mock.
"""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal

import pytest

from app.core.config import settings
from app.modules.ocr.engines import (
    MIN_CHARS_PER_PAGE,
    DocumentFormat,
    EngineUnavailableError,
    PdfTextLayerEngine,
    ScannedPdfError,
    TesseractEngine,
    UnsupportedDocumentError,
    available_engines,
    recognise_sync,
    sniff_format,
    supported_formats,
)
from app.modules.ocr.extraction import extract_document

D = Decimal

PNG_HEADER = b"\x89PNG\r\n\x1a\n"
JPEG_HEADER = b"\xff\xd8\xff\xe0"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _invoice_lines() -> list[str]:
    return [
        "MUMBAI WHOLESALE TRADERS",
        "GSTIN: 27AABCU9603R1ZM",
        "TAX INVOICE",
        "Invoice No: MW-2026-0142",
        "Date: 15/07/2026",
        "Taxable Value: 51000.00",
        "Total Tax: 9180.00",
        "Grand Total: 60180.00",
    ]


def _invoice_font(size: int) -> object | None:
    """A scalable font if the machine has one, else ``None`` for Pillow's bitmap.

    Worth the effort: Pillow's built-in font is an 11 px bitmap, and upscaling it
    produces the blurred glyphs that make Tesseract insert spurious spaces. A real
    outline font renders the way a printed invoice does, so the test measures the
    engine rather than the fixture. The fallback keeps the suite running on a machine
    with no fonts installed.
    """
    from PIL import ImageFont

    for candidate in (
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return None


@pytest.fixture
def invoice_png() -> bytes:
    """A rendered invoice, black on white, at a size Tesseract can read.

    Generated rather than committed as a binary fixture: a checked-in PNG is opaque
    in review, and a test that fails because nobody can see what the image says is a
    test nobody can fix.
    """
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    font = _invoice_font(34)
    width, height = 1000, 560
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    for index, line in enumerate(_invoice_lines()):
        draw.text((20, 20 + index * 64), line, fill="black", font=font)  # type: ignore[arg-type]

    if font is None:
        # Bitmap fallback: upscale so the glyphs clear Tesseract's size threshold.
        image = image.resize((width * 3, height * 3), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def digital_pdf() -> bytes:
    """A PDF with a real text layer, as accounting software produces."""
    pytest.importorskip("pypdf")

    buffer = io.BytesIO()
    _write_simple_pdf(buffer, "\n".join(_invoice_lines()))
    return buffer.getvalue()


def _write_simple_pdf(buffer: io.BytesIO, text: str) -> None:
    """Emit a minimal one-page PDF with a text layer.

    Hand-assembled because the point is to produce a *digital* PDF - one whose
    characters are in the file rather than drawn as pixels. A library that rasterises
    would defeat the test it exists for.
    """
    lines = text.splitlines()
    shown = "".join(f"({line.replace('(', '').replace(')', '')}) Tj 0 -16 Td\n" for line in lines)
    stream = f"BT /F1 12 Tf 40 800 Td\n{shown}ET"

    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    buffer.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1"))

    xref_at = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode())
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets:
        buffer.write(f"{offset:010d} 00000 n \n".encode())
    buffer.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n".encode()
    )


# ---------------------------------------------------------------------------
# Format sniffing
# ---------------------------------------------------------------------------
class TestSniffFormat:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (PNG_HEADER + b"whatever", DocumentFormat.PNG),
            (JPEG_HEADER + b"whatever", DocumentFormat.JPEG),
            (b"%PDF-1.7\n...", DocumentFormat.PDF),
            (b"II*\x00...", DocumentFormat.TIFF),
            (b"MM\x00*...", DocumentFormat.TIFF),
            (b"RIFF\x00\x00\x00\x00WEBPVP8 ", DocumentFormat.WEBP),
        ],
    )
    def test_identifies_accepted_formats(self, data: bytes, expected: DocumentFormat) -> None:
        assert sniff_format(data) is expected

    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"not a document",
            # The dangerous one: HTML that would run script if a browser rendered it.
            b"<html><script>fetch('/api/v1/users/me')</script></html>",
            b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            b"MZ\x90\x00",  # a Windows executable
            b"#!/bin/sh\nrm -rf /",
        ],
    )
    def test_rejects_everything_else(self, data: bytes) -> None:
        assert sniff_format(data) is None

    def test_ignores_a_lying_extension(self) -> None:
        """A file called `invoice.pdf` containing HTML is HTML.

        This is the whole reason sniffing exists: the declared type and the filename
        are both attacker-controlled, and only the bytes are not.
        """
        assert sniff_format(b"<html>gotcha</html>") is None

    def test_a_truncated_webp_is_not_a_webp(self) -> None:
        assert sniff_format(b"RIFF\x00\x00\x00\x00WEB") is None


class TestDocumentFormat:
    def test_pdf_is_not_an_image(self) -> None:
        assert not DocumentFormat.PDF.is_image
        assert DocumentFormat.PNG.is_image

    def test_every_format_has_an_extension(self) -> None:
        for fmt in DocumentFormat:
            assert fmt.extension
            assert "." not in fmt.extension


# ---------------------------------------------------------------------------
# PDF text layer
# ---------------------------------------------------------------------------
class TestPdfTextLayerEngine:
    @pytest.fixture
    def engine(self) -> PdfTextLayerEngine:
        return PdfTextLayerEngine()

    def test_supports_only_pdf(self, engine: PdfTextLayerEngine) -> None:
        assert engine.supports(DocumentFormat.PDF)
        assert not engine.supports(DocumentFormat.PNG)

    def test_reads_the_text_layer_exactly(
        self, engine: PdfTextLayerEngine, digital_pdf: bytes
    ) -> None:
        """No recognition involved, so the characters must come back verbatim."""
        result = engine.recognise(digital_pdf, DocumentFormat.PDF)

        assert result.is_exact
        assert result.mean_confidence == D("1.00")
        assert result.page_count == 1
        assert "27AABCU9603R1ZM" in result.text
        assert "60180.00" in result.text

    def test_extraction_over_the_text_layer_is_perfect(
        self, engine: PdfTextLayerEngine, digital_pdf: bytes
    ) -> None:
        """The end-to-end claim for a digital PDF: every field, and totals that add up.

        This is why the text layer is tried before OCR - the result is not
        "better", it is exact.
        """
        result = engine.recognise(digital_pdf, DocumentFormat.PDF)
        parsed = extract_document(result.text, today=dt.date(2026, 7, 30))

        assert parsed.supplier_gstin is not None
        assert parsed.supplier_gstin.value == "27AABCU9603R1ZM"
        assert parsed.invoice_number is not None
        assert parsed.invoice_number.value == "MW-2026-0142"
        assert parsed.total_amount is not None
        assert parsed.total_amount.value == D("60180.00")
        assert parsed.totals_reconcile

    def test_a_scan_wrapped_in_a_pdf_is_reported_as_such(self, engine: PdfTextLayerEngine) -> None:
        """A near-empty text layer means the page is a picture.

        Reported plainly rather than returned as an empty extraction: "this is a
        scan, send an image" is actionable, "no fields found" is not.
        """
        buffer = io.BytesIO()
        _write_simple_pdf(buffer, "x" * (MIN_CHARS_PER_PAGE // 4))

        with pytest.raises(ScannedPdfError):
            engine.recognise(buffer.getvalue(), DocumentFormat.PDF)

    def test_corrupt_pdf_is_a_client_error(self, engine: PdfTextLayerEngine) -> None:
        from app.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            engine.recognise(b"%PDF-1.4\nthis is not a pdf body", DocumentFormat.PDF)


# ---------------------------------------------------------------------------
# Tesseract
# ---------------------------------------------------------------------------
tesseract = TesseractEngine()
needs_tesseract = pytest.mark.skipif(
    not tesseract.is_available(),
    reason="tesseract binary not installed on this machine",
)


class TestTesseractEngine:
    def test_supports_images_not_pdf(self) -> None:
        assert tesseract.supports(DocumentFormat.PNG)
        assert not tesseract.supports(DocumentFormat.PDF)

    def test_availability_is_a_probe_not_an_import_check(self) -> None:
        """``is_available`` must survive being called when the binary is missing.

        It returns a bool either way - never raises. A probe that throws is useless
        for deciding whether to offer the feature.
        """
        assert isinstance(tesseract.is_available(), bool)

    @needs_tesseract
    def test_reads_a_rendered_invoice(self, invoice_png: bytes) -> None:
        result = tesseract.recognise(invoice_png, DocumentFormat.PNG)

        assert result.engine == "tesseract"
        assert not result.is_exact  # pixels were recognised, not read
        assert result.mean_confidence is not None
        assert D("0") < result.mean_confidence <= D("1")
        assert not result.is_empty

    @needs_tesseract
    def test_preserves_line_structure(self, invoice_png: bytes) -> None:
        """Labels and their values must stay on one line.

        Load-bearing: the supplier name comes from the top few *lines*, and every
        label-to-value match depends on the pair not being split.
        """
        result = tesseract.recognise(invoice_png, DocumentFormat.PNG)
        lines = [line for line in result.text.splitlines() if line.strip()]

        assert len(lines) >= 5
        # The label and the amount landed together on at least one line.
        assert any("otal" in line and any(ch.isdigit() for ch in line) for line in lines)

    @needs_tesseract
    def test_finds_the_gstin_in_a_recognised_image(self, invoice_png: bytes) -> None:
        """The strongest available signal that recognition worked end to end.

        A GSTIN is 15 characters with a strict shape, so matching one means the OCR
        got all 15 right - a single substituted character would fail the pattern.
        """
        result = tesseract.recognise(invoice_png, DocumentFormat.PNG)
        parsed = extract_document(result.text, today=dt.date(2026, 7, 30))

        assert parsed.supplier_gstin is not None, result.text
        assert parsed.supplier_gstin.value == "27AABCU9603R1ZM"

    @needs_tesseract
    def test_rejects_a_non_image(self) -> None:
        with pytest.raises(UnsupportedDocumentError):
            tesseract.recognise(PNG_HEADER + b"truncated garbage", DocumentFormat.PNG)

    @needs_tesseract
    def test_flattens_transparency_instead_of_blackening_it(self) -> None:
        """White text on a transparent background must not become black on black.

        Grayscale conversion turns an alpha channel into black, so an RGBA export -
        which is what "save as PNG" produces in most tools - would otherwise read as
        a blank page.
        """
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (900, 200), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        draw.text((20, 60), "GSTIN: 27AABCU9603R1ZM", fill=(0, 0, 0, 255))
        image = image.resize((2700, 600), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        result = tesseract.recognise(buffer.getvalue(), DocumentFormat.PNG)
        assert not result.is_empty


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_pdf_is_always_supported(self) -> None:
        """pypdf is in the `ocr` extra and pure Python, so PDF needs no binary."""
        assert DocumentFormat.PDF in supported_formats()

    def test_available_engines_lists_names(self) -> None:
        names = available_engines()
        assert "pdf-text-layer" in names

    def test_prefers_the_text_layer_for_a_pdf(self, digital_pdf: bytes) -> None:
        """Dispatch order is the accuracy decision - assert it, do not assume it."""
        result = recognise_sync(digital_pdf, DocumentFormat.PDF)
        assert result.engine == "pdf-text-layer"

    def test_disabling_ocr_refuses_everything(self, digital_pdf: bytes) -> None:
        original = settings.ocr_enabled
        settings.ocr_enabled = False
        try:
            with pytest.raises(EngineUnavailableError):
                recognise_sync(digital_pdf, DocumentFormat.PDF)
        finally:
            settings.ocr_enabled = original
