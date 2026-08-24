"""Turning "this quarter" into the dates a balance sheet actually needs.

**A balance sheet is a position at a moment, not a flow over a window**, and that is the one
thing to hold on to here. A P&L covers 1 April to 30 June; a balance sheet is *as at* 30
June. Asking for "the balance sheet for Q1" therefore resolves to a single date - the last
day of that quarter - and the period only really tells you which date that is.

So what makes a period genuinely useful on this statement is the **comparative**: the
position at the period end beside the position the day before it opened. That is how a
balance sheet is presented on paper, and the pair is what shows movement - cash up, a loan
down - which a single column cannot.

Resolved on the server rather than in each client, because otherwise the web app and the
desktop app each decide what "this quarter" means against an organization whose year may
start in April, and the two answers diverge without anything failing.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from app.modules.analytics.periods import (
    DateRange,
    fiscal_year_start,
    quarter_start,
)


class StatementPeriod(StrEnum):
    """Named windows offered for a balance sheet.

    Deliberately not reusing `analytics.Period`: those are to-date windows built for a
    dashboard ("this month so far"), and half of them - last 30 days, last 12 months - are
    meaningless for a statement that reports a closing position. Named ones here all end on a
    quarter or year boundary, except `TO_DATE`, which is the honest name for "as things stand
    right now".
    """

    TO_DATE = "to_date"
    THIS_QUARTER = "this_quarter"
    LAST_QUARTER = "last_quarter"
    THIS_FISCAL_YEAR = "this_fiscal_year"
    LAST_FISCAL_YEAR = "last_fiscal_year"
    #: Caller supplies `as_of` (and optionally `compare_to`) itself.
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS: dict[StatementPeriod, str] = {
    StatementPeriod.TO_DATE: "As things stand",
    StatementPeriod.THIS_QUARTER: "This quarter",
    StatementPeriod.LAST_QUARTER: "Last quarter",
    StatementPeriod.THIS_FISCAL_YEAR: "This financial year",
    StatementPeriod.LAST_FISCAL_YEAR: "Last financial year",
    StatementPeriod.CUSTOM: "Custom dates",
}


def _quarter_span(day: dt.date, fiscal_start_month: int) -> DateRange:
    """The fiscal quarter containing ``day``, start to end."""
    start = quarter_start(day, fiscal_start_month)
    # Three months on, minus a day: the last day of the quarter, whatever its length.
    month = start.month + 3
    year = start.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return DateRange(start, dt.date(year, month, 1) - dt.timedelta(days=1))


def _fiscal_span(day: dt.date, fiscal_start_month: int) -> DateRange:
    start = fiscal_year_start(day, fiscal_start_month)
    return DateRange(
        start,
        dt.date(start.year + 1, start.month, 1) - dt.timedelta(days=1),
    )


def resolve_statement_period(
    period: StatementPeriod,
    *,
    today: dt.date,
    fiscal_start_month: int,
) -> tuple[dt.date, dt.date | None]:
    """``(as_of, compare_to)`` for a named period.

    ``compare_to`` is the day *before* the window opens - the closing position of whatever
    came before, which is the opening position of this one. Using the first day of the window
    instead would double-count everything posted on that day.

    A window that has not finished yet is cut off at ``today`` rather than reported to its
    future end date: a balance sheet dated 31 March when it is 2 February would be a
    statement about a date that has not happened, showing figures as though it had.
    """
    if period is StatementPeriod.CUSTOM:
        raise ValueError("CUSTOM carries no dates of its own - the caller supplies them")

    if period is StatementPeriod.TO_DATE:
        return today, None

    match period:
        case StatementPeriod.THIS_QUARTER:
            span = _quarter_span(today, fiscal_start_month)
        case StatementPeriod.LAST_QUARTER:
            this = _quarter_span(today, fiscal_start_month)
            span = _quarter_span(this.start - dt.timedelta(days=1), fiscal_start_month)
        case StatementPeriod.THIS_FISCAL_YEAR:
            span = _fiscal_span(today, fiscal_start_month)
        case _:
            this = _fiscal_span(today, fiscal_start_month)
            span = _fiscal_span(this.start - dt.timedelta(days=1), fiscal_start_month)

    as_of = min(span.end, today)
    return as_of, span.start - dt.timedelta(days=1)
