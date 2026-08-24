"""OCR field-extraction tests.

Pure text in, structured fields out - no OCR engine, no database. That is the point
of keeping extraction pure: the product logic can be tested exhaustively without a
model download or a scanner.

The realistic fixture below is what Tesseract actually produces from a GST invoice:
inconsistent spacing, a mangled character or two, and labels not aligned with their
values. Testing against clean synthetic text would prove nothing.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.modules.ocr.extraction import (
    HIGH_CONFIDENCE,
    SUBTOTAL_PATTERN,
    TOTAL_PATTERN,
    FieldSource,
    extract_amount,
    extract_document,
    extract_gstin,
    extract_invoice_number,
    extract_supplier_name,
    parse_amount,
    parse_date,
)

D = Decimal
TODAY = dt.date(2026, 7, 29)

# What Tesseract typically returns for a GST invoice: ragged spacing, a stray
# character, labels separated from values.
REAL_INVOICE = """
MUMBAI WHOLESALE TRADERS
Shop 14, Kalbadevi Road, Mumbai 400002
GSTIN: 27AABCU9603R1ZM    PAN: AABCU9603R
Phone: 022-2345 6789

TAX INVOICE

Invoice No: MW/2026-27/0142        Date: 15/07/2026
Due Date: 14/08/2026

Description              HSN     Qty    Rate      Amount
Widget Assembly A        8483    100    450.00    45,000.00
Mounting Bracket         7318     50    120.00     6,000.00

                              Taxable Value:     51,000.00
                              CGST @ 9%:          4,590.00
                              SGST @ 9%:          4,590.00
                              Total Tax:          9,180.00
                              Grand Total:       60,180.00
"""


class TestAmountParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1000", D("1000")),
            ("1,000", D("1000")),
            # Indian grouping.
            ("1,20,000", D("120000")),
            ("1,20,000.50", D("120000.50")),
            # Western grouping.
            ("120,000.50", D("120000.50")),
            ("₹ 5,000", D("5000")),
            ("Rs. 5,000", D("5000")),
            ("Rs 5000.25", D("5000.25")),
            ("INR 999", D("999")),
            ("  42.00  ", D("42")),
        ],
    )
    def test_parses_both_grouping_conventions(self, raw: str, expected: Decimal) -> None:
        """Indian and Western grouping both appear, sometimes on one invoice."""
        assert parse_amount(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "abc", "₹", "-500"])
    def test_rejects_unusable_input(self, raw: str) -> None:
        """A negative amount on a purchase invoice means the parse went wrong."""
        assert parse_amount(raw) is None


class TestDateParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Date: 15/07/2026", dt.date(2026, 7, 15)),
            ("Date: 15-07-2026", dt.date(2026, 7, 15)),
            ("Date: 15.07.2026", dt.date(2026, 7, 15)),
            ("Date: 2026-07-15", dt.date(2026, 7, 15)),
            ("Date: 15 Jul 2026", dt.date(2026, 7, 15)),
            ("Date: 15-July-2026", dt.date(2026, 7, 15)),
            ("Date: 15/07/26", dt.date(2026, 7, 15)),
        ],
    )
    def test_parses_the_formats_invoices_use(self, text: str, expected: dt.date) -> None:
        assert parse_date(text, today=TODAY) == expected

    def test_is_day_first_not_month_first(self) -> None:
        """`03/04/2026` is 3 April in India, 4 March in the US.

        The locale decides. Guessing per document would silently file invoices in
        the wrong month, which is not recoverable once a period is closed.
        """
        assert parse_date("03/04/2026", today=TODAY) == dt.date(2026, 4, 3)

    def test_rejects_implausible_dates(self) -> None:
        """A far-future date is a misread, not a post-dated invoice."""
        assert parse_date("Date: 15/07/2099", today=TODAY) is None
        assert parse_date("Date: 15/07/1985", today=TODAY) is None

    def test_rejects_impossible_dates(self) -> None:
        assert parse_date("Date: 32/13/2026", today=TODAY) is None

    def test_returns_none_when_absent(self) -> None:
        assert parse_date("no date here", today=TODAY) is None


class TestGstinExtraction:
    def test_finds_a_gstin(self) -> None:
        result = extract_gstin("GSTIN: 27AABCU9603R1ZM")
        assert result is not None
        assert result.value == "27AABCU9603R1ZM"
        assert result.source is FieldSource.PATTERN
        assert result.is_high_confidence

    def test_normalises_case(self) -> None:
        result = extract_gstin("gstin 27aabcu9603r1zm")
        assert result is not None
        assert result.value == "27AABCU9603R1ZM"

    def test_finds_it_without_a_label(self) -> None:
        """The format is specific enough that no label is needed."""
        result = extract_gstin("...27AABCU9603R1ZM...")
        assert result is not None

    def test_does_not_match_a_bare_pan(self) -> None:
        """A PAN is 10 characters and embedded in every GSTIN - it must not be
        mistaken for one."""
        assert extract_gstin("PAN: AABCU9603R") is None

    def test_returns_none_when_absent(self) -> None:
        assert extract_gstin("no tax id on this document") is None

    @pytest.mark.parametrize(
        "recognised",
        [
            "GSTIN: 27 AABCU9603R1ZM",
            "GSTIN: 27AABCU9603R1 ZM",
            "GSTIN: 2 7 A A B C U 9 6 0 3 R 1 Z M",
        ],
    )
    def test_tolerates_spaces_the_engine_inserted(self, recognised: str) -> None:
        """Tesseract really does split a printed GSTIN.

        Reading a rendered invoice returns `27 AABCU9603R1ZM` - engines break long
        alphanumeric runs at wide letter spacing. Missing those would cost the
        reviewer the one field that auto-matches the supplier.
        """
        result = extract_gstin(recognised)
        assert result is not None
        assert result.value == "27AABCU9603R1ZM"

    def test_a_despaced_match_scores_slightly_lower(self) -> None:
        """It rests on an extra assumption - that the spaces were not printed."""
        clean = extract_gstin("GSTIN: 27AABCU9603R1ZM")
        spaced = extract_gstin("GSTIN: 27 AABCU9603R1ZM")
        assert clean is not None and spaced is not None
        assert spaced.confidence < clean.confidence
        assert spaced.is_high_confidence  # still trustworthy enough to pre-fill

    def test_despacing_still_enforces_the_shape(self) -> None:
        """Relaxing contiguity must not relax the format.

        15 spaced characters that are not GSTIN-shaped are not a GSTIN - otherwise
        this would match any long reference number on the page.
        """
        assert extract_gstin("REF: AB CDEFG 1234 X 1 Y 2") is None
        assert extract_gstin("2 7 A A B C U 9 6 0 3 R 1 Q M") is None  # 'Q' not 'Z'


class TestInvoiceNumberExtraction:
    @pytest.mark.parametrize(
        "text",
        [
            "Invoice No: MW/2026-27/0142",
            "Invoice Number: MW/2026-27/0142",
            "Invoice #: MW/2026-27/0142",
            "INV No. MW/2026-27/0142",
            "Bill No: MW/2026-27/0142",
            "Tax Invoice No : MW/2026-27/0142",
        ],
    )
    def test_finds_it_next_to_any_common_label(self, text: str) -> None:
        result = extract_invoice_number(text)
        assert result is not None
        assert result.value == "MW/2026-27/0142"

    def test_purely_numeric_scores_lower(self) -> None:
        """It could be a page number that happened to follow the label."""
        numeric = extract_invoice_number("Invoice No: 1042")
        alphanumeric = extract_invoice_number("Invoice No: INV-1042")
        assert numeric is not None and alphanumeric is not None
        assert numeric.confidence < alphanumeric.confidence

    def test_requires_a_label(self) -> None:
        """Scanning for 'something reference-shaped' finds HSN codes and phone
        numbers far more often than invoice numbers."""
        assert extract_invoice_number("MW/2026-27/0142") is None

    def test_strips_trailing_punctuation(self) -> None:
        result = extract_invoice_number("Invoice No: INV-99.")
        assert result is not None
        assert result.value == "INV-99"


class TestAmountExtraction:
    def test_prefers_the_last_match(self) -> None:
        """'Total' appears as a column header before it appears as the figure."""
        text = "Amount Total\n...\nGrand Total: 60,180.00"
        result = extract_amount(text, TOTAL_PATTERN)
        assert result is not None
        assert result.value == D("60180.00")

    def test_skips_unparseable_matches(self) -> None:
        text = "Total: ----\nTotal: 500.00"
        result = extract_amount(text, TOTAL_PATTERN)
        assert result is not None
        assert result.value == D("500.00")

    def test_returns_none_when_no_label_matches(self) -> None:
        assert extract_amount("nothing relevant", SUBTOTAL_PATTERN) is None


class TestSupplierName:
    def test_takes_the_letterhead_line(self) -> None:
        result = extract_supplier_name(REAL_INVOICE)
        assert result is not None
        assert result.value == "MUMBAI WHOLESALE TRADERS"

    def test_scored_low_because_it_is_a_heuristic(self) -> None:
        """The review UI must ask rather than pre-fill this."""
        result = extract_supplier_name(REAL_INVOICE)
        assert result is not None
        assert result.needs_review
        assert result.source is FieldSource.INFERRED

    def test_skips_boilerplate_headers(self) -> None:
        text = "TAX INVOICE\nGSTIN: 27AABCU9603R1ZM\nACME TRADING CO\n"
        result = extract_supplier_name(text)
        assert result is not None
        assert result.value == "ACME TRADING CO"

    def test_skips_mostly_numeric_lines(self) -> None:
        text = "022-2345 6789\n400002\nREAL COMPANY NAME\n"
        result = extract_supplier_name(text)
        assert result is not None
        assert result.value == "REAL COMPANY NAME"


class TestWholeDocument:
    @pytest.fixture
    def parsed(self):
        return extract_document(REAL_INVOICE, today=TODAY)

    def test_extracts_every_key_field(self, parsed) -> None:
        assert parsed.supplier_gstin is not None
        assert parsed.supplier_gstin.value == "27AABCU9603R1ZM"
        assert parsed.invoice_number is not None
        assert parsed.invoice_number.value == "MW/2026-27/0142"
        assert parsed.invoice_date is not None
        assert parsed.invoice_date.value == dt.date(2026, 7, 15)
        assert parsed.total_amount is not None
        assert parsed.total_amount.value == D("60180.00")
        assert parsed.subtotal is not None
        assert parsed.subtotal.value == D("51000.00")

    def test_totals_reconcile(self, parsed) -> None:
        """51,000 + 9,180 = 60,180."""
        assert parsed.totals_reconcile

    def test_reconciling_totals_raise_confidence(self, parsed) -> None:
        """Arithmetic agreeing is strong independent evidence that all three
        numbers were read correctly - a single wrong digit would break it."""
        assert parsed.total_amount is not None
        assert parsed.total_amount.confidence >= HIGH_CONFIDENCE

        # Same document with the total corrupted: no boost.
        broken = extract_document(REAL_INVOICE.replace("60,180.00", "69,180.00"), today=TODAY)
        assert not broken.totals_reconcile
        assert broken.total_amount is not None
        assert broken.total_amount.confidence < HIGH_CONFIDENCE

    def test_confidence_is_capped_below_certainty(self, parsed) -> None:
        """A consistently misread column could still reconcile, so never 1.0."""
        assert parsed.total_amount is not None
        assert parsed.total_amount.confidence <= D("0.97")

    def test_flags_the_fields_a_human_should_check(self, parsed) -> None:
        # The supplier name is a heuristic, so it always needs review.
        assert "supplier_name" in parsed.fields_needing_review
        # The GSTIN matched a strict pattern, so it does not.
        assert "supplier_gstin" not in parsed.fields_needing_review

    def test_overall_confidence_is_a_mean_of_what_was_found(self, parsed) -> None:
        assert D("0") < parsed.overall_confidence <= D("1")

    def test_empty_input_yields_nothing_rather_than_guesses(self) -> None:
        empty = extract_document("", today=TODAY)
        assert empty.supplier_gstin is None
        assert empty.total_amount is None
        assert empty.overall_confidence == D("0")
        assert not empty.totals_reconcile

    def test_garbage_input_does_not_invent_fields(self) -> None:
        """A blank or failed scan must not produce a plausible-looking document."""
        noise = extract_document("~~~ ### ??? \n |||| \n ....", today=TODAY)
        assert noise.supplier_gstin is None
        assert noise.invoice_number is None
        assert noise.total_amount is None

    def test_inter_state_invoice_with_igst(self) -> None:
        """A Karnataka supplier billing Maharashtra charges IGST, not CGST/SGST."""
        text = """
        BENGALURU SUPPLIES PVT LTD
        GSTIN: 29AABCU9603R1ZM

        Invoice No: BS-2026-88     Date: 20/07/2026

        Taxable Value: 10,000.00
        IGST @ 18%: 1,800.00
        Grand Total: 11,800.00
        """
        parsed = extract_document(text, today=TODAY)
        assert parsed.supplier_gstin is not None
        # State code 29 = Karnataka, which is what makes it inter-state.
        assert parsed.supplier_gstin.value.startswith("29")
        assert parsed.totals_reconcile
        assert parsed.total_amount is not None
        assert parsed.total_amount.value == D("11800.00")
