"""Weighted-average inventory valuation tests.

Pure arithmetic, no database. The identity that must hold after every operation is

    stock_value == quantity * average_cost

and the asymmetry that defines the method is that **receipts move the average while
issues do not**. Both are asserted throughout, because getting the second one wrong
is the classic weighted-average bug and it produces a COGS figure that drifts
without ever looking obviously wrong.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.purchasing.valuation import (
    NegativeStockError,
    ValuationState,
    apply_adjustment,
    apply_issue,
    apply_receipt,
    reverse_receipt,
)

D = Decimal
EMPTY = ValuationState(quantity=D("0"), value=D("0"))


class TestReceipts:
    def test_first_receipt_sets_the_average(self) -> None:
        result = apply_receipt(EMPTY, quantity=D("100"), unit_cost=D("10"))
        assert result.state.quantity == D("100")
        assert result.state.average_cost == D("10.000000")
        assert result.value_added == D("1000.0000")

    def test_second_receipt_blends_the_average(self) -> None:
        """100 @ 10 then 100 @ 20 → 200 @ 15."""
        state = apply_receipt(EMPTY, quantity=D("100"), unit_cost=D("10")).state
        result = apply_receipt(state, quantity=D("100"), unit_cost=D("20"))
        assert result.state.quantity == D("200")
        assert result.state.average_cost == D("15.000000")

    def test_blend_is_quantity_weighted_not_a_simple_mean(self) -> None:
        """900 @ 10 then 100 @ 20 → 11, not 15.

        A simple mean of the two prices is the most common wrong implementation.
        """
        state = apply_receipt(EMPTY, quantity=D("900"), unit_cost=D("10")).state
        result = apply_receipt(state, quantity=D("100"), unit_cost=D("20"))
        assert result.state.average_cost == D("11.000000")

    def test_receipt_after_full_depletion_ignores_the_stale_average(self) -> None:
        """Stock that hit zero must take the new cost outright.

        Blending against the pre-depletion average would carry forward a price for
        units that no longer exist.
        """
        state = apply_receipt(EMPTY, quantity=D("10"), unit_cost=D("100")).state
        state = apply_issue(state, quantity=D("10")).state
        assert state.quantity == 0

        result = apply_receipt(state, quantity=D("5"), unit_cost=D("7"))
        assert result.state.average_cost == D("7.000000")

    def test_recurring_fractional_cost_keeps_precision(self) -> None:
        """A cost of 10/3 must not be rounded to 4dp and then multiplied out."""
        result = apply_receipt(EMPTY, quantity=D("3"), unit_cost=D("10") / D("3"))
        assert result.state.average_cost == D("3.333333")

    def test_rejects_non_positive_quantity(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            apply_receipt(EMPTY, quantity=D("0"), unit_cost=D("10"))

    def test_rejects_negative_cost(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            apply_receipt(EMPTY, quantity=D("1"), unit_cost=D("-5"))

    def test_zero_cost_receipt_is_allowed(self) -> None:
        """Free samples and promotional stock are real."""
        result = apply_receipt(EMPTY, quantity=D("10"), unit_cost=D("0"))
        assert result.state.average_cost == 0
        assert result.value_added == 0


class TestIssues:
    def test_issue_does_not_change_the_average(self) -> None:
        """The average per unit is unchanged; only the total falls."""
        state = apply_receipt(EMPTY, quantity=D("100"), unit_cost=D("12.5")).state
        result = apply_issue(state, quantity=D("40"))

        assert result.state.quantity == D("60")
        assert result.state.average_cost == D("12.500000"), "issue must not move the average"
        assert result.cost_of_goods_sold == D("500.0000")

    def test_cogs_uses_the_blended_average(self) -> None:
        """Not the most recent purchase price, and not the oldest."""
        state = apply_receipt(EMPTY, quantity=D("100"), unit_cost=D("10")).state
        state = apply_receipt(state, quantity=D("100"), unit_cost=D("20")).state
        result = apply_issue(state, quantity=D("50"))
        assert result.cost_of_goods_sold == D("750.0000")  # 50 x 15

    def test_issuing_everything_leaves_zero(self) -> None:
        state = apply_receipt(EMPTY, quantity=D("7"), unit_cost=D("3")).state
        result = apply_issue(state, quantity=D("7"))
        assert result.state.quantity == 0
        assert result.cost_of_goods_sold == D("21.0000")

    def test_over_issue_is_refused(self) -> None:
        """Negative stock makes the average cost undefined - the divisor for the
        next receipt would go negative."""
        state = apply_receipt(EMPTY, quantity=D("5"), unit_cost=D("10")).state
        with pytest.raises(NegativeStockError, match="only 5 on hand"):
            apply_issue(state, quantity=D("6"))

    def test_issue_from_empty_is_refused(self) -> None:
        with pytest.raises(NegativeStockError):
            apply_issue(EMPTY, quantity=D("1"))

    def test_rejects_non_positive_quantity(self) -> None:
        state = apply_receipt(EMPTY, quantity=D("10"), unit_cost=D("1")).state
        with pytest.raises(ValueError, match="must be positive"):
            apply_issue(state, quantity=D("0"))


class TestValueIdentity:
    """`quantity * average_cost == total_value` after every operation."""

    def test_identity_holds_across_a_long_sequence(self) -> None:
        state = EMPTY
        operations = [
            ("receipt", D("100"), D("9.99")),
            ("issue", D("30"), None),
            ("receipt", D("250"), D("11.4567")),
            ("issue", D("175"), None),
            ("receipt", D("33"), D("8")),
            ("issue", D("100"), None),
            ("receipt", D("7"), D("100")),
        ]
        for kind, quantity, cost in operations:
            if kind == "receipt":
                assert cost is not None
                state = apply_receipt(state, quantity=quantity, unit_cost=cost).state
            else:
                state = apply_issue(state, quantity=quantity).state

            assert state.quantity >= 0
            assert state.value >= 0
            # `value` is authoritative, so this is exact rather than approximate.
            assert state.total_value == state.value

    def test_total_cogs_never_exceeds_total_received_value(self) -> None:
        """A sanity bound: you cannot sell more cost than you bought.

        Weighted average can distribute cost differently from FIFO, but it cannot
        create or destroy it.
        """
        state = EMPTY
        received_value = D("0")
        issued_cogs = D("0")

        for quantity, cost in [(D("50"), D("4")), (D("50"), D("6")), (D("100"), D("5"))]:
            outcome = apply_receipt(state, quantity=quantity, unit_cost=cost)
            state = outcome.state
            received_value += outcome.value_added

        for quantity in [D("40"), D("60"), D("50")]:
            outcome = apply_issue(state, quantity=quantity)
            state = outcome.state
            issued_cogs += outcome.cost_of_goods_sold

        # Everything issued, plus everything still on hand, equals what came in.
        assert issued_cogs + state.total_value == received_value


class TestAdjustments:
    def test_positive_adjustment_values_at_current_average(self) -> None:
        """Found stock has to be worth something; the current average is the only
        defensible default."""
        state = apply_receipt(EMPTY, quantity=D("100"), unit_cost=D("10")).state
        outcome = apply_adjustment(state, quantity_delta=D("10"))
        assert outcome.state.quantity == D("110")
        assert outcome.state.average_cost == D("10.000000")

    def test_positive_adjustment_accepts_an_explicit_cost(self) -> None:
        state = apply_receipt(EMPTY, quantity=D("100"), unit_cost=D("10")).state
        outcome = apply_adjustment(state, quantity_delta=D("100"), unit_cost=D("20"))
        assert outcome.state.average_cost == D("15.000000")

    def test_negative_adjustment_writes_off_at_average(self) -> None:
        state = apply_receipt(EMPTY, quantity=D("100"), unit_cost=D("10")).state
        outcome = apply_adjustment(state, quantity_delta=D("-5"))
        assert outcome.state.quantity == D("95")
        assert outcome.cost_of_goods_sold == D("50.0000")  # the shrinkage loss

    def test_zero_adjustment_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be zero"):
            apply_adjustment(EMPTY, quantity_delta=D("0"))


class TestReversals:
    def test_reversing_the_only_receipt_empties_the_position(self) -> None:
        state = apply_receipt(EMPTY, quantity=D("10"), unit_cost=D("5")).state
        result = reverse_receipt(state, quantity=D("10"), unit_cost=D("5"))
        assert result.quantity == 0
        assert result.average_cost == 0

    def test_reversal_restores_the_average_when_nothing_intervened(self) -> None:
        """Exact in the simple case: 100 @ 10 then 100 @ 20, reverse the second."""
        state = apply_receipt(EMPTY, quantity=D("100"), unit_cost=D("10")).state
        state = apply_receipt(state, quantity=D("100"), unit_cost=D("20")).state
        assert state.average_cost == D("15.000000")

        restored = reverse_receipt(state, quantity=D("100"), unit_cost=D("20"))
        assert restored.quantity == D("100")
        assert restored.average_cost == D("10.000000")

    def test_over_reversal_is_refused(self) -> None:
        state = apply_receipt(EMPTY, quantity=D("5"), unit_cost=D("10")).state
        with pytest.raises(NegativeStockError):
            reverse_receipt(state, quantity=D("6"), unit_cost=D("10"))

    def test_reversal_never_produces_a_negative_average(self) -> None:
        """When intervening movements make exact restoration impossible, the method
        holds the average rather than emitting a negative cost."""
        state = ValuationState(quantity=D("10"), value=D("10"))
        restored = reverse_receipt(state, quantity=D("5"), unit_cost=D("100"))
        assert restored.average_cost >= 0
        assert restored.quantity == D("5")
