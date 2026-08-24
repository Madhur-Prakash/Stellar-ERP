import 'json.dart';

/// Analytics contracts.
///
/// **The period vocabulary is not duplicated here as logic.** [Period] mirrors the
/// server's enum so calls type-check, but what "this financial year" *means* in
/// dates is resolved server-side against the organization's fiscal-year start.
/// Re-deriving an April-start year on the client would eventually disagree with the
/// ledger, and the disagreement would be silent.
enum Period {
  thisMonth('this_month'),
  lastMonth('last_month'),
  thisQuarter('this_quarter'),
  thisFiscalYear('this_fiscal_year'),
  last30Days('last_30_days'),
  last12Months('last_12_months');

  const Period(this.wire);

  final String wire;

  static Period parse(String value) => Period.values.firstWhere(
    (Period p) => p.wire == value,
    orElse: () => Period.thisMonth,
  );
}

class DateSpan {
  const DateSpan({required this.start, required this.end, required this.days});

  final String start;
  final String end;
  final int days;

  factory DateSpan.fromJson(Json json) => DateSpan(
    start: str(json, 'start'),
    end: str(json, 'end'),
    days: intOf(json, 'days'),
  );
}

class Movement {
  const Movement({
    required this.current,
    required this.previous,
    this.changePercent,
  });

  final String current;
  final String previous;

  /// Null when the previous period gives no basis for a percentage - going from
  /// zero is an infinite increase, not "+100%". Render "no prior data", never a
  /// number.
  final String? changePercent;

  /// The change as a `double`, for picking an arrow direction and a rounded label.
  ///
  /// Safe to convert: this is not a figure anyone acts on, and the server already
  /// rounded it to one decimal place.
  double? get change =>
      changePercent == null ? null : double.tryParse(changePercent!);

  factory Movement.fromJson(Json json) => Movement(
    current: money(json, 'current'),
    previous: money(json, 'previous'),
    changePercent: moneyOrNull(json, 'change_percent'),
  );

  static const Movement zero = Movement(current: '0', previous: '0');
}

class Dashboard {
  const Dashboard({
    required this.periodLabel,
    required this.span,
    required this.comparison,
    required this.currency,
    required this.revenue,
    required this.expenses,
    required this.grossProfit,
    required this.netProfit,
    required this.cash,
    required this.receivables,
    required this.payables,
    required this.inventoryValue,
    required this.overdueReceivables,
    required this.overduePayables,
    required this.invoicesIssued,
    required this.billsReceived,
  });

  final String periodLabel;
  final DateSpan span;

  /// What the percentages are measured against. Shown so "up 12%" is checkable.
  final DateSpan comparison;
  final String currency;

  final Movement revenue;
  final Movement expenses;
  final Movement grossProfit;
  final Movement netProfit;

  /// Balances as at the end of the window, not movement within it.
  final String cash;
  final String receivables;
  final String payables;
  final String inventoryValue;

  final String overdueReceivables;
  final String overduePayables;

  final int invoicesIssued;
  final int billsReceived;

  factory Dashboard.fromJson(Json json) => Dashboard(
    periodLabel: strOrNull(json, 'period_label') ?? '',
    span: DateSpan.fromJson(mapOf(json, 'span')),
    comparison: DateSpan.fromJson(mapOf(json, 'comparison')),
    currency: strOrNull(json, 'currency') ?? 'INR',
    revenue: Movement.fromJson(mapOf(json, 'revenue')),
    expenses: Movement.fromJson(mapOf(json, 'expenses')),
    grossProfit: Movement.fromJson(mapOf(json, 'gross_profit')),
    netProfit: Movement.fromJson(mapOf(json, 'net_profit')),
    cash: money(json, 'cash'),
    receivables: money(json, 'receivables'),
    payables: money(json, 'payables'),
    inventoryValue: money(json, 'inventory_value'),
    overdueReceivables: money(json, 'overdue_receivables'),
    overduePayables: money(json, 'overdue_payables'),
    invoicesIssued: intOf(json, 'invoices_issued'),
    billsReceived: intOf(json, 'bills_received'),
  );
}

class TrendPoint {
  const TrendPoint({
    required this.label,
    required this.income,
    required this.expenses,
    required this.profit,
  });

  final String label;
  final String income;
  final String expenses;
  final String profit;

  factory TrendPoint.fromJson(Json json) => TrendPoint(
    label: str(json, 'label'),
    income: money(json, 'income'),
    expenses: money(json, 'expenses'),
    profit: money(json, 'profit'),
  );
}

class Trend {
  const Trend({
    required this.points,
    required this.totalIncome,
    required this.totalExpenses,
    required this.totalProfit,
  });

  final List<TrendPoint> points;
  final String totalIncome;
  final String totalExpenses;
  final String totalProfit;

  factory Trend.fromJson(Json json) => Trend(
    points: listOf(json, 'points', TrendPoint.fromJson),
    totalIncome: money(json, 'total_income'),
    totalExpenses: money(json, 'total_expenses'),
    totalProfit: money(json, 'total_profit'),
  );
}

class RankedRow {
  const RankedRow({
    this.id,
    required this.label,
    required this.amount,
    required this.count,
  });

  final String? id;
  final String label;
  final String amount;
  final int count;

  factory RankedRow.fromJson(Json json) => RankedRow(
    id: strOrNull(json, 'id'),
    label: str(json, 'label'),
    amount: money(json, 'amount'),
    count: intOf(json, 'count'),
  );
}

class Ranking {
  const Ranking({required this.rows, required this.total});

  final List<RankedRow> rows;

  /// Across all rows, not just the returned top N - so "these five are 62%" is
  /// true.
  final String total;

  factory Ranking.fromJson(Json json) => Ranking(
    rows: listOf(json, 'rows', RankedRow.fromJson),
    total: money(json, 'total'),
  );
}

class ControlCheck {
  const ControlCheck({
    required this.name,
    required this.ledger,
    required this.subledger,
    required this.difference,
    required this.agrees,
  });

  final String name;
  final String ledger;
  final String subledger;
  final String difference;
  final bool agrees;

  factory ControlCheck.fromJson(Json json) => ControlCheck(
    name: str(json, 'name'),
    ledger: money(json, 'ledger'),
    subledger: money(json, 'subledger'),
    difference: money(json, 'difference'),
    agrees: boolOf(json, 'agrees'),
  );
}

class ControlChecks {
  const ControlChecks({
    required this.asOf,
    required this.checks,
    required this.allAgree,
  });

  final String asOf;
  final List<ControlCheck> checks;
  final bool allAgree;

  factory ControlChecks.fromJson(Json json) => ControlChecks(
    asOf: str(json, 'as_of'),
    checks: listOf(json, 'checks', ControlCheck.fromJson),
    allAgree: boolOf(json, 'all_agree'),
  );
}

class PeriodOption {
  const PeriodOption({required this.value, required this.label});

  final Period value;
  final String label;

  factory PeriodOption.fromJson(Json json) => PeriodOption(
    value: Period.parse(str(json, 'value')),
    label: str(json, 'label'),
  );
}

class PeriodOptions {
  const PeriodOptions({
    required this.options,
    required this.fiscalYearStartMonth,
    required this.today,
  });

  final List<PeriodOption> options;
  final int fiscalYearStartMonth;

  /// The server's today, in the organization's timezone - not the machine's.
  final String today;

  factory PeriodOptions.fromJson(Json json) => PeriodOptions(
    options: listOf(json, 'options', PeriodOption.fromJson),
    fiscalYearStartMonth: intOf(json, 'fiscal_year_start_month', 4),
    today: str(json, 'today'),
  );
}
