"""Analytics API contracts."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.core.schemas import ResponseSchema
from app.modules.analytics.periods import Period


class MovementRead(ResponseSchema):
    """A figure and its like-for-like comparison."""

    current: Decimal
    previous: Decimal
    #: Null when the previous period gives no basis for a percentage - going from
    #: zero is an infinite increase, not "+100%". Render "no prior data", not a
    #: number.
    change_percent: Decimal | None = None


class PeriodRead(ResponseSchema):
    start: dt.date
    end: dt.date
    days: int


class DashboardRead(ResponseSchema):
    """Everything the dashboard needs in one request.

    One call rather than eight: the tiles are read together, and eight parallel
    requests would each re-resolve the same period and re-open the same connection.
    """

    period: Period
    period_label: str
    span: PeriodRead
    #: The window the figures are compared against. Exposed so the UI can say
    #: exactly what "up 12%" is relative to, instead of leaving the user to guess.
    comparison: PeriodRead
    currency: str

    revenue: MovementRead
    expenses: MovementRead
    gross_profit: MovementRead
    net_profit: MovementRead

    #: Balances as at the end of the window, not movement within it.
    cash: Decimal
    receivables: Decimal
    payables: Decimal
    inventory_value: Decimal

    overdue_receivables: Decimal
    overdue_payables: Decimal

    invoices_issued: int
    bills_received: int


class TrendPointRead(ResponseSchema):
    label: str
    start: dt.date
    end: dt.date
    income: Decimal
    expenses: Decimal
    profit: Decimal


class TrendRead(ResponseSchema):
    span: PeriodRead
    points: list[TrendPointRead]
    total_income: Decimal
    total_expenses: Decimal
    total_profit: Decimal


class RankedRowRead(ResponseSchema):
    #: Null for rankings not keyed to a record - product lines are grouped by
    #: description, because invoice lines are deliberately free-text.
    id: uuid.UUID | None = None
    label: str
    amount: Decimal
    count: int


class RankingRead(ResponseSchema):
    span: PeriodRead
    rows: list[RankedRowRead]
    #: The total across *all* rows, not just the returned top N, so the UI can show
    #: "these five are 62% of revenue" - which is the actually useful reading.
    total: Decimal


class ControlCheckRead(ResponseSchema):
    name: str
    #: The control account's balance.
    ledger: Decimal
    #: The same figure derived from the documents behind it.
    subledger: Decimal
    difference: Decimal
    agrees: bool


class ControlChecksRead(ResponseSchema):
    """The monthly reconciliation, as a first-class response.

    `all_agree` false means a document updated one table but not the other. It is
    surfaced rather than raising, for the same reason `TrialBalance.is_balanced` is:
    a corrupted ledger should be *visible*, not a 500 on an otherwise useful screen.
    """

    as_of: dt.date
    checks: list[ControlCheckRead]
    all_agree: bool


class PeriodOption(ResponseSchema):
    value: Period
    label: str


class PeriodOptions(ResponseSchema):
    """The selectable windows, resolved against the organization's fiscal year.

    Served rather than hard-coded in the frontend so "this financial year" means the
    same dates on both sides - an April-start business and a January-start one get
    different answers, and duplicating that rule in TypeScript would eventually
    disagree with the ledger.
    """

    options: list[PeriodOption] = Field(min_length=1)
    fiscal_year_start_month: int
    today: dt.date
