import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/format.dart';
import '../../core/locale_settings.dart';
import '../../models/analytics.dart';
import '../../models/auth.dart';
import '../../models/organization.dart';
import '../../state/auth_controller.dart';
import '../../state/data_providers.dart';
import '../../theme/app_theme.dart';
import '../../theme/oklch.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_badge.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_card.dart';
import '../../widgets/charts.dart';
import '../../widgets/info_tip.dart';
import '../../widgets/metric_tile.dart';
import '../../widgets/primitives.dart';

/// The dashboard.
///
/// **Every financial figure here is real.** It was not always: until the ledger existed
/// these tiles showed illustrative numbers labelled "Sample", because an unlabelled fake
/// figure in an accounting product is the most damaging thing a screen can do. They now
/// come from `/analytics/dashboard`, which is computed by the same `ReportingService` that
/// renders the P&L - so a tile cannot disagree with the statement behind it.
class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  /// The chart window is fixed at twelve months, independent of the tiles' period: the
  /// tiles answer "how is this month going", the chart answers "what does the year look
  /// like", and tying them together would make one of the two useless.
  static const Period chartPeriod = Period.last12Months;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AppTokens t = context.tokens;
    final AuthState auth = ref.watch(authControllerProvider);
    final bool canSeeMoney = auth.can('report:read');
    final OrganizationSummary? organization = auth.organization;
    final String firstName = auth.user?.firstName ?? 'there';

    // No organization yet: onboarding, not a dashboard.
    if (organization == null) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          PageHeader(title: 'Welcome, $firstName'),
          AppCard(
            child: EmptyState(
              icon: LucideIcons.building2,
              title: 'Create your organization',
              description:
                  'An organization holds your books, your team, and your data. Create one '
                  'to get started, or ask a colleague to invite you to theirs.',
              action: AppButton(
                // `create=1` scrolls Settings to the form rather than landing at
                // the top of the page with it below the fold.
                onPressed: () => context.go('/settings?create=1'),
                leftIcon: LucideIcons.plus,
                label: 'Create organization',
              ),
            ),
          ),
        ],
      );
    }

    final Dashboard? dashboard = canSeeMoney
        ? ref.watch(dashboardProvider(Period.thisMonth)).valueOrNull
        : null;
    final Trend? trend = canSeeMoney
        ? ref.watch(trendProvider(chartPeriod)).valueOrNull
        : null;
    final ControlChecks? checks = canSeeMoney
        ? ref.watch(controlChecksProvider).valueOrNull
        : null;
    final List<Member>? members = auth.can('member:read')
        ? ref.watch(membersProvider).valueOrNull
        : null;
    final AuditFeed? activity = auth.can('audit:read')
        ? ref.watch(auditFeedProvider(const AuditFilter())).valueOrNull
        : null;
    final List<OrganizationListItem>? organizations = ref
        .watch(organizationsProvider)
        .valueOrNull;

    final String currency = dashboard?.currency ?? localeSettings().currency;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        PageHeader(
          title: 'Good ${_greeting()}, $firstName',
          description: dashboard != null
              ? '${dashboard.periodLabel} at ${organization.name} - '
                    '${formatDate(dashboard.span.start)} to ${formatDate(dashboard.span.end)}.'
              : 'Here is what is happening at ${organization.name}.',
          action: Row(
            mainAxisSize: MainAxisSize.min,
            spacing: 8,
            children: <Widget>[
              // The primary action on the home screen, because recording money is the
              // thing people open this software to do.
              if (auth.can('journal:write'))
                AppButton(
                  onPressed: () => context.go('/billing'),
                  leftIcon: LucideIcons.plus,
                  label: 'Record money',
                ),
              const AppButton(
                onPressed: null,
                variant: AppButtonVariant.secondary,
                leftIcon: LucideIcons.sparkles,
                label: 'Ask AI',
              ),
            ],
          ),
        ),

        // A control account that disagrees with its documents is the one problem on this
        // screen worth interrupting for: every figure below is derived from the ledger, so
        // if the ledger has drifted, they are all suspect.
        if (checks != null && !checks.allAgree) ...<Widget>[
          AppCard(
            borderColour: t.danger.at(0.3),
            background: t.dangerBg,
            padding: const EdgeInsets.all(20),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 12,
              children: <Widget>[
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Icon(
                    LucideIcons.triangleAlert,
                    size: 16,
                    color: t.danger,
                  ),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        'The ledger does not agree with your documents',
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w500,
                          color: t.content,
                        ),
                      ),
                      const SizedBox(height: 4),
                      for (final ControlCheck check in checks.checks.where(
                        (ControlCheck c) => !c.agrees,
                      ))
                        Text.rich(
                          TextSpan(
                            children: <InlineSpan>[
                              TextSpan(
                                text: check.name,
                                style: const TextStyle(
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              TextSpan(
                                text:
                                    ': ledger '
                                    '${formatMoney(check.ledger, currency: currency)}, '
                                    'documents '
                                    '${formatMoney(check.subledger, currency: currency)} - '
                                    'a difference of '
                                    '${formatMoney(check.difference, currency: currency)}',
                              ),
                            ],
                          ),
                          style: TextStyle(
                            fontSize: 13,
                            color: t.contentSecondary,
                            height: 1.5,
                          ),
                        ),
                      const SizedBox(height: 4),
                      Text(
                        'Something was recorded in one place and not the other. The figures '
                        'below are derived from the ledger, so treat them as unconfirmed '
                        'until this is resolved.',
                        style: TextStyle(
                          fontSize: 13,
                          color: t.contentMuted,
                          height: 1.5,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
        ],

        // Day one is a wall of zeroes. Without a next step that reads as "this software is
        // broken" rather than "you have not entered anything yet".
        if (canSeeMoney &&
            dashboard != null &&
            isZeroMoney(dashboard.revenue.current) &&
            isZeroMoney(dashboard.expenses.current) &&
            dashboard.invoicesIssued == 0) ...<Widget>[
          AppCard(
            borderColour: t.primary.at(0.25),
            background: t.primary.at(0.05),
            padding: const EdgeInsets.all(20),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        'Nothing recorded for '
                        '${dashboard.periodLabel.toLowerCase()} yet',
                        style: TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w500,
                          color: t.content,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        'Record what you have received and spent, and these figures fill in.',
                        style: TextStyle(fontSize: 13, color: t.contentMuted),
                      ),
                    ],
                  ),
                ),
                if (auth.can('journal:write'))
                  AppButton(
                    onPressed: () => context.go('/billing'),
                    leftIcon: LucideIcons.plus,
                    label: 'Record money in or out',
                  ),
              ],
            ),
          ),
          const SizedBox(height: 16),
        ],

        // ---- Performance ----
        if (canSeeMoney)
          TileGrid(
            children: <Widget>[
              MovementTile(
                label: 'Revenue',
                movement: dashboard?.revenue,
                currency: currency,
                icon: LucideIcons.trendingUp,
                info: <Widget>[
                  infoText(
                    'Everything you earned this period - money recorded as coming in, '
                    'plus any invoices you posted.',
                  ),
                  infoRich(<String>[
                    '',
                    'GST is excluded.',
                    ' Tax you collect belongs to the government, so counting it as '
                        'revenue would flatter the business.',
                  ]),
                ],
              ),
              MovementTile(
                label: 'Expenses',
                movement: dashboard?.expenses,
                currency: currency,
                icon: LucideIcons.wallet,
                risingIsGood: false,
                info: <Widget>[
                  infoText(
                    'Everything you spent this period. Includes household categories if '
                    'you use them, which is why this can look higher than a purely '
                    'business figure.',
                  ),
                ],
              ),
              MovementTile(
                label: 'Net profit',
                movement: dashboard?.netProfit,
                currency: currency,
                icon: LucideIcons.trendingUp,
                info: <Widget>[
                  infoText(
                    'Revenue less expenses. Negative means you spent more than you earned.',
                  ),
                  infoText(
                    'This is not the same as cash: an unpaid invoice counts as revenue '
                    'before the money arrives.',
                  ),
                ],
              ),
              MetricTile(
                label: 'Cash and bank',
                value: dashboard == null
                    ? null
                    : formatMoney(dashboard.cash, currency: currency),
                icon: LucideIcons.landmark,
                hint: dashboard == null
                    ? null
                    : 'as at ${formatDate(dashboard.span.end)}',
                info: <Widget>[
                  infoText(
                    'What you actually hold across every cash and bank account, right now '
                    '- not for the period.',
                  ),
                  infoRich(<String>[
                    '',
                    'A negative figure means an entry is wrong',
                    ', since you cannot pay out cash you never had. Usually a payment '
                        'recorded against the wrong account.',
                  ]),
                ],
              ),
            ],
          )
        else
          AppCard(
            padding: const EdgeInsets.all(16),
            child: Text(
              'You do not have permission to view financial reports.',
              style: TextStyle(fontSize: 13, color: t.contentMuted),
            ),
          ),

        // ---- Position ----
        if (canSeeMoney) ...<Widget>[
          const SizedBox(height: 16),
          TileGrid(
            children: <Widget>[
              MetricTile(
                label: 'Owed to you',
                value: dashboard == null
                    ? null
                    : formatMoney(dashboard.receivables, currency: currency),
                icon: LucideIcons.receipt,
                hint:
                    dashboard != null &&
                        !isZeroMoney(dashboard.overdueReceivables)
                    ? '${formatMoney(dashboard.overdueReceivables, currency: currency)} overdue'
                    : null,
                hintTone:
                    dashboard != null &&
                        !isZeroMoney(dashboard.overdueReceivables)
                    ? t.danger
                    : null,
                info: <Widget>[
                  infoRich(<String>[
                    'Money customers still owe on ',
                    'invoices you posted',
                    ' and they have not fully paid.',
                  ]),
                  infoText(
                    'It stays at zero if you only use the Billing screen - recording money '
                    'in means the cash already arrived, so nobody owes you anything. This '
                    'fills up only when you raise an invoice under Sales and wait to be paid.',
                  ),
                ],
              ),
              MetricTile(
                label: 'You owe',
                value: dashboard == null
                    ? null
                    : formatMoney(dashboard.payables, currency: currency),
                icon: LucideIcons.fileText,
                hint:
                    dashboard != null && !isZeroMoney(dashboard.overduePayables)
                    ? '${formatMoney(dashboard.overduePayables, currency: currency)} overdue'
                    : null,
                hintTone:
                    dashboard != null && !isZeroMoney(dashboard.overduePayables)
                    ? t.danger
                    : null,
                info: <Widget>[
                  infoRich(<String>[
                    'Money you still owe on ',
                    'supplier bills you entered',
                    ' and have not paid yet.',
                  ]),
                  infoText(
                    'Also zero while you only use Billing: recording money out means you '
                    'have already paid, so there is no debt left to track. Entering a bill '
                    'under Inventory without paying it is what fills this in.',
                  ),
                ],
              ),
              MetricTile(
                label: 'Stock value',
                value: dashboard == null
                    ? null
                    : formatMoney(dashboard.inventoryValue, currency: currency),
                icon: LucideIcons.boxes,
                info: <Widget>[
                  infoText(
                    'What your unsold stock cost you, valued at weighted average. Only '
                    'fills in if you track products under Inventory.',
                  ),
                ],
              ),
              MetricTile(
                label: 'Team members',
                value: members == null ? null : '${members.length}',
                icon: LucideIcons.users,
                hint: members == null
                    ? null
                    : '${members.where((Member m) => m.isActive).length} active',
              ),
            ],
          ),
        ],

        // ---- Chart and activity ----
        const SizedBox(height: 16),
        LayoutBuilder(
          builder: (BuildContext context, BoxConstraints constraints) {
            final bool wide = constraints.maxWidth >= 1180;
            final Widget chart = _TrendCard(
              trend: trend,
              currency: currency,
              canSeeMoney: canSeeMoney,
            );
            final Widget feed = _ActivityCard(
              activity: activity,
              canSeeAudit: auth.can('audit:read'),
            );

            if (!wide) {
              return Column(spacing: 16, children: <Widget>[chart, feed]);
            }
            return Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 16,
              children: <Widget>[
                Expanded(flex: 2, child: chart),
                Expanded(child: feed),
              ],
            );
          },
        ),

        // Reconciliation is stated even when it passes: a control that is only visible
        // when it fails gives no confidence when it does not.
        if (canSeeMoney && checks != null && checks.allAgree) ...<Widget>[
          const SizedBox(height: 16),
          Row(
            spacing: 6,
            children: <Widget>[
              Icon(LucideIcons.shieldCheck, size: 14, color: t.success),
              Expanded(
                child: Text(
                  'Receivables, payables, and stock all reconcile to the ledger as at '
                  '${formatDate(checks.asOf)}.',
                  style: TextStyle(fontSize: 12, color: t.contentMuted),
                ),
              ),
            ],
          ),
        ],

        // ---- Organizations ----
        if (organizations != null && organizations.length > 1) ...<Widget>[
          const SizedBox(height: 16),
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                CardHeader(
                  title: 'Your organizations',
                  description: 'Switch with ${_shortcutHint()}',
                ),
                CardBody(
                  child: Wrap(
                    spacing: 12,
                    runSpacing: 12,
                    children: <Widget>[
                      for (final OrganizationListItem item in organizations)
                        SizedBox(
                          width: 260,
                          child: Container(
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(
                              color: t.surfaceSunken.at(0.4),
                              borderRadius: BorderRadius.circular(Radii.lg),
                              border: Border.all(color: t.border),
                            ),
                            child: Row(
                              spacing: 12,
                              children: <Widget>[
                                Container(
                                  width: 32,
                                  height: 32,
                                  decoration: BoxDecoration(
                                    color: t.primary.at(0.12),
                                    borderRadius: BorderRadius.circular(
                                      Radii.md,
                                    ),
                                  ),
                                  alignment: Alignment.center,
                                  child: Text(
                                    item.name
                                        .substring(
                                          0,
                                          item.name.length >= 2 ? 2 : 1,
                                        )
                                        .toUpperCase(),
                                    style: TextStyle(
                                      fontSize: 11,
                                      fontWeight: FontWeight.w700,
                                      color: t.primary,
                                    ),
                                  ),
                                ),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: <Widget>[
                                      Text(
                                        item.name,
                                        overflow: TextOverflow.ellipsis,
                                        style: TextStyle(
                                          fontSize: 13,
                                          fontWeight: FontWeight.w500,
                                          color: t.content,
                                        ),
                                      ),
                                      Text(
                                        '${item.roleName} · ${item.memberCount} '
                                        'member${item.memberCount == 1 ? '' : 's'}',
                                        style: TextStyle(
                                          fontSize: 11,
                                          color: t.contentMuted,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                                if (item.id == organization.id)
                                  const AppBadge(
                                    'Current',
                                    tone: BadgeTone.primary,
                                  ),
                              ],
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ],
    );
  }

  static String _greeting() {
    final int hour = DateTime.now().hour;
    if (hour < 12) return 'morning';
    if (hour < 17) return 'afternoon';
    return 'evening';
  }

  static String _shortcutHint() => '⌘K';
}

class _TrendCard extends StatelessWidget {
  const _TrendCard({
    required this.trend,
    required this.currency,
    required this.canSeeMoney,
  });

  final Trend? trend;
  final String currency;
  final bool canSeeMoney;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            title: 'Revenue and expenses',
            description: 'Last twelve months, from posted ledger entries',
            action: trend == null
                ? null
                : Text(
                    '${formatMoney(trend!.totalProfit, currency: currency)} profit',
                    style: TextStyle(
                      fontSize: 12,
                      color: t.contentMuted,
                      fontFeatures: tabularFigures,
                    ),
                  ),
          ),
          CardBody(
            child: !canSeeMoney
                ? Padding(
                    padding: const EdgeInsets.symmetric(vertical: 64),
                    child: Text(
                      'You do not have permission to view financial reports.',
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 13, color: t.contentMuted),
                    ),
                  )
                : trend == null
                ? const Skeleton(height: 280)
                : _buildChart(context, t),
          ),
        ],
      ),
    );
  }

  Widget _buildChart(BuildContext context, AppTokens t) {
    final List<TrendPoint> points = trend!.points;
    final bool empty = points.every(
      (TrendPoint p) => isZeroMoney(p.income) && isZeroMoney(p.expenses),
    );

    if (empty) {
      return const EmptyState(
        icon: LucideIcons.trendingUp,
        title: 'Nothing posted yet',
        description:
            'Once you post an invoice or a bill, twelve months of revenue and expenses '
            'appear here.',
        verticalPadding: 64,
      );
    }

    return Column(
      children: <Widget>[
        MoneyAreaChart(
          currency: currency,
          series: <ChartSeries>[
            ChartSeries(
              name: 'Revenue',
              colour: t.primary,
              points: <ChartPoint>[
                for (final TrendPoint point in points)
                  ChartPoint(
                    label: point.label,
                    value: chartValue(point.income),
                    exact: point.income,
                  ),
              ],
            ),
            ChartSeries(
              name: 'Expenses',
              colour: t.warning,
              points: <ChartPoint>[
                for (final TrendPoint point in points)
                  ChartPoint(
                    label: point.label,
                    value: chartValue(point.expenses),
                    exact: point.expenses,
                  ),
              ],
            ),
          ],
        ),
        ChartLegend(
          series: <ChartSeries>[
            ChartSeries(
              name: 'Revenue',
              colour: t.primary,
              points: const <ChartPoint>[],
            ),
            ChartSeries(
              name: 'Expenses',
              colour: t.warning,
              points: const <ChartPoint>[],
            ),
          ],
        ),
      ],
    );
  }
}

class _ActivityCard extends StatelessWidget {
  const _ActivityCard({required this.activity, required this.canSeeAudit});

  final AuditFeed? activity;
  final bool canSeeAudit;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            title: 'Recent activity',
            description: 'From the audit trail',
            action: canSeeAudit
                ? AppTextLink(
                    label: 'View all',
                    onTap: () => context.go('/audit'),
                  )
                : null,
          ),
          CardBody(
            child: !canSeeAudit
                ? Padding(
                    padding: const EdgeInsets.symmetric(vertical: 32),
                    child: Text(
                      'You do not have permission to view the audit trail.',
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 13, color: t.contentMuted),
                    ),
                  )
                : activity == null
                ? Column(
                    spacing: 12,
                    children: <Widget>[
                      for (int index = 0; index < 5; index++)
                        const Row(
                          spacing: 12,
                          children: <Widget>[
                            Skeleton(width: 28, height: 28, radius: Radii.full),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                spacing: 6,
                                children: <Widget>[
                                  Skeleton(height: 12),
                                  Skeleton(width: 80, height: 10),
                                ],
                              ),
                            ),
                          ],
                        ),
                    ],
                  )
                : activity!.entries.isEmpty
                ? const EmptyState(
                    icon: LucideIcons.fileText,
                    title: 'Nothing yet',
                    description:
                        'Actions across your organization will appear here.',
                    verticalPadding: 32,
                  )
                : Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    spacing: 14,
                    children: <Widget>[
                      for (final AuditEntry entry in activity!.entries.take(6))
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          spacing: 12,
                          children: <Widget>[
                            Container(
                              margin: const EdgeInsets.only(top: 5),
                              width: 6,
                              height: 6,
                              decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                color: switch (entry.severity) {
                                  'critical' => t.danger,
                                  'warning' => t.warning,
                                  _ => t.success,
                                },
                              ),
                            ),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: <Widget>[
                                  Text(
                                    entry.summary ?? entry.action,
                                    style: TextStyle(
                                      fontSize: 13,
                                      color: t.content,
                                      height: 1.35,
                                    ),
                                  ),
                                  const SizedBox(height: 2),
                                  Text(
                                    '${entry.actor.display} · '
                                    '${formatRelative(entry.createdAt)}',
                                    style: TextStyle(
                                      fontSize: 11,
                                      color: t.contentMuted,
                                    ),
                                  ),
                                ],
                              ),
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
