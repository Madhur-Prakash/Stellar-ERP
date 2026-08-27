"""Period arithmetic for analytics.

**This module is pure.** Dates in, dates out - no database, no ORM. It is separate
because this is where dashboards quietly lie, and the lies are all arithmetic:

**Comparing month-to-date against a whole previous month.** On the 3rd of the month
a naive dashboard compares three days of revenue against thirty and reports revenue
"down 90%". Every such dashboard is wrong for the first three weeks of every month.
:func:`previous_comparable` truncates the comparison window to the same number of
days, so day 3 is compared against days 1-3 of the previous month.

**Reporting a percentage change from zero.** If last month was ₹0 and this month is
₹50,000, the change is not +100%, and it is not +∞ - it is undefined, because there
is no base to compare against. :func:`percent_change` returns ``None``, and the UI
shows "no prior data" instead of a number that looks meaningful and is not.

**Assuming the year starts in January.** India's financial year starts in April, and
the organization stores its own start month. A "this year" figure computed on the
calendar year is simply a different number from the one the business files.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final

#: A percentage change is reported to one decimal place. More is false precision on
#: a figure that exists to be glanced at.
PERCENT_QUANTUM: Final = Decimal("0.1")


class Period(StrEnum):
    """The windows the dashboard offers.

    A closed set rather than free-form dates: these are the comparisons a business
    owner actually makes, each has a well-defined "previous comparable" period, and
    an arbitrary range does not.
    """

    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    THIS_QUARTER = "this_quarter"
    THIS_FISCAL_YEAR = "this_fiscal_year"
    LAST_30_DAYS = "last_30_days"
    LAST_12_MONTHS = "last_12_months"

    @property
    def label(self) -> str:
        return {
            Period.THIS_MONTH: "This month",
            Period.LAST_MONTH: "Last month",
            Period.THIS_QUARTER: "This quarter",
            Period.THIS_FISCAL_YEAR: "This financial year",
            Period.LAST_30_DAYS: "Last 30 days",
            Period.LAST_12_MONTHS: "Last 12 months",
        }[self]

    @property
    def is_to_date(self) -> bool:
        """Whether the window is still running.

        Drives the comparison rule: an in-progress period must be compared against
        a truncated one, a finished period against the whole of its predecessor.
        """
        return self in (Period.THIS_MONTH, Period.THIS_QUARTER, Period.THIS_FISCAL_YEAR)


@dataclass(frozen=True, slots=True)
class DateRange:
    """An inclusive date range.

    Inclusive at both ends because that is what the ledger means: an entry dated
    31 March belongs to the year ending 31 March. A half-open range invites the
    off-by-one that drops the last day of every period.
    """

    start: dt.date
    end: dt.date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"end {self.end} is before start {self.start}")

    @property
    def days(self) -> int:
        """Length in days, counting both endpoints."""
        return (self.end - self.start).days + 1

    def contains(self, day: dt.date) -> bool:
        return self.start <= day <= self.end


def _add_months(day: dt.date, months: int) -> dt.date:
    """Shift by whole months, clamping the day to the target month's length.

    31 January minus one month is 31 December, but 31 March minus one month cannot
    be 31 February - it clamps to the 28th or 29th. Naive day-arithmetic here is the
    source of the classic "the report skipped March" bug.
    """
    total = (day.year * 12 + day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def month_end(day: dt.date) -> dt.date:
    return dt.date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)


def fiscal_year_start(day: dt.date, start_month: int) -> dt.date:
    """The start of the fiscal year containing ``day``.

    With an April start, 15 February 2026 falls in the year that began 1 April
    2025 - the previous calendar year. Getting this backwards shifts every
    year-to-date figure by up to twelve months.
    """
    if not 1 <= start_month <= 12:
        raise ValueError(f"start_month must be 1-12, got {start_month}")

    year = day.year if day.month >= start_month else day.year - 1
    return dt.date(year, start_month, 1)


def quarter_start(day: dt.date, fiscal_start_month: int) -> dt.date:
    """The start of the fiscal quarter containing ``day``.

    Quarters are counted from the *fiscal* year start, not from January. For an
    April-start business, Q1 is April-June - which is what "this quarter" means to
    them, and what their filings use.
    """
    year_start = fiscal_year_start(day, fiscal_start_month)
    months_in = (day.year - year_start.year) * 12 + (day.month - year_start.month)
    return _add_months(year_start, (months_in // 3) * 3)


def resolve_period(period: Period, *, today: dt.date, fiscal_start_month: int = 4) -> DateRange:
    """Turn a named window into concrete dates.

    ``today`` is a parameter rather than read from the clock so that every caller -
    tests, a scheduled export, a request replayed for debugging - resolves the same
    dates for the same inputs.
    """
    match period:
        case Period.THIS_MONTH:
            return DateRange(month_start(today), today)
        case Period.LAST_MONTH:
            previous = _add_months(month_start(today), -1)
            return DateRange(previous, month_end(previous))
        case Period.THIS_QUARTER:
            return DateRange(quarter_start(today, fiscal_start_month), today)
        case Period.THIS_FISCAL_YEAR:
            return DateRange(fiscal_year_start(today, fiscal_start_month), today)
        case Period.LAST_30_DAYS:
            # 30 days *including* today, so the range is 30 days long, not 31.
            return DateRange(today - dt.timedelta(days=29), today)
        case Period.LAST_12_MONTHS:
            return DateRange(month_start(_add_months(today, -11)), today)


def previous_comparable(period: Period, current: DateRange) -> DateRange:
    """The window to compare ``current`` against.

    **The rule that matters:** an in-progress period is compared against the *same
    number of days* in its predecessor, not against the predecessor in full. On the
    3rd of the month, "this month" is three days; comparing it to a thirty-day month
    reports a 90% collapse in revenue that did not happen, and a dashboard that does
    this is misleading for most of every month.

    For a finished period the whole predecessor is the right comparison, because
    both windows are complete.
    """
    if period is Period.THIS_MONTH:
        start = _add_months(current.start, -1)
        # Truncate to the same day count, and never past the end of that month -
        # comparing 31 days of January against February is not possible.
        end = min(start + dt.timedelta(days=current.days - 1), month_end(start))
        return DateRange(start, end)

    if period is Period.THIS_QUARTER:
        start = _add_months(current.start, -3)
        same_length = start + dt.timedelta(days=current.days - 1)
        quarter_last_day = _add_months(start, 3) - dt.timedelta(days=1)
        return DateRange(start, min(same_length, quarter_last_day))

    if period is Period.THIS_FISCAL_YEAR:
        start = _add_months(current.start, -12)
        same_length = start + dt.timedelta(days=current.days - 1)
        year_last_day = _add_months(start, 12) - dt.timedelta(days=1)
        return DateRange(start, min(same_length, year_last_day))

    if period is Period.LAST_MONTH:
        start = _add_months(current.start, -1)
        return DateRange(start, month_end(start))

    if period is Period.LAST_12_MONTHS:
        return DateRange(_add_months(current.start, -12), _add_months(current.start, -1))

    # LAST_30_DAYS and anything else day-based: the immediately preceding window of
    # identical length.
    end = current.start - dt.timedelta(days=1)
    return DateRange(end - dt.timedelta(days=current.days - 1), end)


def percent_change(current: Decimal, previous: Decimal) -> Decimal | None:
    """Change from ``previous`` to ``current``, as a percentage.

    ``None`` when there is no meaningful base:

    * **Previous is zero.** Going from ₹0 to ₹50,000 is not "+100%" - the increase
      is infinite, and any finite number printed there is a fabrication. The caller
      shows "no prior data".
    * **Previous is negative.** A percentage change across a sign flip is
      arithmetically defined but not interpretable: a loss of ₹1,000 becoming a
      profit of ₹1,000 is "-200%", which reads as catastrophic. Expenses and revenue
      are non-negative in practice, so this only arises on net profit, which is
      exactly where the misreading would matter most.
    """
    if previous <= 0:
        return None
    change = (current - previous) / previous * Decimal("100")
    return change.quantize(PERCENT_QUANTUM, rounding=ROUND_HALF_UP)


def month_buckets(span: DateRange) -> list[DateRange]:
    """Split a range into calendar months, clipped to the range.

    The first and last buckets are partial whenever the span does not start on the
    1st or end on a month end. Clipping rather than extending keeps the sum of the
    buckets equal to the total for the span - a chart whose bars add up to something
    other than the headline figure is worse than no chart.
    """
    buckets: list[DateRange] = []
    cursor = month_start(span.start)

    while cursor <= span.end:
        finish = month_end(cursor)
        buckets.append(DateRange(max(cursor, span.start), min(finish, span.end)))
        cursor = _add_months(cursor, 1).replace(day=1)

    return buckets


def month_label(day: dt.date) -> str:
    """``Apr 2026``. Short, and unambiguous across a year boundary - a 12-month
    chart labelled only ``Apr`` shows two of them."""
    return f"{calendar.month_abbr[day.month]} {day.year}"


def local_date(instant: dt.datetime, timezone_name: str) -> dt.date:
    """The calendar date at ``instant`` in ``timezone_name``.

    **Period boundaries are local dates, not UTC dates.** At 00:30 on 1 August in
    Asia/Kolkata it is still 31 July in UTC, so a dashboard resolving "this month"
    from the UTC date shows the whole of July - every night, for five and a half
    hours. The same error moves the fiscal-year boundary on 1 April.

    Kept pure - the instant is a parameter - so the boundary behaviour is testable
    without freezing the clock.

    Falls back to the instant's own date if the zone name is unknown. A bad timezone
    string is a configuration error worth logging, not a reason to fail the whole
    dashboard; being off by a few hours beats showing nothing.
    """
    return local_datetime(instant, timezone_name).date()


def local_datetime(instant: dt.datetime, timezone_name: str) -> dt.datetime:
    """``instant`` expressed in ``timezone_name``, still timezone-aware.

    :func:`local_date` is this, with the time thrown away. Kept separate because one
    caller needs the *hour*: the seal worker asks whether an organization's chosen
    sealing time has arrived, and "01:00" has to mean 01:00 where the business is.
    Comparing a UTC hour against a setting the owner picked in local time makes the
    same setting fire at a different wall-clock time for every tenant.

    Same fallback and same purity as ``local_date`` - the instant is a parameter, so
    boundary behaviour is testable without freezing the clock.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware; a naive datetime has no true date")

    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, ModuleNotFoundError):
        return instant

    return instant.astimezone(zone)
