import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:file_picker/file_picker.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:path/path.dart' as p;

import '../../core/format.dart';
import '../../core/locale_settings.dart';
import '../../models/accounting.dart';
import '../../models/analytics.dart';
import '../../models/page.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../../theme/app_theme.dart';
import '../../theme/oklch.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_badge.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_card.dart';
import '../../widgets/data_table.dart';
import '../../widgets/info_tip.dart';
import '../../widgets/metric_tile.dart';
import '../../widgets/primitives.dart';
import '../../widgets/toast.dart';
import 'accounting_charts.dart';
import 'report_range.dart';

/// Accounting - chart of accounts, journal entries, and the financial statements.
///
/// One screen with tabs rather than five routes: an accountant moves between the trial
/// balance and the ledger constantly, and a full route transition on every switch is slower
/// than keeping the queries warm in one place.
class AccountingScreen extends ConsumerWidget {
  const AccountingScreen({super.key, this.tab});

  final String? tab;

  static const List<(String, String)> _tabs = <(String, String)>[
    ('chart', 'Chart of accounts'),
    ('entries', 'Journal entries'),
    ('trial-balance', 'Trial balance'),
    ('pnl', 'Profit & loss'),
    ('balance-sheet', 'Balance sheet'),
  ];

  /// Narrows an untrusted query parameter to a known tab, so a hand-edited value falls
  /// back to the default instead of breaking the screen.
  String get _active {
    final bool known = _tabs.any(((String, String) entry) => entry.$1 == tab);
    return known ? tab! : 'chart';
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const PageHeader(
          title: 'Accounting',
          description:
              'Double-entry ledger. Posted entries are immutable - corrections are made by '
              'reversal.',
        ),
        AppTabs(
          tabs: _tabs,
          active: _active,
          semanticLabel: 'Accounting views',
          // `replace` keeps tab switching out of the back stack.
          onChanged: (String next) => context.replace('/accounting?tab=$next'),
        ),
        switch (_active) {
          'entries' => const _JournalEntriesTab(),
          'trial-balance' => const _TrialBalanceTab(),
          'pnl' => const _ProfitAndLossTab(),
          'balance-sheet' => const _BalanceSheetTab(),
          _ => const _ChartOfAccountsTab(),
        },
      ],
    );
  }
}

// =============================================================================
// A range-driven tab
// =============================================================================
/// Holds the report range for the tabs that have one.
///
/// One range control drives every chart on a tab. Separate filters per chart would let two
/// panels sit side by side showing different periods, which is a reliable way to draw a
/// wrong conclusion from correct numbers.
mixin _RangeState<T extends StatefulWidget> on State<T> {
  RangePreset preset = RangePreset.yearToDate;
  DateRange? customRange;

  DateRange resolvedRange(PeriodOptions? periods) {
    final int fiscalStart =
        periods?.fiscalYearStartMonth ?? localeSettings().fiscalYearStartMonth;
    // The server's today, in the organization's timezone - not the machine's.
    final DateTime today = periods?.today != null
        ? DateTime.parse(periods!.today)
        : DateTime.now();

    if (preset == RangePreset.custom) {
      final DateRange custom =
          customRange ??
          resolveRange(RangePreset.yearToDate, today, fiscalStart);
      // A reversed range would be rejected by the server anyway; collapsing it keeps the
      // report on screen while the user is mid-edit rather than flashing an error.
      return custom.toDate.compareTo(custom.fromDate) < 0
          ? DateRange(fromDate: custom.toDate, toDate: custom.toDate)
          : custom;
    }
    return resolveRange(preset, today, fiscalStart);
  }

  Widget rangeSelector(PeriodOptions? periods) {
    final int fiscalStart =
        periods?.fiscalYearStartMonth ?? localeSettings().fiscalYearStartMonth;
    final DateTime today = periods?.today != null
        ? DateTime.parse(periods!.today)
        : DateTime.now();

    return ReportRangeSelector(
      preset: preset,
      custom:
          customRange ??
          resolveRange(RangePreset.yearToDate, today, fiscalStart),
      today: today,
      fiscalStartMonth: fiscalStart,
      onPresetChanged: (RangePreset next) => setState(() {
        // Seed the custom fields from whatever is on screen, so switching to Custom does
        // not blank the report.
        if (next == RangePreset.custom) {
          customRange = resolvedRange(periods);
        }
        preset = next;
      }),
      onCustomChanged: (DateRange next) => setState(() => customRange = next),
    );
  }
}

// =============================================================================
// Chart of accounts
// =============================================================================
class _ChartOfAccountsTab extends ConsumerStatefulWidget {
  const _ChartOfAccountsTab();

  @override
  ConsumerState<_ChartOfAccountsTab> createState() =>
      _ChartOfAccountsTabState();
}

class _ChartOfAccountsTabState extends ConsumerState<_ChartOfAccountsTab>
    with _RangeState {
  @override
  Widget build(BuildContext context) {
    final PeriodOptions? periods = ref.watch(periodOptionsProvider).valueOrNull;
    final DateRange range = resolvedRange(periods);
    final String currency = localeSettings().currency;

    // Balances are point-in-time, so only the end of the range applies - "cash over March"
    // is not a number.
    final AsyncValue<List<Account>> accounts = ref.watch(
      accountsProvider(range.toDate),
    );
    // The waterfall's closing bar must equal the dashboard's net profit, so it is built
    // from the statement rather than recomputed.
    final AsyncValue<ProfitAndLoss> report = ref.watch(
      profitAndLossProvider(range),
    );
    final Trend? trend = ref.watch(trendForRangeProvider(range)).valueOrNull;

    final List<Account> rows = accounts.valueOrNull ?? const <Account>[];

    // The 114-row table is gone. It listed every account in the template, of which four
    // hold a balance, so it was a hundred rows of zero in front of the four figures anyone
    // came here for. The charts show what has money; the trial balance is the place to read
    // exact per-account figures.
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      spacing: 16,
      children: <Widget>[
        AppCard(
          child: CardHeader(
            title: 'Period',
            description:
                'Every chart below covers ${range.fromDate} to ${range.toDate}.',
            action: rangeSelector(periods),
          ),
        ),
        ProfitWaterfallCard(
          report: report.valueOrNull,
          currency: currency,
          isLoading: report.isLoading,
        ),
        if (accounts.isLoading)
          AppCard(
            padding: const EdgeInsets.all(20),
            child: const Skeleton(height: 256),
          )
        else ...<Widget>[
          LayoutBuilder(
            builder: (BuildContext context, BoxConstraints constraints) {
              final Widget balances = AccountBalancesChart(
                accounts: rows,
                currency: currency,
              );
              final Widget mix = SpendingMixChart(
                accounts: rows,
                currency: currency,
              );
              if (constraints.maxWidth < 1180) {
                return Column(spacing: 16, children: <Widget>[balances, mix]);
              }
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                spacing: 16,
                children: <Widget>[
                  Expanded(child: balances),
                  Expanded(child: mix),
                ],
              );
            },
          ),
          TrendCard(points: trend?.points, currency: currency),
          BalanceByTypeChart(accounts: rows, currency: currency),
        ],
      ],
    );
  }
}

// =============================================================================
// Journal entries
// =============================================================================
class _JournalEntriesTab extends ConsumerStatefulWidget {
  const _JournalEntriesTab();

  @override
  ConsumerState<_JournalEntriesTab> createState() => _JournalEntriesTabState();
}

class _JournalEntriesTabState extends ConsumerState<_JournalEntriesTab> {
  int _page = 1;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AsyncValue<Paged<JournalEntry>> entries = ref.watch(
      journalEntriesProvider(_page),
    );
    final Paged<JournalEntry>? page = entries.valueOrNull;
    final String currency = localeSettings().currency;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      spacing: 16,
      children: <Widget>[
        CashMovementChart(
          entries: page?.items ?? const <JournalEntry>[],
          currency: currency,
        ),
        AppCard(
          child: Column(
            children: <Widget>[
              AppDataTable<JournalEntry>(
                rows: page?.items ?? const <JournalEntry>[],
                rowKey: (JournalEntry row) => row.id,
                isLoading: entries.isLoading,
                empty: const EmptyState(
                  title: 'No journal entries',
                  description:
                      'Entries appear here as invoices, bills, and payments are posted.',
                ),
                columns: <AppColumn<JournalEntry>>[
                  AppColumn<JournalEntry>(
                    header: 'Number',
                    fixedWidth: 110,
                    cell: (JournalEntry row) => Text(
                      row.entryNumber ?? 'draft',
                      style: monoStyle(
                        color: row.entryNumber == null
                            ? t.contentMuted
                            : t.content,
                      ),
                    ),
                  ),
                  AppColumn<JournalEntry>(
                    header: 'Date',
                    fixedWidth: 116,
                    cell: (JournalEntry row) => Text(formatDate(row.entryDate)),
                  ),
                  AppColumn<JournalEntry>(
                    header: 'Narration',
                    cell: (JournalEntry row) => Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(row.narration, overflow: TextOverflow.ellipsis),
                        Text(
                          row.journalCode +
                              (row.reference != null
                                  ? ' · ${row.reference}'
                                  : ''),
                          style: TextStyle(fontSize: 11, color: t.contentMuted),
                        ),
                      ],
                    ),
                  ),
                  AppColumn<JournalEntry>(
                    header: 'Money',
                    hideOnNarrow: true,
                    fixedWidth: 150,
                    cell: (JournalEntry row) => row.cashDirection == null
                        // No cash leg, or a transfer between your own accounts that nets
                        // to nothing.
                        ? Text(
                            'no cash movement',
                            style: TextStyle(
                              fontSize: 12,
                              color: t.contentMuted,
                            ),
                          )
                        : Text(
                            '${row.cashDirection == 'in' ? 'In' : 'Out'} '
                            '${formatMoney(row.cashAmount, currency: currency)}',
                            style: TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w500,
                              color: row.cashDirection == 'in'
                                  ? t.success
                                  : t.danger,
                            ),
                          ),
                  ),
                  AppColumn<JournalEntry>(
                    header: 'Status',
                    hideOnNarrow: true,
                    fixedWidth: 170,
                    cell: (JournalEntry row) => row.isReversed
                        ? const AppBadge(
                            'Reversed - cancelled',
                            tone: BadgeTone.warning,
                            tooltip:
                                'Cancelled by an opposite entry. Both remain on the record.',
                          )
                        : row.reversesId != null
                        ? const AppBadge(
                            'Reversal entry',
                            tooltip: 'This entry cancels an earlier one.',
                          )
                        : AppBadge(
                            row.status,
                            tone: switch (row.status) {
                              'posted' => BadgeTone.success,
                              'reversed' => BadgeTone.warning,
                              _ => BadgeTone.neutral,
                            },
                          ),
                  ),
                  AppColumn<JournalEntry>(
                    header: 'Amount',
                    numeric: true,
                    cell: (JournalEntry row) => Text(
                      formatMoney(row.totalDebit, currency: currency),
                      style: TextStyle(
                        color: row.isReversed ? t.contentMuted : t.content,
                        decoration: row.isReversed
                            ? TextDecoration.lineThrough
                            : null,
                      ),
                    ),
                  ),
                ],
              ),
              if (page != null)
                Pagination(
                  page: page.meta.page,
                  totalPages: page.meta.totalPages,
                  totalItems: page.meta.totalItems,
                  onChanged: (int next) => setState(() => _page = next),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

// =============================================================================
// Trial balance
// =============================================================================
class _TrialBalanceTab extends ConsumerWidget {
  const _TrialBalanceTab();

  /// Did this account have movement that cancelled out?
  ///
  /// Distinct from "no activity": an account whose charge was reversed has a story, an
  /// untouched account does not, and showing both as two dashes conflates them.
  static bool _netsToNil(TrialBalanceRow row) =>
      isZeroMoney(row.debit) &&
      isZeroMoney(row.credit) &&
      !(isZeroMoney(row.grossDebit) && isZeroMoney(row.grossCredit));

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AppTokens t = context.tokens;
    final AsyncValue<TrialBalance> balance = ref.watch(trialBalanceProvider);
    final TrialBalance? data = balance.valueOrNull;
    final String currency = localeSettings().currency;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      spacing: 16,
      children: <Widget>[
        // Surfaced rather than hidden: an unbalanced ledger is the single most serious
        // condition this system can be in.
        if (data != null && !data.isBalanced)
          AppCard(
            borderColour: t.danger.at(0.4),
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
                        'Ledger does not balance',
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                          color: t.danger,
                        ),
                      ),
                      Text(
                        'Debits ${formatMoney(data.totalDebit, currency: currency)} ≠ '
                        'credits ${formatMoney(data.totalCredit, currency: currency)}. '
                        'This should be impossible - contact support.',
                        style: TextStyle(
                          fontSize: 12,
                          color: t.contentSecondary,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        AppCard(
          child: Column(
            children: <Widget>[
              CardHeader(
                // The ⓘ rather than a paragraph under the heading: the explanation is long
                // enough to push the figures down the screen, and most visits do not need
                // it.
                titleWidget: Row(
                  mainAxisSize: MainAxisSize.min,
                  spacing: 6,
                  children: <Widget>[
                    Text(
                      'Trial balance',
                      style: TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                        color: t.content,
                      ),
                    ),
                    InfoTip(
                      label: 'the trial balance',
                      children: <Widget>[
                        infoText(
                          'Every account that has money in it, and which side that money '
                          'sits on.',
                        ),
                        infoRich(<String>[
                          '',
                          'Debit',
                          ' is what you have and what you have spent. ',
                          'Credit',
                          ' is what you owe and what you have earned. They are just the two '
                              'sides of an entry, not good and bad.',
                        ]),
                        infoText(
                          'Every entry puts the same amount on both sides, so the two totals '
                          'at the bottom must match. That is the one thing this table proves '
                          '- and if they ever did not match, something would be wrong with '
                          'the books themselves rather than with any single entry.',
                        ),
                        infoRich(<String>[
                          '',
                          'A cash or bank account should appear under Debit.',
                          ' If one shows under Credit, the books say more went out of it '
                              'than ever went in - which is impossible for real cash, and '
                              'usually means money that came from a different account was '
                              'recorded against this one. The totals still balance, because '
                              'a wrong pair of entries balances just as well as a right one.',
                        ]),
                        infoRich(<String>[
                          '',
                          'Dealt with',
                          " lists the people and businesses behind an account's balance, "
                              'from the From/To field on the Billing screen. A dash means '
                              'those entries did not name anyone.',
                        ]),
                      ],
                    ),
                  ],
                ),
                description: data == null
                    ? null
                    : 'As at ${formatDate(data.asOf)}',
                action: data?.isBalanced == true
                    ? const AppBadge(
                        'Balanced',
                        tone: BadgeTone.success,
                        dot: true,
                      )
                    : null,
              ),
              AppDataTable<TrialBalanceRow>(
                rows: data?.rows ?? const <TrialBalanceRow>[],
                rowKey: (TrialBalanceRow row) => row.accountId,
                isLoading: balance.isLoading,
                empty: const EmptyState(
                  title: 'Nothing posted yet',
                  description: 'Post an entry to see balances.',
                ),
                footer: data == null
                    ? null
                    : <Widget>[
                        const FooterCell('Total', numeric: false),
                        const SizedBox.shrink(),
                        FooterCell(
                          formatMoney(data.totalDebit, currency: currency),
                        ),
                        FooterCell(
                          formatMoney(data.totalCredit, currency: currency),
                        ),
                      ],
                columns: <AppColumn<TrialBalanceRow>>[
                  AppColumn<TrialBalanceRow>(
                    header: 'Account',
                    flex: 2,
                    cell: (TrialBalanceRow row) => Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(row.name),
                        if (_netsToNil(row))
                          Padding(
                            padding: const EdgeInsets.only(top: 4),
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              spacing: 6,
                              children: <Widget>[
                                Padding(
                                  padding: const EdgeInsets.only(top: 2),
                                  child: Icon(
                                    LucideIcons.undo2,
                                    size: 14,
                                    color: t.warning,
                                  ),
                                ),
                                Expanded(
                                  child: Text.rich(
                                    TextSpan(
                                      children: <InlineSpan>[
                                        TextSpan(
                                          text: formatMoney(
                                            row.grossDebit,
                                            currency: currency,
                                          ),
                                          style: const TextStyle(
                                            fontWeight: FontWeight.w600,
                                          ),
                                        ),
                                        const TextSpan(
                                          text:
                                              ' was posted here and then reversed, so '
                                              'it does not affect the balance.',
                                        ),
                                      ],
                                    ),
                                    style: TextStyle(
                                      fontSize: 13,
                                      color: t.warning,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                      ],
                    ),
                  ),
                  AppColumn<TrialBalanceRow>(
                    header: 'Dealt with',
                    hideOnNarrow: true,
                    cell: (TrialBalanceRow row) => _Parties(names: row.parties),
                  ),
                  AppColumn<TrialBalanceRow>(
                    header: 'Debit',
                    numeric: true,
                    cell: (TrialBalanceRow row) => isZeroMoney(row.debit)
                        ? Text('-', style: TextStyle(color: t.contentMuted))
                        : Text(formatMoney(row.debit, currency: currency)),
                  ),
                  AppColumn<TrialBalanceRow>(
                    header: 'Credit',
                    numeric: true,
                    cell: (TrialBalanceRow row) => isZeroMoney(row.credit)
                        ? Text('-', style: TextStyle(color: t.contentMuted))
                        : Text(formatMoney(row.credit, currency: currency)),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// The parties an account has dealt with.
///
/// One column, not a from/to pair. An account that both received from and paid the same
/// person showed that name in both columns, which reads as a contradiction even though it is
/// exactly what happened - because direction belongs to a transaction and this row is a
/// balance over many of them.
///
/// A dash means the entries behind this balance named nobody, which is the honest answer for
/// anything recorded before naming the party became required.
class _Parties extends StatelessWidget {
  const _Parties({required this.names});

  final List<String> names;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    if (names.isEmpty) {
      return Text('-', style: TextStyle(color: t.contentMuted));
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(
          names.first,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(fontSize: 12, color: t.content),
        ),
        if (names.length > 1)
          // Counted, with the names in the tooltip: a cell that grew with the number of
          // parties would set the row height for the whole table.
          Tooltip(
            message: names.skip(1).join(', '),
            child: Text(
              'and ${names.length - 1} more',
              style: TextStyle(fontSize: 11, color: t.contentMuted),
            ),
          ),
      ],
    );
  }
}

// =============================================================================
// Profit & loss
// =============================================================================
class _ProfitAndLossTab extends ConsumerStatefulWidget {
  const _ProfitAndLossTab();

  @override
  ConsumerState<_ProfitAndLossTab> createState() => _ProfitAndLossTabState();
}

class _ProfitAndLossTabState extends ConsumerState<_ProfitAndLossTab>
    with _RangeState {
  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final PeriodOptions? periods = ref.watch(periodOptionsProvider).valueOrNull;
    final DateRange range = resolvedRange(periods);
    final AsyncValue<ProfitAndLoss> report = ref.watch(
      profitAndLossProvider(range),
    );
    final ProfitAndLoss? data = report.valueOrNull;
    final String currency = localeSettings().currency;

    if (data == null) {
      return Column(
        spacing: 16,
        children: <Widget>[
          AppCard(
            child: CardHeader(
              title: 'Profit & loss',
              action: rangeSelector(periods),
            ),
          ),
          AppCard(
            padding: const EdgeInsets.all(20),
            child: const Skeleton(height: 240),
          ),
        ],
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      spacing: 16,
      children: <Widget>[
        TileGrid(
          maxColumns: 3,
          children: <Widget>[
            MetricTile(
              label: 'Revenue',
              value: formatMoney(data.totalIncome, currency: currency),
              icon: LucideIcons.trendingUp,
              valueSize: 20,
              uppercaseLabel: true,
            ),
            MetricTile(
              label: 'Gross profit',
              value: formatMoney(data.grossProfit, currency: currency),
              icon: LucideIcons.scale,
              valueSize: 20,
              uppercaseLabel: true,
            ),
            MetricTile(
              label: 'Net profit',
              value: formatMoney(data.netProfit, currency: currency),
              icon: LucideIcons.bookOpen,
              valueSize: 20,
              uppercaseLabel: true,
              valueTone: isNegativeMoney(data.netProfit) ? t.danger : t.success,
            ),
          ],
        ),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              CardHeader(
                title: 'Profit & loss',
                description:
                    '${formatDate(data.fromDate)} to ${formatDate(data.toDate)}',
                action: rangeSelector(periods),
              ),
              CardBody(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  spacing: 20,
                  children: <Widget>[
                    _ReportSection(
                      title: 'Income',
                      lines: data.income,
                      total: data.totalIncome,
                      currency: currency,
                    ),
                    _ReportSection(
                      title: 'Expenses',
                      lines: data.expenses,
                      total: data.totalExpenses,
                      currency: currency,
                    ),
                    Container(
                      padding: const EdgeInsets.only(top: 12),
                      decoration: BoxDecoration(
                        border: Border(top: BorderSide(color: t.border)),
                      ),
                      child: Row(
                        children: <Widget>[
                          Text(
                            'Net profit',
                            style: TextStyle(
                              fontSize: 14,
                              fontWeight: FontWeight.w600,
                              color: t.content,
                            ),
                          ),
                          const Spacer(),
                          Text(
                            formatMoney(data.netProfit, currency: currency),
                            style: TextStyle(
                              fontSize: 15,
                              fontWeight: FontWeight.w600,
                              color: t.content,
                              fontFeatures: tabularFigures,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

// =============================================================================
// Balance sheet
// =============================================================================
/// The balance sheet, for a chosen window, exportable to a file.
///
/// **A balance sheet is a position at a date, not a total over a period** - so the period
/// picker chooses *which date*, and what it buys you is the second column: the position the
/// day before the window opened. That pair is what shows movement, and it is how the
/// statement is presented on paper.
///
/// A [ConsumerStatefulWidget] rather than the plain [ConsumerWidget] it was, because the
/// chosen window is local screen state - nothing else in the app needs to know which quarter
/// somebody is looking at.
class _BalanceSheetTab extends ConsumerStatefulWidget {
  const _BalanceSheetTab();

  @override
  ConsumerState<_BalanceSheetTab> createState() => _BalanceSheetTabState();
}

class _BalanceSheetTabState extends ConsumerState<_BalanceSheetTab> {
  BalanceSheetQuery _query = const BalanceSheetQuery();

  /// Which export is in flight, so both buttons disable together and the right one says so.
  String? _saving;

  /// Save an export wherever the user asks for it.
  ///
  /// A real save dialog, not a chosen directory. Writing to Downloads and naming the path in
  /// a toast was the earlier behaviour, and it decided for the user: an accountant filing
  /// this alongside a client's other statements has somewhere specific in mind, and moving
  /// the file afterwards is work the dialog does for free. `FilePicker` is already a
  /// dependency here, so this costs nothing.
  ///
  /// **The report is fetched only after a location is chosen.** Asking first means cancelling
  /// the dialog does not leave a request in flight whose bytes are then thrown away.
  Future<void> _export(String format) async {
    final BalanceSheetView? view = ref
        .read(balanceSheetViewProvider(_query))
        .valueOrNull;
    final String suggested =
        'balance-sheet-${view?.sheet.asOf ?? 'export'}.$format';

    final String? path = await FilePicker.saveFile(
      dialogTitle: 'Save balance sheet',
      fileName: suggested,
      type: FileType.custom,
      allowedExtensions: <String>[format],
      // Keeps the dialog tied to the app window, so it cannot end up behind it.
      lockParentWindow: true,
    );
    // Null is a cancelled dialog, which is not an error and gets no toast: the user changed
    // their mind, and telling them so would be noise.
    if (path == null) return;

    setState(() => _saving = format);
    try {
      final List<int> data = await ref
          .read(accountingApiProvider)
          .exportBalanceSheet(
            format: format,
            period: _query.period,
            asOf: _query.period == StatementPeriod.custom ? _query.asOf : null,
            compareTo: _query.period == StatementPeriod.custom
                ? _query.compareTo
                : null,
          );

      // Some platforms return a path without the extension when the user edits the name;
      // appending it keeps the file openable by whatever opens .xlsx and .pdf.
      final String target = path.toLowerCase().endsWith('.$format')
          ? path
          : '$path.$format';
      await File(target).writeAsBytes(data);

      if (!mounted) return;
      context.toastSuccess('Saved ${p.basename(target)}', description: target);
    } catch (error) {
      if (mounted) {
        context.toastApiError(error, 'Could not export the $format file');
      }
    } finally {
      if (mounted) setState(() => _saving = null);
    }
  }

  Future<void> _pickDate({required bool isAsOf}) async {
    final DateTime? picked = await showDatePicker(
      context: context,
      initialDate: DateTime.now(),
      firstDate: DateTime(2000),
      lastDate: DateTime(2100),
      helpText: isAsOf ? 'Position as at' : 'Compare with',
    );
    if (picked == null) return;
    final String iso = picked.toIso8601String().split('T').first;
    setState(
      () => _query = isAsOf
          ? _query.copyWith(asOf: iso)
          : _query.copyWith(compareTo: iso),
    );
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AsyncValue<BalanceSheetView> async = ref.watch(
      balanceSheetViewProvider(_query),
    );
    final BalanceSheetView? view = async.valueOrNull;
    final BalanceSheet? data = view?.sheet;
    final BalanceSheet? prior = view?.comparative;
    final String currency = view?.currency ?? localeSettings().currency;
    final bool custom = _query.period == StatementPeriod.custom;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            title: 'Balance sheet',
            description: data == null
                ? 'Built from every posted entry'
                : prior == null
                ? 'As at ${formatDate(data.asOf)}'
                : 'As at ${formatDate(data.asOf)}, beside ${formatDate(prior.asOf)}',
            action: data == null
                ? null
                : AppBadge(
                    data.isBalanced ? 'Balanced' : 'Out of balance',
                    tone: data.isBalanced
                        ? BadgeTone.success
                        : BadgeTone.danger,
                    dot: true,
                  ),
          ),
          CardBody(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 16,
              children: <Widget>[
                Container(
                  padding: const EdgeInsets.only(bottom: 16),
                  decoration: BoxDecoration(
                    border: Border(bottom: BorderSide(color: t.border)),
                  ),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    spacing: 12,
                    children: <Widget>[
                      // The shared [Segmented] control, not a dropdown - the same one the
                      // report range above uses. Six mutually exclusive windows are worth
                      // showing at once: the choice is the point of the screen, and a closed
                      // menu hides five of them behind a click.
                      Segmented(
                        active: _query.period.wire,
                        onChanged: (String next) => setState(
                          () => _query = _query.copyWith(
                            period: StatementPeriod.parse(next),
                          ),
                        ),
                        segments: <(String, String, String?)>[
                          for (final StatementPeriod period
                              in StatementPeriod.values)
                            (period.wire, period.label, null),
                        ],
                      ),
                      // Only for a custom window. On a named one the server owns both
                      // dates, and offering them here would invite someone to set one and
                      // quietly override the period they picked.
                      if (custom) ...<Widget>[
                        AppButton(
                          onPressed: () => _pickDate(isAsOf: true),
                          variant: AppButtonVariant.secondary,
                          leftIcon: LucideIcons.calendar,
                          label: _query.asOf ?? 'As at',
                        ),
                        AppButton(
                          onPressed: () => _pickDate(isAsOf: false),
                          variant: AppButtonVariant.secondary,
                          leftIcon: LucideIcons.calendar,
                          label: _query.compareTo ?? 'Compare with',
                        ),
                      ],
                      const Spacer(),
                      AppButton(
                        onPressed: data == null || _saving != null
                            ? null
                            : () => _export('xlsx'),
                        variant: AppButtonVariant.secondary,
                        leftIcon: LucideIcons.sheet,
                        label: _saving == 'xlsx' ? 'Saving…' : 'Excel',
                      ),
                      AppButton(
                        onPressed: data == null || _saving != null
                            ? null
                            : () => _export('pdf'),
                        variant: AppButtonVariant.secondary,
                        leftIcon: LucideIcons.fileDown,
                        label: _saving == 'pdf' ? 'Saving…' : 'PDF',
                      ),
                    ],
                  ),
                ),

                if (data == null)
                  const Skeleton(height: 260)
                else ...<Widget>[
                  if (prior != null)
                    Row(
                      children: <Widget>[
                        const Spacer(),
                        for (final String label in <String>[
                          formatDate(data.asOf),
                          formatDate(prior.asOf),
                        ])
                          SizedBox(
                            width: 132,
                            child: Text(
                              label,
                              textAlign: TextAlign.right,
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.w600,
                                letterSpacing: 0.6,
                                color: t.contentMuted,
                              ),
                            ),
                          ),
                      ],
                    ),
                  _ReportSection(
                    title: 'Assets',
                    lines: data.assets,
                    total: data.totalAssets,
                    currency: currency,
                    prior: prior?.assets,
                    priorTotal: prior?.totalAssets,
                  ),
                  _ReportSection(
                    title: 'Liabilities',
                    lines: data.liabilities,
                    total: data.totalLiabilities,
                    currency: currency,
                    prior: prior?.liabilities,
                    priorTotal: prior?.totalLiabilities,
                  ),
                  _ReportSection(
                    title: 'Equity',
                    lines: data.equity,
                    total: data.totalEquity,
                    currency: currency,
                    prior: prior?.equity,
                    priorTotal: prior?.totalEquity,
                  ),
                  Container(
                    padding: const EdgeInsets.only(top: 12),
                    decoration: BoxDecoration(
                      border: Border(top: BorderSide(color: t.border)),
                    ),
                    child: Row(
                      children: <Widget>[
                        Text(
                          'Liabilities + equity',
                          style: TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w600,
                            color: t.content,
                          ),
                        ),
                        const Spacer(),
                        SizedBox(
                          width: 132,
                          child: Text(
                            // Displayed for the reader to check against total assets. The
                            // authoritative check is `isBalanced`, computed server-side.
                            formatMoney(
                              sumMoney(<String>[
                                data.totalLiabilities,
                                data.totalEquity,
                              ]),
                              currency: currency,
                            ),
                            textAlign: TextAlign.right,
                            style: TextStyle(
                              fontSize: 15,
                              fontWeight: FontWeight.w600,
                              color: t.content,
                              fontFeatures: tabularFigures,
                            ),
                          ),
                        ),
                        if (prior != null)
                          SizedBox(
                            width: 132,
                            child: Text(
                              formatMoney(
                                sumMoney(<String>[
                                  prior.totalLiabilities,
                                  prior.totalEquity,
                                ]),
                                currency: currency,
                              ),
                              textAlign: TextAlign.right,
                              style: TextStyle(
                                fontSize: 15,
                                fontWeight: FontWeight.w600,
                                color: t.contentMuted,
                                fontFeatures: tabularFigures,
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ReportSection extends StatelessWidget {
  const _ReportSection({
    required this.title,
    required this.lines,
    required this.total,
    required this.currency,
    this.prior,
    this.priorTotal,
  });

  final String title;
  final List<ReportLine> lines;
  final String total;
  final String currency;

  /// The same section at the comparison date, when one was asked for.
  final List<ReportLine>? prior;
  final String? priorTotal;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    // Matched by **label, never by position**: the two dates can hold different accounts -
    // one opened mid-period - so pairing by index would put an unrelated figure beside a row
    // and print a confident wrong number. A row with no counterpart shows a dash.
    final Map<String, String> before = <String, String>{
      for (final ReportLine line in prior ?? const <ReportLine>[])
        line.label: line.amount,
    };
    final bool comparing = priorTotal != null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(
          title.toUpperCase(),
          style: TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.8,
            color: t.contentMuted,
          ),
        ),
        const SizedBox(height: 6),
        if (lines.isEmpty)
          Text(
            'Nothing to report',
            style: TextStyle(fontSize: 13, color: t.contentMuted),
          )
        else
          for (final ReportLine line in lines)
            Padding(
              padding: EdgeInsets.only(
                left: (line.level - 1) * 12.0,
                bottom: 4,
              ),
              child: Row(
                children: <Widget>[
                  if (line.accountCode != null) ...<Widget>[
                    Text(
                      line.accountCode!,
                      style: monoStyle(fontSize: 11, color: t.contentMuted),
                    ),
                    const SizedBox(width: 8),
                  ],
                  Expanded(
                    child: Text(
                      line.label,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 13, color: t.contentSecondary),
                    ),
                  ),
                  SizedBox(
                    width: 132,
                    child: Text(
                      formatMoney(line.amount, currency: currency),
                      textAlign: TextAlign.right,
                      style: TextStyle(
                        fontSize: 13,
                        color: t.content,
                        fontFeatures: tabularFigures,
                      ),
                    ),
                  ),
                  if (comparing)
                    SizedBox(
                      width: 132,
                      child: Text(
                        // A dash, not a zero: the account did not exist at the earlier
                        // date, and "0.00" would assert a balance never recorded.
                        before.containsKey(line.label)
                            ? formatMoney(
                                before[line.label],
                                currency: currency,
                              )
                            : '-',
                        textAlign: TextAlign.right,
                        style: TextStyle(
                          fontSize: 13,
                          color: t.contentMuted,
                          fontFeatures: tabularFigures,
                        ),
                      ),
                    ),
                ],
              ),
            ),
        Container(
          margin: const EdgeInsets.only(top: 6),
          padding: const EdgeInsets.only(top: 6),
          decoration: BoxDecoration(
            border: Border(top: BorderSide(color: t.border.at(0.6))),
          ),
          child: Row(
            children: <Widget>[
              Text(
                'Total ${title.toLowerCase()}',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w500,
                  color: t.content,
                ),
              ),
              const Spacer(),
              Text(
                formatMoney(total, currency: currency),
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w500,
                  color: t.content,
                  fontFeatures: tabularFigures,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
