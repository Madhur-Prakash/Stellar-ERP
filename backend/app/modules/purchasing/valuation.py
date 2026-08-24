"""Inventory valuation - weighted average cost.

**The choice.** Three methods are standard: FIFO, weighted average, and specific
identification. This uses **moving weighted average**, and the reason is
maintainability rather than theory.

FIFO requires storing every receipt as a cost *layer* and consuming layers in
order. That is more precise, and it is what a large distributor needs. But it means
a second table whose rows are partially consumed, a consumption algorithm that must
be exactly reversible when a receipt is cancelled, and a recalculation cascade when
a backdated receipt lands before layers that were already consumed. Every one of
those is a place for the books to diverge from reality.

Weighted average keeps two numbers per (product, warehouse): quantity and total
value. There are no layers to consume and nothing to partially unwind.

For a small business - the target here - the difference in reported profit between
FIFO and weighted average is immaterial, while the difference in ways the system
can silently break is not. Indian accounting standards (AS 2 / Ind AS 2) permit
either.

**Total value is authoritative; unit cost is derived.** This is the important
implementation decision, and the reverse of the obvious one. Storing the average
cost and multiplying by quantity does not reconcile against the ledger - see
:class:`ValuationState` for the worked example where 9,000 of stock reports as
8,999.9999. Carrying the total instead means ``value`` changes only by amounts that
were actually posted, so the stock report and the Inventory account cannot diverge.

**The arithmetic.** A receipt adds its value; an issue removes a share of the value
proportional to the share of the quantity leaving. Depleting the position releases
whatever value remains exactly, so no residue can sit against zero units.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Final, NamedTuple

#: Cost is carried at higher precision than a printed amount. A unit cost of
#: ₹3.333333 across 10,000 units differs from ₹3.3333 by ₹0.33 - small, but it
#: accumulates across every issue and shows up as an unexplained inventory
#: variance.
COST_QUANTUM: Final = Decimal("0.000001")
MONEY_QUANTUM: Final = Decimal("0.0001")
ZERO: Final = Decimal("0")


def round_cost(value: Decimal) -> Decimal:
    """Quantise a unit cost to 6dp."""
    return value.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def round_value(value: Decimal) -> Decimal:
    """Quantise a monetary total to the storage precision."""
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


class ValuationState(NamedTuple):
    """A product's stock position in one warehouse.

    **``value`` is authoritative; ``average_cost`` is derived from it.**

    The obvious design stores the average cost and multiplies by quantity to get
    value. It does not reconcile. Three receipts of 100@50, 50@60, and 25@40 total
    exactly 9,000 in the ledger, but the average is 8,000/150 = 53.333333… which
    truncates, and 175 x 51.428571 comes back as 8,999.9999. A one-hundredth-of-a-
    paisa gap between the stock report and the Inventory account, growing with every
    movement, and impossible to explain to an auditor.

    Carrying the total instead makes the identity exact by construction: ``value``
    only ever changes by the amount actually posted to the ledger, so the two can
    never diverge. The unit cost - which is what an issue is costed at - is computed
    from it on demand.
    """

    quantity: Decimal
    #: Total cost of the stock on hand. Matches the Inventory ledger balance.
    value: Decimal

    @property
    def average_cost(self) -> Decimal:
        """Cost per unit, derived. Zero when there is no stock to divide by."""
        if self.quantity <= 0:
            return ZERO
        return round_cost(self.value / self.quantity)

    @property
    def total_value(self) -> Decimal:
        return self.value


class ReceiptOutcome(NamedTuple):
    state: ValuationState
    #: What the received goods were worth, at the cost they came in at.
    value_added: Decimal


class IssueOutcome(NamedTuple):
    state: ValuationState
    #: Cost of goods sold for this issue - the figure posted to the P&L.
    cost_of_goods_sold: Decimal


class NegativeStockError(ValueError):
    """Raised when an issue would drive quantity below zero.

    Permitting negative stock is a real option in some systems (it lets a sale
    proceed before the paperwork catches up), but it makes average cost undefined:
    the divisor goes negative and the next receipt produces a nonsensical average.
    Refusing is the honest behaviour - the fix is to record the receipt.
    """


def apply_receipt(
    current: ValuationState,
    *,
    quantity: Decimal,
    unit_cost: Decimal,
) -> ReceiptOutcome:
    """Blend a receipt into the moving average.

    Handles the two edge cases that break a naive implementation:

    * **Empty stock** - the divisor would be the received quantity alone, which is
      correct, but only if the existing average is ignored rather than averaged in.
    * **Negative existing quantity** - cannot arise here, because
      :func:`apply_issue` refuses to create it.
    """
    if quantity <= 0:
        raise ValueError("Receipt quantity must be positive")
    if unit_cost < 0:
        raise ValueError("Receipt cost cannot be negative")

    new_quantity = current.quantity + quantity
    value_added = round_value(quantity * unit_cost)

    # Values add exactly. No division, so nothing to round away - this is why the
    # stock value can never drift from the ledger.
    return ReceiptOutcome(
        state=ValuationState(quantity=new_quantity, value=current.value + value_added),
        value_added=value_added,
    )


def apply_issue(current: ValuationState, *, quantity: Decimal) -> IssueOutcome:
    """Remove stock at the current average cost.

    The average is deliberately **unchanged**: an issue does not alter what the
    remaining units cost. Recomputing it here is the most common way a
    weighted-average implementation goes wrong.
    """
    if quantity <= 0:
        raise ValueError("Issue quantity must be positive")
    if quantity > current.quantity:
        raise NegativeStockError(f"Cannot issue {quantity} - only {current.quantity} on hand")

    remaining = current.quantity - quantity

    if remaining == 0:
        # Depleting the position releases exactly what is left, with no rounding -
        # otherwise a residue of a fraction of a paisa would sit against zero units
        # and never clear.
        cogs = current.value
        new_value = ZERO
    else:
        # Proportional to the value on hand rather than quantity x rounded average,
        # so the amount removed from `value` is exactly the amount posted to COGS.
        cogs = round_value(current.value * quantity / current.quantity)
        new_value = current.value - cogs

    return IssueOutcome(
        state=ValuationState(quantity=remaining, value=new_value),
        cost_of_goods_sold=cogs,
    )


def apply_adjustment(
    current: ValuationState,
    *,
    quantity_delta: Decimal,
    unit_cost: Decimal | None = None,
) -> ReceiptOutcome | IssueOutcome:
    """Stock-take correction, in either direction.

    A positive delta needs a cost - found stock has to be valued at something, and
    the current average is the only defensible default. A negative delta is written
    off at the current average, which is what a shrinkage loss is worth.
    """
    if quantity_delta == 0:
        raise ValueError("Adjustment cannot be zero")

    if quantity_delta > 0:
        cost = unit_cost if unit_cost is not None else current.average_cost
        return apply_receipt(current, quantity=quantity_delta, unit_cost=cost)
    return apply_issue(current, quantity=-quantity_delta)


def reverse_receipt(
    current: ValuationState,
    *,
    quantity: Decimal,
    unit_cost: Decimal,
) -> ValuationState:
    """Undo a receipt - a cancelled goods receipt or a return to supplier.

    This is where weighted average is genuinely lossy, and it is worth being honest
    about: removing a receipt cannot perfectly restore the previous average, because
    the average has since blended with other movements. The reconstruction below
    backs out the receipt's own value and requantities, which is exact when no other
    receipt intervened and an approximation when one did.

    FIFO would restore precisely, by dropping the layer. That is the one real
    advantage it has here, and the trade was made deliberately - see the module
    docstring.
    """
    remaining = current.quantity - quantity
    if remaining < 0:
        raise NegativeStockError(f"Cannot reverse {quantity} - only {current.quantity} on hand")
    if remaining == 0:
        return ValuationState(quantity=ZERO, value=ZERO)

    removed_value = round_value(quantity * unit_cost)
    restored_value = current.value - removed_value

    if restored_value <= 0:
        # The receipt was worth more than everything on hand, so other movements
        # have changed the picture. Fall back to the proportional share rather than
        # emitting a negative value.
        return ValuationState(
            quantity=remaining,
            value=round_value(current.value * remaining / current.quantity),
        )

    return ValuationState(quantity=remaining, value=restored_value)
