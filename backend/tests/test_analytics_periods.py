"""Period arithmetic tests.

Pure dates in, pure dates out - no database. Worth testing exhaustively because
every misleading dashboard figure traces back to one of these functions, and the
failures are silent: a wrong comparison window still renders a plausible number.

Dates are chosen deliberately rather than generated: month lengths, leap years, and
the Indian fiscal-year boundary are where this breaks.
"""

from __future__ import annotations

import datetime as dt
import itertools
from decimal import Decimal

import pytest

from app.modules.analytics.periods import (
    DateRange,
    Period,
    fiscal_year_start,
    local_date,
    month_buckets,
    month_end,
    month_label,
    percent_change,
    previous_comparable,
    quarter_start,
    resolve_period,
)

D = Decimal


def resolve(period: Period, today: dt.date, fiscal_start: int = 4) -> DateRange:
    return resolve_period(period, today=today, fiscal_start_month=fiscal_start)


class TestDateRange:
    def test_counts_both_endpoints(self) -> None:
        """An entry dated on the end date is inside the period."""
        span = DateRange(dt.date(2026, 4, 1), dt.date(2026, 4, 30))
        assert span.days == 30
        assert span.contains(dt.date(2026, 4, 30))
        assert not span.contains(dt.date(2026, 5, 1))

    def test_a_single_day_is_one_day(self) -> None:
        span = DateRange(dt.date(2026, 4, 1), dt.date(2026, 4, 1))
        assert span.days == 1

    def test_rejects_a_reversed_range(self) -> None:
        with pytest.raises(ValueError, match="before start"):
            DateRange(dt.date(2026, 4, 30), dt.date(2026, 4, 1))


class TestFiscalYear:
    def test_april_start_puts_february_in_the_previous_year(self) -> None:
        """15 Feb 2026 is in the year that began 1 April 2025.

        Getting this backwards shifts every year-to-date figure by up to twelve
        months - and the wrong figure still looks like a plausible one.
        """
        assert fiscal_year_start(dt.date(2026, 2, 15), 4) == dt.date(2025, 4, 1)

    def test_april_start_puts_may_in_the_current_year(self) -> None:
        assert fiscal_year_start(dt.date(2026, 5, 15), 4) == dt.date(2026, 4, 1)

    def test_the_first_day_of_the_year_belongs_to_that_year(self) -> None:
        assert fiscal_year_start(dt.date(2026, 4, 1), 4) == dt.date(2026, 4, 1)

    def test_the_last_day_of_the_year_belongs_to_the_previous_start(self) -> None:
        assert fiscal_year_start(dt.date(2026, 3, 31), 4) == dt.date(2025, 4, 1)

    def test_a_january_start_is_the_calendar_year(self) -> None:
        assert fiscal_year_start(dt.date(2026, 2, 15), 1) == dt.date(2026, 1, 1)

    @pytest.mark.parametrize("month", [0, 13, -1])
    def test_rejects_an_impossible_start_month(self, month: int) -> None:
        with pytest.raises(ValueError, match="1-12"):
            fiscal_year_start(dt.date(2026, 2, 15), month)


class TestQuarters:
    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            # An April-start business: Q1 is Apr-Jun, not Jan-Mar.
            (dt.date(2026, 4, 1), dt.date(2026, 4, 1)),
            (dt.date(2026, 6, 30), dt.date(2026, 4, 1)),
            (dt.date(2026, 7, 1), dt.date(2026, 7, 1)),
            (dt.date(2026, 12, 31), dt.date(2026, 10, 1)),
            (dt.date(2027, 1, 1), dt.date(2027, 1, 1)),
            (dt.date(2027, 3, 31), dt.date(2027, 1, 1)),
        ],
    )
    def test_quarters_run_from_the_fiscal_year_start(self, day: dt.date, expected: dt.date) -> None:
        assert quarter_start(day, 4) == expected

    def test_a_january_start_gives_calendar_quarters(self) -> None:
        assert quarter_start(dt.date(2026, 2, 15), 1) == dt.date(2026, 1, 1)
        assert quarter_start(dt.date(2026, 8, 15), 1) == dt.date(2026, 7, 1)


class TestResolvePeriod:
    TODAY = dt.date(2026, 7, 15)

    def test_this_month_runs_to_today_not_month_end(self) -> None:
        """A month-to-date figure must not include days that have not happened."""
        span = resolve(Period.THIS_MONTH, self.TODAY)
        assert span == DateRange(dt.date(2026, 7, 1), dt.date(2026, 7, 15))

    def test_last_month_is_the_whole_month(self) -> None:
        span = resolve(Period.LAST_MONTH, self.TODAY)
        assert span == DateRange(dt.date(2026, 6, 1), dt.date(2026, 6, 30))

    def test_this_quarter_starts_at_the_fiscal_quarter(self) -> None:
        span = resolve(Period.THIS_QUARTER, self.TODAY)
        assert span == DateRange(dt.date(2026, 7, 1), dt.date(2026, 7, 15))

    def test_this_fiscal_year_starts_in_april(self) -> None:
        span = resolve(Period.THIS_FISCAL_YEAR, self.TODAY)
        assert span == DateRange(dt.date(2026, 4, 1), dt.date(2026, 7, 15))

    def test_last_30_days_is_30_days_including_today(self) -> None:
        """Off by one here makes every 30-day figure a 31-day figure."""
        span = resolve(Period.LAST_30_DAYS, self.TODAY)
        assert span.days == 30
        assert span.end == self.TODAY
        assert span.start == dt.date(2026, 6, 16)

    def test_last_12_months_covers_12_month_buckets(self) -> None:
        span = resolve(Period.LAST_12_MONTHS, self.TODAY)
        assert span.start == dt.date(2025, 8, 1)
        assert len(month_buckets(span)) == 12

    def test_every_period_resolves(self) -> None:
        """A new member of the enum must not fall through the match silently."""
        for period in Period:
            span = resolve(period, self.TODAY)
            assert span.days >= 1
            assert period.label


class TestPreviousComparable:
    def test_month_to_date_compares_against_the_same_days(self) -> None:
        """The headline correctness property of this module.

        On the 3rd, "this month" is 3 days. Comparing it against a full 30-day month
        reports a 90% collapse in revenue that never happened - and a dashboard that
        does this is wrong for most of every month.
        """
        current = resolve(Period.THIS_MONTH, dt.date(2026, 7, 3))
        assert current.days == 3

        previous = previous_comparable(Period.THIS_MONTH, current)
        assert previous == DateRange(dt.date(2026, 6, 1), dt.date(2026, 6, 3))
        assert previous.days == current.days

    def test_a_full_month_to_date_compares_against_the_full_previous_month(self) -> None:
        current = resolve(Period.THIS_MONTH, dt.date(2026, 6, 30))
        previous = previous_comparable(Period.THIS_MONTH, current)
        assert previous == DateRange(dt.date(2026, 5, 1), dt.date(2026, 5, 30))

    def test_a_31_day_month_does_not_overflow_a_shorter_previous_month(self) -> None:
        """31 days of March cannot be compared against 31 days of February.

        The comparison is clipped to the end of February rather than spilling into
        March, which would double-count days that are already in the current period.
        """
        current = resolve(Period.THIS_MONTH, dt.date(2026, 3, 31))
        assert current.days == 31

        previous = previous_comparable(Period.THIS_MONTH, current)
        assert previous.end == month_end(dt.date(2026, 2, 1))
        assert previous == DateRange(dt.date(2026, 2, 1), dt.date(2026, 2, 28))
        # Deliberately shorter than the current window: a slightly unfair comparison
        # is better than one that reaches into the period being measured.
        assert previous.days < current.days

    def test_leap_february_is_handled(self) -> None:
        current = resolve(Period.THIS_MONTH, dt.date(2024, 3, 31))
        previous = previous_comparable(Period.THIS_MONTH, current)
        assert previous.end == dt.date(2024, 2, 29)

    def test_last_month_compares_against_the_month_before(self) -> None:
        current = resolve(Period.LAST_MONTH, dt.date(2026, 7, 15))
        previous = previous_comparable(Period.LAST_MONTH, current)
        assert previous == DateRange(dt.date(2026, 5, 1), dt.date(2026, 5, 31))

    def test_last_30_days_compares_against_the_preceding_30(self) -> None:
        current = resolve(Period.LAST_30_DAYS, dt.date(2026, 7, 15))
        previous = previous_comparable(Period.LAST_30_DAYS, current)

        assert previous.days == 30
        assert previous.end == current.start - dt.timedelta(days=1)
        # No overlap: the same day must never count in both windows.
        assert previous.end < current.start

    def test_year_to_date_compares_against_the_same_span_last_year(self) -> None:
        current = resolve(Period.THIS_FISCAL_YEAR, dt.date(2026, 7, 15))
        previous = previous_comparable(Period.THIS_FISCAL_YEAR, current)

        assert previous.start == dt.date(2025, 4, 1)
        assert previous.days == current.days

    def test_no_period_overlaps_its_comparison(self) -> None:
        """A day counted in both windows inflates the base and flatters the trend."""
        for period in Period:
            for today in (dt.date(2026, 1, 1), dt.date(2026, 3, 31), dt.date(2026, 7, 15)):
                current = resolve(period, today)
                previous = previous_comparable(period, current)
                assert previous.end < current.start, f"{period} on {today} overlaps"


class TestPercentChange:
    def test_computes_a_rise(self) -> None:
        assert percent_change(D("110"), D("100")) == D("10.0")

    def test_computes_a_fall(self) -> None:
        assert percent_change(D("90"), D("100")) == D("-10.0")

    def test_rounds_to_one_decimal(self) -> None:
        """More precision on a glanceable figure is false precision."""
        assert percent_change(D("112.34"), D("100")) == D("12.3")

    def test_no_change_is_zero_not_none(self) -> None:
        assert percent_change(D("100"), D("100")) == D("0.0")

    def test_growth_from_zero_is_undefined_not_100_percent(self) -> None:
        """₹0 to ₹50,000 is not "+100%".

        The increase is infinite; any finite number printed there is invented. The
        caller shows "no prior data" instead.
        """
        assert percent_change(D("50000"), D("0")) is None

    def test_zero_to_zero_is_undefined(self) -> None:
        assert percent_change(D("0"), D("0")) is None

    def test_a_negative_base_is_undefined(self) -> None:
        """A loss becoming a profit is arithmetically "-200%", which reads as a
        disaster. Undefined is the honest answer."""
        assert percent_change(D("1000"), D("-1000")) is None

    def test_exactness_survives(self) -> None:
        """Decimal in, Decimal out - no float anywhere in the path."""
        result = percent_change(D("0.3"), D("0.1"))
        assert result == D("200.0")
        assert isinstance(result, Decimal)


class TestMonthBuckets:
    def test_splits_a_full_year_into_twelve(self) -> None:
        buckets = month_buckets(DateRange(dt.date(2026, 4, 1), dt.date(2027, 3, 31)))
        assert len(buckets) == 12
        assert buckets[0] == DateRange(dt.date(2026, 4, 1), dt.date(2026, 4, 30))
        assert buckets[-1] == DateRange(dt.date(2027, 3, 1), dt.date(2027, 3, 31))

    def test_clips_partial_months_at_both_ends(self) -> None:
        buckets = month_buckets(DateRange(dt.date(2026, 4, 15), dt.date(2026, 6, 10)))
        assert len(buckets) == 3
        assert buckets[0] == DateRange(dt.date(2026, 4, 15), dt.date(2026, 4, 30))
        assert buckets[-1] == DateRange(dt.date(2026, 6, 1), dt.date(2026, 6, 10))

    def test_the_buckets_exactly_cover_the_span(self) -> None:
        """The property that makes a chart's bars add up to the headline figure.

        Any gap loses transactions; any overlap counts them twice. Either way the
        chart contradicts the total printed above it.
        """
        span = DateRange(dt.date(2025, 8, 14), dt.date(2026, 7, 15))
        buckets = month_buckets(span)

        assert buckets[0].start == span.start
        assert buckets[-1].end == span.end
        assert sum(bucket.days for bucket in buckets) == span.days
        for earlier, later in itertools.pairwise(buckets):
            assert later.start == earlier.end + dt.timedelta(days=1)

    def test_a_single_day_is_one_bucket(self) -> None:
        buckets = month_buckets(DateRange(dt.date(2026, 4, 15), dt.date(2026, 4, 15)))
        assert buckets == [DateRange(dt.date(2026, 4, 15), dt.date(2026, 4, 15))]

    def test_crosses_a_december_boundary(self) -> None:
        buckets = month_buckets(DateRange(dt.date(2026, 12, 1), dt.date(2027, 1, 31)))
        assert len(buckets) == 2
        assert buckets[1].start == dt.date(2027, 1, 1)


class TestMonthLabel:
    def test_includes_the_year(self) -> None:
        """A 12-month chart labelled only "Apr" shows two of them."""
        assert month_label(dt.date(2026, 4, 1)) == "Apr 2026"
        assert month_label(dt.date(2027, 4, 1)) == "Apr 2027"


class TestLocalDate:
    def test_late_evening_utc_is_already_tomorrow_in_india(self) -> None:
        """19:00 UTC on 31 July is 00:30 IST on 1 August.

        A dashboard resolving "this month" from the UTC date would show the whole of
        July for the first five and a half hours of every Indian day.
        """
        instant = dt.datetime(2026, 7, 31, 19, 0, tzinfo=dt.UTC)
        assert local_date(instant, "Asia/Kolkata") == dt.date(2026, 8, 1)
        assert instant.date() == dt.date(2026, 7, 31)  # what the naive version gives

    def test_moves_the_fiscal_year_boundary_too(self) -> None:
        """The same error shifts year-to-date figures by a full year on 1 April."""
        instant = dt.datetime(2026, 3, 31, 20, 0, tzinfo=dt.UTC)
        local = local_date(instant, "Asia/Kolkata")
        assert local == dt.date(2026, 4, 1)
        assert fiscal_year_start(local, 4) == dt.date(2026, 4, 1)
        assert fiscal_year_start(instant.date(), 4) == dt.date(2025, 4, 1)  # off by a year

    def test_early_morning_utc_is_still_the_same_day_westward(self) -> None:
        instant = dt.datetime(2026, 7, 1, 2, 0, tzinfo=dt.UTC)
        assert local_date(instant, "America/New_York") == dt.date(2026, 6, 30)

    def test_an_unknown_zone_degrades_rather_than_failing(self) -> None:
        """A bad timezone string should not take down the dashboard."""
        instant = dt.datetime(2026, 7, 31, 19, 0, tzinfo=dt.UTC)
        assert local_date(instant, "Mars/Olympus_Mons") == dt.date(2026, 7, 31)

    def test_rejects_a_naive_datetime(self) -> None:
        """Without an offset there is no fact of the matter about the date."""
        with pytest.raises(ValueError, match="timezone-aware"):
            local_date(dt.datetime(2026, 7, 31, 19, 0), "Asia/Kolkata")
