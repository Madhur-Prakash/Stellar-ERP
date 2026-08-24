import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/format.dart';
import '../../core/locale_settings.dart';
import '../../models/analytics.dart';
import '../../state/data_providers.dart';
import '../../theme/app_theme.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_badge.dart';
import '../../widgets/app_card.dart';
import '../../widgets/app_select.dart';
import '../../widgets/charts.dart';
import '../../widgets/data_table.dart';
import '../../widgets/metric_tile.dart';
import '../../widgets/primitives.dart';

/// Analytics - the same figures as the statements, arranged for scanning.
///
/// Everything here is derived server-side from the ledger by the service that renders the
/// P&L, so nothing on this screen can disagree with the accounts. Three deliberate
/// presentation choices:
///
/// * **The period selector changes one thing: the window.** All five panels move together,
///   because comparing a month-to-date revenue figure against a year-to-date customer
///   ranking is how people reach wrong conclusions from correct numbers.
/// * **Concentration is stated, not left to be computed.** "These five customers are 62% of
///   revenue" is the useful reading of a top-five list; five names and five amounts on their
///   own are not.
/// * **Reconciliation is shown when it passes, too.** A control that only appears when it
///   fails offers no reassurance when it holds.
class AnalyticsScreen extends ConsumerStatefulWidget {
  const AnalyticsScreen({super.key});

  @override
  ConsumerState<AnalyticsScreen> createState() => _AnalyticsScreenState();
}

class _AnalyticsScreenState extends ConsumerState<AnalyticsScreen> {
  Period _period = Period.thisFiscalYear;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final PeriodOptions? options = ref.watch(periodOptionsProvider).valueOrNull;
    final Dashboard? dashboard = ref
        .watch(dashboardProvider(_period))
        .valueOrNull;
    final AsyncValue<Trend> trend = ref.watch(trendProvider(_period));
    final AsyncValue<Ranking> customers = ref.watch(
      topCustomersProvider(_period),
    );
    final AsyncValue<Ranking> products = ref.watch(
      topProductsProvider(_period),
    );
    final AsyncValue<ControlChecks> checks = ref.watch(controlChecksProvider);

    // The organization's currency, not a literal: the response carries one, and the fallback
    // has to come from the session rather than from a hardcoded 'INR'.
    final String currency = dashboard?.currency ?? localeSettings().currency;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        PageHeader(
          title: 'Analytics',
          description: dashboard != null
              ? '${dashboard.periodLabel}: ${formatDate(dashboard.span.start)} to '
                    '${formatDate(dashboard.span.end)}, compared against '
                    '${formatDate(dashboard.comparison.start)} to '
                    '${formatDate(dashboard.comparison.end)}.'
              : 'Figures derived from the ledger - the same source as the financial '
                    'statements.',
          action: SizedBox(
            width: 220,
            child: AppSelect(
              value: _period.wire,
              options: <SelectOption>[
                for (final PeriodOption option
                    in options?.options ?? const <PeriodOption>[])
                  SelectOption(value: option.value.wire, label: option.label),
              ],
              onChanged: (String next) =>
                  setState(() => _period = Period.parse(next)),
            ),
          ),
        ),

        // ---- Headline ----
        TileGrid(
          children: <Widget>[
            MovementTile(
              label: 'Revenue',
              movement: dashboard?.revenue,
              currency: currency,
              valueSize: 22,
            ),
            MovementTile(
              label: 'Gross profit',
              movement: dashboard?.grossProfit,
              currency: currency,
              valueSize: 22,
            ),
            MovementTile(
              label: 'Expenses',
              movement: dashboard?.expenses,
              currency: currency,
              risingIsGood: false,
              valueSize: 22,
            ),
            MovementTile(
              label: 'Net profit',
              movement: dashboard?.netProfit,
              currency: currency,
              valueSize: 22,
            ),
          ],
        ),
        const SizedBox(height: 16),

        // ---- Trend ----
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              CardHeader(
                title: 'Income and expenses by month',
                description:
                    'Bars sum exactly to the totals above - the series is derived from the '
                    'same posted entries.',
                action: trend.valueOrNull == null
                    ? null
                    : Text(
                        '${formatMoney(trend.value!.totalIncome, currency: currency)} in · '
                        '${formatMoney(trend.value!.totalExpenses, currency: currency)} out',
                        style: TextStyle(
                          fontSize: 12,
                          color: t.contentMuted,
                          fontFeatures: tabularFigures,
                        ),
                      ),
              ),
              CardBody(
                child: trend.valueOrNull == null
                    ? const Skeleton(height: 300)
                    : _MonthlyBars(trend: trend.value!, currency: currency),
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),

        // ---- Rankings ----
        LayoutBuilder(
          builder: (BuildContext context, BoxConstraints constraints) {
            final Widget left = _RankingCard(
              title: 'Top customers',
              description:
                  "Ranked on taxable value - GST collected is the government's money, not "
                  'revenue.',
              ranking: customers,
              currency: currency,
              countLabel: 'invoices',
            );
            final Widget right = _RankingCard(
              title: 'Best-selling lines',
              description:
                  'Grouped by line description, so services and one-off charges are counted '
                  'too.',
              ranking: products,
              currency: currency,
              countLabel: 'lines',
            );
            if (constraints.maxWidth < 1000) {
              return Column(spacing: 16, children: <Widget>[left, right]);
            }
            return Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 16,
              children: <Widget>[
                Expanded(child: left),
                Expanded(child: right),
              ],
            );
          },
        ),
        const SizedBox(height: 16),

        // ---- Reconciliation ----
        _ReconciliationCard(checks: checks, currency: currency),
      ],
    );
  }
}

/// Grouped bars per month.
class _MonthlyBars extends StatelessWidget {
  const _MonthlyBars({required this.trend, required this.currency});

  final Trend trend;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final bool empty = trend.points.every(
      (TrendPoint p) => isZeroMoney(p.income) && isZeroMoney(p.expenses),
    );

    if (empty) {
      return const EmptyState(
        icon: LucideIcons.trendingUp,
        title: 'Nothing posted in this period',
        description: 'Post an invoice or a bill and it appears here.',
        verticalPadding: 64,
      );
    }

    final List<ChartSeries> series = <ChartSeries>[
      ChartSeries(
        name: 'Income',
        colour: t.primary,
        points: <ChartPoint>[
          for (final TrendPoint p in trend.points)
            ChartPoint(
              label: p.label,
              value: chartValue(p.income),
              exact: p.income,
            ),
        ],
      ),
      ChartSeries(
        name: 'Expenses',
        colour: t.warning,
        points: <ChartPoint>[
          for (final TrendPoint p in trend.points)
            ChartPoint(
              label: p.label,
              value: chartValue(p.expenses),
              exact: p.expenses,
            ),
        ],
      ),
    ];

    return Column(
      children: <Widget>[
        GroupedBarChart(series: series, currency: currency),
        ChartLegend(series: series),
      ],
    );
  }
}

class _RankingCard extends StatelessWidget {
  const _RankingCard({
    required this.title,
    required this.description,
    required this.ranking,
    required this.currency,
    required this.countLabel,
  });

  final String title;
  final String description;
  final AsyncValue<Ranking> ranking;
  final String currency;
  final String countLabel;

  @override
  Widget build(BuildContext context) {
    final Ranking? data = ranking.valueOrNull;

    // Concentration: what share of the whole period the listed rows account for. The reason
    // a top-five list is worth showing at all.
    int? share;
    if (data != null && !isZeroMoney(data.total)) {
      final String shown = sumMoney(
        data.rows.map((RankedRow row) => row.amount),
      );
      final double whole = chartValue(data.total);
      if (whole > 0) share = (chartValue(shown) / whole * 100).round();
    }

    return AppCard(
      child: Column(
        children: <Widget>[
          CardHeader(
            title: title,
            description: description,
            action: share == null
                ? null
                : AppBadge(
                    '$share% of total',
                    tooltip: "Share of the period's total taxable value",
                  ),
          ),
          AppDataTable<RankedRow>(
            rows: data?.rows ?? const <RankedRow>[],
            rowKey: (RankedRow row) => row.id ?? row.label,
            isLoading: data == null,
            empty: const EmptyState(
              title: 'Nothing in this period',
              description: 'Post an invoice and it appears here.',
            ),
            columns: <AppColumn<RankedRow>>[
              AppColumn<RankedRow>(
                header: '',
                cell: (RankedRow row) =>
                    Text(row.label, overflow: TextOverflow.ellipsis),
              ),
              AppColumn<RankedRow>(
                header: countLabel,
                numeric: true,
                hideOnNarrow: true,
                cell: (RankedRow row) => Text('${row.count}'),
              ),
              AppColumn<RankedRow>(
                header: 'Value',
                numeric: true,
                cell: (RankedRow row) =>
                    Text(formatMoney(row.amount, currency: currency)),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// Control-account reconciliation.
///
/// Each figure is derived twice - once from the control account, once from the documents
/// that should have produced it. This is the check a bookkeeper does monthly by hand;
/// showing it means a document that updated one table and not the other is caught in days
/// rather than found by an accountant a year later.
class _ReconciliationCard extends StatelessWidget {
  const _ReconciliationCard({required this.checks, required this.currency});

  final AsyncValue<ControlChecks> checks;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final ControlChecks? data = checks.valueOrNull;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            title: 'Reconciliation',
            description: data != null
                ? 'Ledger against source documents as at ${formatDate(data.asOf)}. '
                      'These must agree.'
                : 'Ledger against source documents.',
            action: data == null
                ? null
                : Row(
                    mainAxisSize: MainAxisSize.min,
                    spacing: 6,
                    children: <Widget>[
                      Icon(
                        data.allAgree
                            ? LucideIcons.shieldCheck
                            : LucideIcons.triangleAlert,
                        size: 14,
                        color: data.allAgree ? t.success : t.danger,
                      ),
                      Text(
                        data.allAgree ? 'All reconcile' : 'Discrepancy found',
                        style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w500,
                          color: data.allAgree ? t.success : t.danger,
                        ),
                      ),
                    ],
                  ),
          ),
          AppDataTable<ControlCheck>(
            rows: data?.checks ?? const <ControlCheck>[],
            rowKey: (ControlCheck row) => row.name,
            isLoading: data == null,
            columns: <AppColumn<ControlCheck>>[
              AppColumn<ControlCheck>(
                header: 'Control account',
                cell: (ControlCheck row) => Text(row.name),
              ),
              AppColumn<ControlCheck>(
                header: 'Ledger',
                numeric: true,
                cell: (ControlCheck row) =>
                    Text(formatMoney(row.ledger, currency: currency)),
              ),
              AppColumn<ControlCheck>(
                header: 'Documents',
                numeric: true,
                cell: (ControlCheck row) =>
                    Text(formatMoney(row.subledger, currency: currency)),
              ),
              AppColumn<ControlCheck>(
                header: 'Difference',
                numeric: true,
                cell: (ControlCheck row) => Text(
                  formatMoney(row.difference, currency: currency),
                  style: TextStyle(
                    color: row.agrees ? t.contentMuted : t.danger,
                    fontWeight: row.agrees ? FontWeight.w400 : FontWeight.w600,
                  ),
                ),
              ),
              AppColumn<ControlCheck>(
                header: '',
                fixedWidth: 110,
                cell: (ControlCheck row) => row.agrees
                    ? const AppBadge('Agrees', tone: BadgeTone.success)
                    : const AppBadge('Check this', tone: BadgeTone.danger),
              ),
            ],
          ),
          if (data != null && !data.allAgree)
            CardBody(
              padding: const EdgeInsets.only(
                left: 20,
                right: 20,
                top: 12,
                bottom: 20,
              ),
              child: Text(
                'A difference means something was recorded in one place and not the other - '
                'most often a document edited outside the normal flow. Every figure on this '
                'screen is derived from the ledger, so resolve this before relying on them.',
                style: TextStyle(
                  fontSize: 12,
                  color: t.contentSecondary,
                  height: 1.5,
                ),
              ),
            ),
        ],
      ),
    );
  }
}
