import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/format.dart';
import '../../models/accounting.dart';
import '../../models/analytics.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_card.dart';
import '../../widgets/charts.dart';
import '../../widgets/info_tip.dart';
import '../../widgets/primitives.dart';

/// Charts for the accounting screens.
///
/// **Bars, not pie charts, for balances.** A cash account can hold a negative balance -
/// and a negative value has no possible pie slice. A pie would have to drop it, hide it, or
/// plot its absolute value, and all three are lies about the books. Horizontal bars handle
/// a negative naturally and are easier to read against a label besides.
///
/// **Only accounts with money.** The chart of accounts is 114 rows and four of them have a
/// balance; plotting the rest is 110 bars of nothing that bury the four that matter.

/// The colour a bar takes from its account type.
Color typeColour(AppTokens t, AccountType type) => switch (type) {
  AccountType.asset => t.info,
  AccountType.liability => t.warning,
  AccountType.equity => t.primary,
  AccountType.income => t.success,
  AccountType.expense => t.danger,
};

/// Balance-sheet types only - the accounts that represent something you hold or owe.
///
/// Income and expense accounts are deliberately excluded even though they carry balances.
/// They are *running totals* of what has been earned and spent, not places money sits, and
/// plotting them beside cash makes the axis mean two different things at once: further
/// right is more money for an asset, but more cost for an expense. That conflation is
/// genuinely misleading, and it is what the donut and the trend chart are for instead.
const Set<AccountType> _holdingTypes = <AccountType>{
  AccountType.asset,
  AccountType.liability,
  AccountType.equity,
};

/// Every account holding money, longest bar first.
///
/// Sorted by size rather than by code: the question this answers is "where is my money",
/// and code order scatters the answer across the chart.
class AccountBalancesChart extends StatelessWidget {
  const AccountBalancesChart({
    super.key,
    required this.accounts,
    required this.currency,
  });

  final List<Account> accounts;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    final List<Account> holding =
        accounts
            .where(
              (Account a) =>
                  !a.isGroup &&
                  _holdingTypes.contains(a.accountType) &&
                  !isZeroMoney(a.balance),
            )
            .toList()
          ..sort(
            (Account a, Account b) => chartValue(
              b.balance,
            ).abs().compareTo(chartValue(a.balance).abs()),
          );

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            titleWidget: _ChartTitle(
              icon: LucideIcons.wallet,
              text: 'Where your money is',
            ),
            description:
                'What you hold and what you owe. Hover a bar for the exact figure.',
            action: InfoTip(
              label: 'account balances',
              align: InfoTipAlign.right,
              children: <Widget>[
                infoText(
                  'What you actually have and owe, biggest first - cash, bank, stock, money '
                  'owed to you, money you owe. Blue is an asset, amber a liability.',
                ),
                infoRich(<String>[
                  'Income and spending are ',
                  'not',
                  ' here on purpose. They are running totals rather than places money '
                      'sits, and putting them on this axis would make "further right" mean '
                      'more money for cash and more cost for an expense. Those are on the '
                      'donut and the trend chart.',
                ]),
                infoRich(<String>[
                  '',
                  'A negative asset is worth a second look.',
                  ' Cash cannot really go below zero, so a negative cash bar means a '
                      'payment was recorded against the wrong account.',
                ]),
              ],
            ),
          ),
          CardBody(
            child: holding.isEmpty
                ? const EmptyState(
                    icon: LucideIcons.wallet,
                    title: 'Nothing held yet',
                    description:
                        'Record money in or out and the accounts holding it appear here.',
                    verticalPadding: 48,
                  )
                : HorizontalBarChart(
                    currency: currency,
                    points: <ChartPoint>[
                      for (final Account account in holding)
                        ChartPoint(
                          label: account.name,
                          value: chartValue(account.balance),
                          exact: account.balance,
                        ),
                    ],
                    colours: <Color>[
                      for (final Account account in holding)
                        // A negative balance is always shown in the danger colour,
                        // whatever the account type: it is the exception worth noticing.
                        if (isNegativeMoney(account.balance))
                          t.danger
                        else
                          typeColour(t, account.accountType),
                    ],
                    subtitles: <String>[
                      for (final Account account in holding)
                        account.accountType.label,
                    ],
                  ),
          ),
        ],
      ),
    );
  }
}

/// Net balance per account type - the accounting equation, drawn.
///
/// Assets on one side, liabilities and equity on the other, with income and expenses
/// feeding the difference. Seeing them side by side is the fastest way to sanity-check a
/// set of books.
class BalanceByTypeChart extends StatelessWidget {
  const BalanceByTypeChart({
    super.key,
    required this.accounts,
    required this.currency,
  });

  final List<Account> accounts;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    // Summed exactly rather than by adding doubles: this is a figure the tooltip prints.
    final Map<AccountType, List<String>> byType = <AccountType, List<String>>{};
    for (final Account account in accounts) {
      if (account.isGroup) continue;
      byType
          .putIfAbsent(account.accountType, () => <String>[])
          .add(account.balance);
    }

    final List<(AccountType, String)> totals = <(AccountType, String)>[
      for (final AccountType type in AccountType.values)
        if (byType.containsKey(type)) (type, sumMoney(byType[type]!)),
    ].where(((AccountType, String) entry) => !isZeroMoney(entry.$2)).toList();

    if (totals.isEmpty) return const SizedBox.shrink();

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            titleWidget: _ChartTitle(
              icon: LucideIcons.trendingUp,
              text: 'Totals by type',
            ),
            description:
                'Assets, liabilities, equity, income, and expenses side by side.',
          ),
          CardBody(
            child: GroupedBarChart(
              height: 224,
              currency: currency,
              series: <ChartSeries>[
                ChartSeries(
                  name: 'Net balance',
                  colour: t.primary,
                  points: <ChartPoint>[
                    for (final (AccountType type, String total) in totals)
                      ChartPoint(
                        label: type.label,
                        value: chartValue(total),
                        exact: total,
                      ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Every cash movement, by day, with the entries behind each bar.
///
/// The point is the tooltip: hovering a day lists the individual entries that made it up,
/// with their numbers and narrations. A total with no way to see what it is composed of
/// invites the question it cannot answer.
class CashMovementChart extends StatelessWidget {
  const CashMovementChart({
    super.key,
    required this.entries,
    required this.currency,
  });

  final List<JournalEntry> entries;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    final Map<String, List<JournalEntry>> byDay =
        <String, List<JournalEntry>>{};
    for (final JournalEntry entry in entries) {
      if (entry.cashDirection == null) continue;
      byDay.putIfAbsent(entry.entryDate, () => <JournalEntry>[]).add(entry);
    }

    final List<String> days = byDay.keys.toList()..sort();

    String tooltipFor(int index) {
      final List<JournalEntry> day = byDay[days[index]]!;
      final StringBuffer out = StringBuffer(formatDate(days[index]));
      for (final JournalEntry entry in day) {
        out.write('\n');
        out.write(entry.cashDirection == 'in' ? '↓ ' : '↑ ');
        out.write(entry.narration);
        out.write('  ');
        out.write(formatMoney(entry.cashAmount, currency: currency));
        if (entry.isReversed) out.write(' · reversed');
      }
      return out.toString();
    }

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            titleWidget: _ChartTitle(
              icon: LucideIcons.landmark,
              text: 'Cash movement',
            ),
            description:
                'Money in and out by day. Hover a bar to see the entries behind it.',
            action: InfoTip(
              label: 'cash movement',
              align: InfoTipAlign.right,
              children: <Widget>[
                infoText(
                  'Only entries that moved cash or bank. An invoice posting moves '
                  'receivables and revenue rather than money, so it does not appear here.',
                ),
                infoText(
                  'A reversed entry and its reversal both show, one in each direction - '
                  'which is exactly what cancelling out looks like.',
                ),
              ],
            ),
          ),
          CardBody(
            child: days.isEmpty
                ? const EmptyState(
                    icon: LucideIcons.landmark,
                    title: 'No cash movement yet',
                    description:
                        'Entries that move money appear here once recorded.',
                    verticalPadding: 48,
                  )
                : Column(
                    children: <Widget>[
                      GroupedBarChart(
                        height: 256,
                        currency: currency,
                        tooltipBuilder: tooltipFor,
                        series: <ChartSeries>[
                          ChartSeries(
                            name: 'In',
                            colour: t.success,
                            points: <ChartPoint>[
                              for (final String day in days)
                                _dayTotal(byDay[day]!, 'in', day),
                            ],
                          ),
                          ChartSeries(
                            name: 'Out',
                            colour: t.danger,
                            points: <ChartPoint>[
                              for (final String day in days)
                                _dayTotal(byDay[day]!, 'out', day),
                            ],
                          ),
                        ],
                      ),
                      ChartLegend(
                        series: <ChartSeries>[
                          ChartSeries(
                            name: 'In',
                            colour: t.success,
                            points: const <ChartPoint>[],
                          ),
                          ChartSeries(
                            name: 'Out',
                            colour: t.danger,
                            points: const <ChartPoint>[],
                          ),
                        ],
                      ),
                    ],
                  ),
          ),
        ],
      ),
    );
  }

  /// One direction's total for a day, summed exactly.
  static ChartPoint _dayTotal(
    List<JournalEntry> day,
    String direction,
    String label,
  ) {
    final String total = sumMoney(
      day
          .where((JournalEntry e) => e.cashDirection == direction)
          .map((JournalEntry e) => e.cashAmount),
    );
    return ChartPoint(
      label: formatDate(label),
      value: chartValue(total),
      exact: total,
    );
  }
}

/// Fixed and ordered rather than generated, so a category keeps its colour between
/// renders. A donut whose colours shuffle on every refetch cannot be read.
List<Color> sliceColours(AppTokens t) => <Color>[
  t.primary,
  t.info,
  t.success,
  t.warning,
  t.danger,
  const Color(0xFF8B5CF6),
  const Color(0xFF0EA5E9),
  const Color(0xFF14B8A6),
  const Color(0xFFF59E0B),
  const Color(0xFFEC4899),
];

/// Small categories are folded together - twelve two-percent slivers is a colour key.
const int _maxSlices = 7;

/// Share of total spending by category.
class SpendingMixChart extends StatelessWidget {
  const SpendingMixChart({
    super.key,
    required this.accounts,
    required this.currency,
  });

  final List<Account> accounts;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    final List<Account> expenses =
        accounts
            .where(
              (Account a) =>
                  !a.isGroup &&
                  a.accountType == AccountType.expense &&
                  chartValue(a.balance) > 0,
            )
            .toList()
          ..sort(
            (Account a, Account b) =>
                chartValue(b.balance).compareTo(chartValue(a.balance)),
          );

    final List<ChartPoint> points = <ChartPoint>[
      for (final Account account in expenses.take(_maxSlices))
        ChartPoint(
          label: account.name,
          value: chartValue(account.balance),
          exact: account.balance,
        ),
    ];

    final List<Account> rest = expenses.skip(_maxSlices).toList();
    if (rest.isNotEmpty) {
      final String folded = sumMoney(rest.map((Account a) => a.balance));
      points.add(
        ChartPoint(
          label: 'Other (${rest.length})',
          value: chartValue(folded),
          exact: folded,
        ),
      );
    }

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            titleWidget: _ChartTitle(
              icon: LucideIcons.chartPie,
              text: 'What you spent it on',
            ),
            description: 'Share of total spending by category.',
            action: InfoTip(
              label: 'spending mix',
              align: InfoTipAlign.right,
              children: <Widget>[
                infoRich(<String>[
                  'Every expense category with a balance, as a percentage of total '
                      'spending. Small ones are grouped into ',
                  'Other',
                  ' to keep it readable.',
                ]),
              ],
            ),
          ),
          CardBody(
            child: points.isEmpty
                ? const EmptyState(
                    icon: LucideIcons.chartPie,
                    title: 'Nothing spent yet',
                    description:
                        'Record money out and the breakdown appears here.',
                    verticalPadding: 48,
                  )
                : DonutChart(
                    points: points,
                    colours: sliceColours(t),
                    currency: currency,
                    total: sumMoney(points.map((ChartPoint p) => p.exact)),
                  ),
          ),
        ],
      ),
    );
  }
}

/// Income, spending, and profit month by month.
class TrendCard extends StatelessWidget {
  const TrendCard({super.key, required this.points, required this.currency});

  final List<TrendPoint>? points;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final List<TrendPoint> series = points ?? const <TrendPoint>[];
    final bool hasActivity = series.any(
      (TrendPoint p) => !isZeroMoney(p.income) || !isZeroMoney(p.expenses),
    );

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            titleWidget: _ChartTitle(
              icon: LucideIcons.chartLine,
              text: 'Trend over time',
            ),
            description: 'Income, spending, and what was left, month by month.',
            action: InfoTip(
              label: 'the trend',
              align: InfoTipAlign.right,
              children: <Widget>[
                infoText(
                  'Twelve months of posted entries. Months with nothing recorded show as '
                  'zero rather than being skipped, so the line does not imply trading that '
                  'did not happen.',
                ),
              ],
            ),
          ),
          CardBody(
            child: !hasActivity
                ? const EmptyState(
                    icon: LucideIcons.chartLine,
                    title: 'Not enough history yet',
                    description:
                        'Once entries span a few months, the trend appears here.',
                    verticalPadding: 48,
                  )
                : ComposedTrendChart(
                    currency: currency,
                    income: ChartSeries(
                      name: 'Income',
                      colour: t.success,
                      points: <ChartPoint>[
                        for (final TrendPoint p in series)
                          ChartPoint(
                            label: p.label,
                            value: chartValue(p.income),
                            exact: p.income,
                          ),
                      ],
                    ),
                    expenses: ChartSeries(
                      name: 'Spending',
                      colour: t.danger,
                      points: <ChartPoint>[
                        for (final TrendPoint p in series)
                          ChartPoint(
                            label: p.label,
                            value: chartValue(p.expenses),
                            exact: p.expenses,
                          ),
                      ],
                    ),
                    profit: ChartSeries(
                      name: 'Profit',
                      colour: t.primary,
                      points: <ChartPoint>[
                        for (final TrendPoint p in series)
                          ChartPoint(
                            label: p.label,
                            value: chartValue(p.profit),
                            exact: p.profit,
                          ),
                      ],
                    ),
                  ),
          ),
        ],
      ),
    );
  }
}

/// Costs below this many steps are folded together - a wall of slivers is unreadable.
const int _maxCostSteps = 8;

/// A waterfall: income, then each cost stepping down, ending on net profit.
///
/// **Built from the P&L response, not recomputed.** The closing bar has to be the same net
/// profit the dashboard shows, and the only way to guarantee that is to use the figures the
/// statement itself returned rather than adding up something similar. If they ever
/// disagreed, nobody could tell which was right.
class ProfitWaterfallCard extends StatelessWidget {
  const ProfitWaterfallCard({
    super.key,
    required this.report,
    required this.currency,
    this.isLoading = false,
  });

  final ProfitAndLoss? report;
  final String currency;
  final bool isLoading;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final bool hasActivity =
        report != null &&
        (!isZeroMoney(report!.totalIncome) ||
            !isZeroMoney(report!.totalExpenses));

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            titleWidget: _ChartTitle(
              icon: LucideIcons.trendingDown,
              text: 'How income became profit',
            ),
            description:
                'Income first, then each cost taken off it. The last bar is what was left.',
            action: InfoTip(
              label: 'the waterfall',
              align: InfoTipAlign.right,
              children: <Widget>[
                infoText(
                  'Read left to right. The green bar is everything earned; each red bar is '
                  'a cost stepping the running total down; the final bar is what remained.',
                ),
                infoRich(<String>[
                  '',
                  'That last bar is the same net profit the dashboard shows.',
                  ' It comes from the profit & loss statement rather than being added up '
                      'again here, so the two cannot disagree.',
                ]),
                infoText(
                  'A loss is drawn in red and hangs below the zero line.',
                ),
              ],
            ),
          ),
          CardBody(
            child: isLoading
                ? const Skeleton(height: 280)
                : !hasActivity
                ? const EmptyState(
                    icon: LucideIcons.trendingDown,
                    title: 'Nothing to break down yet',
                    description:
                        'Record income and spending and this shows how one became the '
                        'other.',
                    verticalPadding: 48,
                  )
                : WaterfallChart(
                    currency: currency,
                    steps: _buildSteps(report!, t),
                  ),
          ),
        ],
      ),
    );
  }

  static List<WaterfallStep> _buildSteps(ProfitAndLoss report, AppTokens t) {
    final List<WaterfallStep> steps = <WaterfallStep>[];

    final double income = chartValue(report.totalIncome);
    steps.add(
      WaterfallStep(
        label: 'Income',
        from: income < 0 ? income : 0,
        to: income < 0 ? 0 : income,
        exact: report.totalIncome,
        runningTotal: report.totalIncome,
        kind: WaterfallKind.up,
      ),
    );

    double running = income;
    String runningExact = report.totalIncome;

    // Biggest cost first: the eye should meet the thing worth asking about immediately.
    final List<ReportLine> costs =
        report.expenses
            .where((ReportLine line) => !isZeroMoney(line.amount))
            .toList()
          ..sort(
            (ReportLine a, ReportLine b) => chartValue(
              b.amount,
            ).abs().compareTo(chartValue(a.amount).abs()),
          );

    void addCost(String label, String amount) {
      final double value = chartValue(amount);
      final double after = running - value;
      runningExact = sumMoney(<String>[runningExact, '-$amount']);
      steps.add(
        WaterfallStep(
          label: label,
          from: running,
          to: after,
          exact: amount,
          runningTotal: runningExact,
          kind: WaterfallKind.down,
        ),
      );
      running = after;
    }

    for (final ReportLine cost in costs.take(_maxCostSteps)) {
      addCost(cost.label, cost.amount);
    }

    final List<ReportLine> folded = costs.skip(_maxCostSteps).toList();
    if (folded.isNotEmpty) {
      addCost(
        'Other (${folded.length})',
        sumMoney(folded.map((ReportLine line) => line.amount)),
      );
    }

    // The closing bar rises from zero, because it is a total rather than a change.
    final double net = chartValue(report.netProfit);
    steps.add(
      WaterfallStep(
        label: net < 0 ? 'Net loss' : 'Net profit',
        from: net < 0 ? net : 0,
        to: net < 0 ? 0 : net,
        exact: report.netProfit,
        runningTotal: report.netProfit,
        kind: WaterfallKind.total,
      ),
    );

    return steps;
  }
}

/// A card heading with a leading glyph.
class _ChartTitle extends StatelessWidget {
  const _ChartTitle({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Row(
      mainAxisSize: MainAxisSize.min,
      spacing: 8,
      children: <Widget>[
        Icon(icon, size: 16, color: t.contentMuted),
        Text(
          text,
          style: TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w600,
            color: t.content,
          ),
        ),
      ],
    );
  }
}
