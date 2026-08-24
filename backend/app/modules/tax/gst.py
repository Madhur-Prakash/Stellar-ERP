"""GST calculation and document totals.

Isolated from the models and services because tax arithmetic is the part most
likely to be wrong, most likely to change with legislation, and the only part that
can be tested exhaustively without a database.

**Indian GST in one paragraph.** A sale carries one total tax rate (5%, 12%, 18%,
28%), but *how it splits* depends on geography. Within a state it is **intra-state**
and splits evenly into CGST (central) and SGST (state). Across states it is
**inter-state** and the whole amount is IGST. The customer pays the same either
way; the split determines which government gets it, so it must appear as separate
ledger lines and separate columns on the invoice.

**Rounding is done once, per line, on the tax.** Two alternatives are worse:

* *Round only the invoice total.* Then the sum of the printed line taxes does not
  equal the printed total tax, and the invoice visibly fails to add up.
* *Round at every intermediate step.* Error accumulates across lines.

So: line amounts stay exact at 4dp, each line's tax is quantised to 2dp, and the
document total is the sum of already-rounded parts. That way every printed figure
is the sum of the printed figures above it - which is the only property that
survives a customer with a calculator.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final, NamedTuple

#: Currency presentation precision. Amounts are *stored* at 4dp; tax is rounded to
#: 2dp because that is what appears on a document and what a customer verifies.
MONEY_QUANTUM: Final = Decimal("0.01")
EXACT_QUANTUM: Final = Decimal("0.0001")
ZERO: Final = Decimal("0.0000")

#: The statutory GST slabs. 0 covers exempt and zero-rated supplies.
GST_RATES: Final[tuple[Decimal, ...]] = (
    Decimal("0"),
    Decimal("0.25"),
    Decimal("3"),
    Decimal("5"),
    Decimal("12"),
    Decimal("18"),
    Decimal("28"),
)


class TaxTreatment(StrEnum):
    """How a supply is taxed, which decides the split."""

    #: Same state as the seller: CGST + SGST, half each.
    INTRA_STATE = "intra_state"
    #: Different state: IGST, whole amount.
    INTER_STATE = "inter_state"
    #: Outside India. Zero-rated, but must still be reported, so it is not the
    #: same thing as a 0% domestic supply.
    EXPORT = "export"
    #: Explicitly exempt or nil-rated.
    EXEMPT = "exempt"


def round_money(value: Decimal) -> Decimal:
    """Quantise to 2dp using ROUND_HALF_UP.

    Banker's rounding (Python's default, ROUND_HALF_EVEN) is *more* statistically
    neutral but contradicts what every invoice, tax authority, and customer
    expects: 0.125 must become 0.13, not 0.12.
    """
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def to_exact(value: Decimal) -> Decimal:
    """Quantise to the 4dp storage precision."""
    return value.quantize(EXACT_QUANTUM, rounding=ROUND_HALF_UP)


class LineTax(NamedTuple):
    """The computed tax breakdown for one line."""

    taxable_amount: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal

    @property
    def total(self) -> Decimal:
        return self.cgst + self.sgst + self.igst


class LineTotals(NamedTuple):
    """Everything derived from one line's inputs."""

    #: quantity x unit_price, before discount.
    gross: Decimal
    discount_amount: Decimal
    #: gross - discount. The base the tax applies to.
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    tax_amount: Decimal
    #: taxable + tax.
    total: Decimal


def compute_line(
    *,
    quantity: Decimal,
    unit_price: Decimal,
    tax_rate: Decimal,
    treatment: TaxTreatment,
    discount_percent: Decimal = ZERO,
    discount_amount: Decimal | None = None,
) -> LineTotals:
    """Compute one line's taxable base and tax split.

    ``discount_amount`` wins over ``discount_percent`` when both are supplied - an
    absolute figure is a deliberate override and a percentage is a default, so the
    specific one takes precedence.

    Discount reduces the **taxable base**, not the tax: GST is levied on the price
    actually charged. Computing tax on the gross and then discounting would
    overcharge the customer and overstate the liability.
    """
    gross = to_exact(quantity * unit_price)

    if discount_amount is not None:
        discount = to_exact(min(discount_amount, gross))
    elif discount_percent:
        discount = to_exact(gross * discount_percent / Decimal("100"))
    else:
        discount = ZERO

    taxable = to_exact(gross - discount)
    tax = split_tax(taxable, tax_rate, treatment)

    tax_amount = tax.total
    return LineTotals(
        gross=gross,
        discount_amount=discount,
        taxable=taxable,
        cgst=tax.cgst,
        sgst=tax.sgst,
        igst=tax.igst,
        tax_amount=tax_amount,
        total=to_exact(taxable + tax_amount),
    )


def split_tax(taxable: Decimal, rate: Decimal, treatment: TaxTreatment) -> LineTax:
    """Split a line's tax into CGST/SGST/IGST according to the treatment.

    The intra-state split halves the *rounded* total rather than rounding each half
    independently. Rounding halves separately can produce a pair that does not sum
    to the total (18% of 100.05 → 9.00 + 9.00 = 18.00, but the total is 18.01), and
    the invoice would not add up. Instead CGST takes half rounded down and SGST
    takes the remainder, so the two always reconstitute the total exactly.
    """
    if treatment in (TaxTreatment.EXPORT, TaxTreatment.EXEMPT) or rate == 0:
        return LineTax(taxable, ZERO, ZERO, ZERO)

    total_tax = round_money(taxable * rate / Decimal("100"))

    if treatment is TaxTreatment.INTER_STATE:
        return LineTax(taxable, ZERO, ZERO, total_tax)

    half = round_money(total_tax / Decimal("2"))
    # SGST absorbs any odd sub-unit so the pair sums to total_tax exactly.
    return LineTax(taxable, half, total_tax - half, ZERO)


class DocumentTotals(NamedTuple):
    """Aggregated totals for an invoice, quotation, or order."""

    subtotal: Decimal
    discount_total: Decimal
    taxable_total: Decimal
    cgst_total: Decimal
    sgst_total: Decimal
    igst_total: Decimal
    tax_total: Decimal
    #: Sub-unit adjustment applied to reach a whole-currency total, if requested.
    round_off: Decimal
    grand_total: Decimal


def compute_document(
    lines: Iterable[LineTotals],
    *,
    round_to_whole: bool = False,
) -> DocumentTotals:
    """Sum already-computed lines into document totals.

    Sums the *rounded* line taxes rather than re-deriving tax from the document
    subtotal. Re-deriving can disagree with the printed lines by a sub-unit, and a
    customer who adds up the column will find the discrepancy.

    ``round_to_whole`` produces the whole-rupee total common on Indian invoices.
    The adjustment is returned as ``round_off`` so it can be posted to its own
    ledger account - otherwise the journal entry would not balance.
    """
    subtotal = ZERO
    discount_total = ZERO
    taxable_total = ZERO
    cgst_total = ZERO
    sgst_total = ZERO
    igst_total = ZERO

    for line in lines:
        subtotal += line.gross
        discount_total += line.discount_amount
        taxable_total += line.taxable
        cgst_total += line.cgst
        sgst_total += line.sgst
        igst_total += line.igst

    tax_total = cgst_total + sgst_total + igst_total
    exact_total = to_exact(taxable_total + tax_total)

    round_off = ZERO
    grand_total = exact_total
    if round_to_whole:
        whole = exact_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        round_off = to_exact(whole - exact_total)
        grand_total = to_exact(whole)

    return DocumentTotals(
        subtotal=to_exact(subtotal),
        discount_total=to_exact(discount_total),
        taxable_total=to_exact(taxable_total),
        cgst_total=to_exact(cgst_total),
        sgst_total=to_exact(sgst_total),
        igst_total=to_exact(igst_total),
        tax_total=to_exact(tax_total),
        round_off=round_off,
        grand_total=grand_total,
    )


def resolve_treatment(
    *,
    seller_state_code: str | None,
    buyer_state_code: str | None,
    buyer_country: str = "IN",
    is_exempt: bool = False,
) -> TaxTreatment:
    """Decide the tax treatment from the two parties' locations.

    Falls back to intra-state when either state code is missing. That is the
    conservative default for a domestic sale: it produces a CGST/SGST split, which
    is correct for the overwhelming majority of small-business transactions, and an
    incorrect split is a filing correction rather than an under-collection.
    """
    if is_exempt:
        return TaxTreatment.EXEMPT
    if buyer_country.upper() != "IN":
        return TaxTreatment.EXPORT
    if not seller_state_code or not buyer_state_code:
        return TaxTreatment.INTRA_STATE
    if seller_state_code == buyer_state_code:
        return TaxTreatment.INTRA_STATE
    return TaxTreatment.INTER_STATE


def state_code_from_gstin(gstin: str | None) -> str | None:
    """Extract the state code from a GSTIN.

    A GSTIN's first two digits are the state code, so a customer's state - and
    therefore the CGST/SGST versus IGST decision - is derivable from the number
    they already gave you.
    """
    if not gstin or len(gstin) < 2 or not gstin[:2].isdigit():
        return None
    return gstin[:2]
