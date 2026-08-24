"""GST engine tests.

Pure arithmetic, no database - so the cases can be exhaustive. This is the module
most likely to be wrong in a way nobody notices until a customer adds up a column
and finds it does not reconcile.

The assertions are mostly **identities** rather than expected constants: the tax
split must always reconstitute the total, the document total must always equal the
sum of its printed lines. Those must hold for any input, which makes them stronger
than checking one worked example.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.tax.gst import (
    GST_RATES,
    TaxTreatment,
    compute_document,
    compute_line,
    resolve_treatment,
    round_money,
    split_tax,
    state_code_from_gstin,
)

D = Decimal

# Values chosen to land on rounding boundaries and odd sub-units, which is where
# a naive split breaks.
AWKWARD = ["100.05", "33.33", "0.01", "999.99", "1234.56", "7.77", "2.5", "0.03"]


class TestTaxSplit:
    @pytest.mark.parametrize("taxable", AWKWARD)
    @pytest.mark.parametrize("rate", ["5", "12", "18", "28"])
    def test_intra_state_halves_reconstitute_the_total(self, taxable: str, rate: str) -> None:
        """CGST + SGST must equal the total tax exactly.

        Rounding each half independently is the obvious implementation and it is
        wrong: 18% of 100.05 is 18.01, whose halves round to 9.00 + 9.00 = 18.00.
        The invoice would then not add up. SGST absorbs the odd sub-unit instead.
        """
        split = split_tax(D(taxable), D(rate), TaxTreatment.INTRA_STATE)
        expected = round_money(D(taxable) * D(rate) / D("100"))

        assert split.cgst + split.sgst == expected
        assert split.igst == 0

    @pytest.mark.parametrize("taxable", AWKWARD)
    def test_inter_state_is_all_igst(self, taxable: str) -> None:
        split = split_tax(D(taxable), D("18"), TaxTreatment.INTER_STATE)
        assert split.igst == round_money(D(taxable) * D("18") / D("100"))
        assert split.cgst == 0
        assert split.sgst == 0

    @pytest.mark.parametrize("treatment", [TaxTreatment.EXPORT, TaxTreatment.EXEMPT])
    def test_export_and_exempt_carry_no_tax(self, treatment: TaxTreatment) -> None:
        """Both are untaxed, but they are distinct: an export is zero-rated and
        still reportable, an exempt supply is neither."""
        assert split_tax(D("1000"), D("18"), treatment).total == 0

    def test_zero_rate_produces_no_tax(self) -> None:
        assert split_tax(D("1000"), D("0"), TaxTreatment.INTRA_STATE).total == 0

    @pytest.mark.parametrize("rate", [str(r) for r in GST_RATES])
    def test_every_statutory_slab_splits_cleanly(self, rate: str) -> None:
        split = split_tax(D("1000"), D(rate), TaxTreatment.INTRA_STATE)
        assert split.cgst + split.sgst == round_money(D("1000") * D(rate) / D("100"))

    def test_rounding_is_half_up_not_bankers(self) -> None:
        """0.125 must become 0.13. Python's default ROUND_HALF_EVEN gives 0.12,
        which contradicts every invoice and tax authority."""
        assert split_tax(D("2.5"), D("5"), TaxTreatment.INTER_STATE).igst == D("0.13")
        assert round_money(D("0.125")) == D("0.13")
        assert round_money(D("0.135")) == D("0.14")


class TestLineComputation:
    def test_discount_reduces_the_taxable_base(self) -> None:
        """GST is levied on the price actually charged.

        Taxing the gross and discounting afterwards would overcharge the customer
        and overstate the liability.
        """
        line = compute_line(
            quantity=D("10"),
            unit_price=D("100"),
            tax_rate=D("18"),
            treatment=TaxTreatment.INTRA_STATE,
            discount_percent=D("10"),
        )
        assert line.gross == D("1000.0000")
        assert line.discount_amount == D("100.0000")
        assert line.taxable == D("900.0000")
        assert line.tax_amount == D("162.0000")  # 18% of 900, not of 1000
        assert line.total == D("1062.0000")

    def test_absolute_discount_overrides_percentage(self) -> None:
        """An absolute figure is a deliberate override; a percentage is a default."""
        line = compute_line(
            quantity=D("1"),
            unit_price=D("1000"),
            tax_rate=D("18"),
            treatment=TaxTreatment.INTRA_STATE,
            discount_percent=D("50"),
            discount_amount=D("100"),
        )
        assert line.discount_amount == D("100.0000")
        assert line.taxable == D("900.0000")

    def test_discount_cannot_exceed_the_line(self) -> None:
        """Otherwise the taxable base goes negative and the tax becomes a credit."""
        line = compute_line(
            quantity=D("1"),
            unit_price=D("100"),
            tax_rate=D("18"),
            treatment=TaxTreatment.INTRA_STATE,
            discount_amount=D("500"),
        )
        assert line.discount_amount == D("100.0000")
        assert line.taxable == 0
        assert line.tax_amount == 0

    def test_fractional_quantity_is_exact(self) -> None:
        """Sold by weight or by the hour."""
        line = compute_line(
            quantity=D("2.5"),
            unit_price=D("40"),
            tax_rate=D("5"),
            treatment=TaxTreatment.INTRA_STATE,
        )
        assert line.gross == D("100.0000")
        assert line.tax_amount == D("5.0000")

    def test_line_identities_hold(self) -> None:
        line = compute_line(
            quantity=D("7"),
            unit_price=D("12.55"),
            tax_rate=D("12"),
            treatment=TaxTreatment.INTRA_STATE,
            discount_percent=D("3"),
        )
        assert line.taxable == line.gross - line.discount_amount
        assert line.tax_amount == line.cgst + line.sgst + line.igst
        assert line.total == line.taxable + line.tax_amount


class TestDocumentTotals:
    @pytest.fixture
    def lines(self) -> list:
        """Mixed rates and awkward unit prices."""
        return [
            compute_line(
                quantity=D("3"),
                unit_price=D("33.33"),
                tax_rate=D("18"),
                treatment=TaxTreatment.INTRA_STATE,
            ),
            compute_line(
                quantity=D("7"),
                unit_price=D("12.55"),
                tax_rate=D("12"),
                treatment=TaxTreatment.INTRA_STATE,
            ),
            compute_line(
                quantity=D("1"),
                unit_price=D("999.99"),
                tax_rate=D("5"),
                treatment=TaxTreatment.INTRA_STATE,
                discount_percent=D("5"),
            ),
        ]

    def test_total_equals_the_sum_of_printed_lines(self, lines: list) -> None:
        """The property a customer with a calculator actually checks.

        Re-deriving tax from the document subtotal can disagree with the printed
        lines by a sub-unit, so the totals sum the already-rounded line figures.
        """
        doc = compute_document(lines)
        assert doc.tax_total == sum(line.tax_amount for line in lines)
        assert doc.taxable_total == sum(line.taxable for line in lines)
        assert doc.subtotal == sum(line.gross for line in lines)

    def test_document_identities_hold(self, lines: list) -> None:
        doc = compute_document(lines)
        assert doc.cgst_total + doc.sgst_total + doc.igst_total == doc.tax_total
        assert doc.taxable_total == doc.subtotal - doc.discount_total
        assert doc.grand_total == doc.taxable_total + doc.tax_total + doc.round_off

    def test_round_to_whole_yields_an_integer(self, lines: list) -> None:
        doc = compute_document(lines, round_to_whole=True)
        assert doc.grand_total == doc.grand_total.to_integral_value()

    def test_round_off_closes_the_gap_exactly(self, lines: list) -> None:
        """`round_off` must be postable to its own ledger account.

        If it did not exactly close the difference, the journal entry the invoice
        posts would not balance and could not be written at all.
        """
        exact = compute_document(lines).grand_total
        rounded = compute_document(lines, round_to_whole=True)
        assert exact + rounded.round_off == rounded.grand_total
        assert abs(rounded.round_off) < 1

    def test_empty_document_is_all_zero(self) -> None:
        doc = compute_document([])
        assert doc.grand_total == 0
        assert doc.tax_total == 0

    def test_mixed_treatments_keep_columns_separate(self) -> None:
        """An intra-state and an inter-state line on one document must not merge."""
        doc = compute_document(
            [
                compute_line(
                    quantity=D("1"),
                    unit_price=D("1000"),
                    tax_rate=D("18"),
                    treatment=TaxTreatment.INTRA_STATE,
                ),
                compute_line(
                    quantity=D("1"),
                    unit_price=D("1000"),
                    tax_rate=D("18"),
                    treatment=TaxTreatment.INTER_STATE,
                ),
            ]
        )
        assert doc.cgst_total == D("90.0000")
        assert doc.sgst_total == D("90.0000")
        assert doc.igst_total == D("180.0000")
        assert doc.tax_total == D("360.0000")


class TestTreatmentResolution:
    def test_same_state_is_intra_state(self) -> None:
        assert (
            resolve_treatment(seller_state_code="27", buyer_state_code="27")
            is TaxTreatment.INTRA_STATE
        )

    def test_different_state_is_inter_state(self) -> None:
        assert (
            resolve_treatment(seller_state_code="27", buyer_state_code="29")
            is TaxTreatment.INTER_STATE
        )

    def test_foreign_country_is_export(self) -> None:
        assert (
            resolve_treatment(seller_state_code="27", buyer_state_code=None, buyer_country="AE")
            is TaxTreatment.EXPORT
        )

    def test_missing_buyer_state_falls_back_to_intra_state(self) -> None:
        """The conservative default for a domestic sale.

        It produces a CGST/SGST split, correct for the large majority of
        small-business transactions, and a wrong split is a filing correction
        rather than an under-collection.
        """
        assert (
            resolve_treatment(seller_state_code="27", buyer_state_code=None)
            is TaxTreatment.INTRA_STATE
        )

    def test_exempt_flag_overrides_geography(self) -> None:
        assert (
            resolve_treatment(seller_state_code="27", buyer_state_code="29", is_exempt=True)
            is TaxTreatment.EXEMPT
        )


class TestGstinParsing:
    def test_extracts_state_code(self) -> None:
        assert state_code_from_gstin("29AABCU9603R1ZM") == "29"

    @pytest.mark.parametrize("value", [None, "", "X", "XX9999", "ABCDEFGH"])
    def test_returns_none_for_unusable_input(self, value: str | None) -> None:
        assert state_code_from_gstin(value) is None
