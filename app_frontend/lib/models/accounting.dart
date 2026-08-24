import 'json.dart';

/// Accounting contracts - chart of accounts, journals, and the statements.
///
/// **Money is a `String` in every type here, never a `double`.** The backend
/// serialises `Decimal` as a decimal string because a JSON number is an IEEE-754
/// double; typing these as `double` would let a float conversion in at the very
/// edge of the system. Format with `formatMoney`, compare with `compareMoney`.
enum AccountType {
  asset,
  liability,
  equity,
  income,
  expense;

  static AccountType parse(String value) => AccountType.values.firstWhere(
    (AccountType type) => type.name == value,
    orElse: () => AccountType.asset,
  );

  String get label => '${name[0].toUpperCase()}${name.substring(1)}';
}

class Account {
  const Account({
    required this.id,
    required this.code,
    required this.name,
    required this.accountType,
    required this.isGroup,
    required this.isActive,
    required this.balance,
    required this.totalDebit,
    required this.totalCredit,
  });

  final String id;
  final String code;
  final String name;
  final AccountType accountType;
  final bool isGroup;
  final bool isActive;
  final String balance;
  final String totalDebit;
  final String totalCredit;

  factory Account.fromJson(Json json) => Account(
    id: str(json, 'id'),
    code: str(json, 'code'),
    name: str(json, 'name'),
    accountType: AccountType.parse(str(json, 'account_type')),
    isGroup: boolOf(json, 'is_group'),
    isActive: boolOf(json, 'is_active', true),
    balance: money(json, 'balance'),
    totalDebit: money(json, 'total_debit'),
    totalCredit: money(json, 'total_credit'),
  );
}

class JournalEntryLine {
  const JournalEntryLine({
    required this.id,
    required this.accountCode,
    required this.accountName,
    required this.debit,
    required this.credit,
    this.description,
  });

  final String id;
  final String accountCode;
  final String accountName;
  final String debit;
  final String credit;
  final String? description;

  factory JournalEntryLine.fromJson(Json json) => JournalEntryLine(
    id: str(json, 'id'),
    accountCode: str(json, 'account_code'),
    accountName: str(json, 'account_name'),
    debit: money(json, 'debit'),
    credit: money(json, 'credit'),
    description: strOrNull(json, 'description'),
  );
}

class JournalEntry {
  const JournalEntry({
    required this.id,
    this.entryNumber,
    required this.entryDate,
    required this.narration,
    this.reference,
    this.counterparty,
    required this.status,
    required this.journalCode,
    required this.totalDebit,
    required this.totalCredit,
    this.cashDirection,
    required this.cashAmount,
    this.reversesId,
    required this.lines,
  });

  final String id;
  final String? entryNumber;
  final String entryDate;
  final String narration;
  final String? reference;

  /// Who the money came from or went to. Null on entries made before it was
  /// required.
  final String? counterparty;

  /// `draft`, `posted`, or `reversed`.
  final String status;
  final String journalCode;
  final String totalDebit;
  final String totalCredit;

  /// Whether cash actually moved, and which way.
  ///
  /// An entry always has both a debit and a credit, so "debited or credited" has no
  /// single answer - the useful question is whether money came in or went out,
  /// decided by which side the cash account sits on. Null when no cash was
  /// involved, or when the entry only moved money between your own accounts.
  final String? cashDirection;
  final String cashAmount;

  /// Set when this entry cancels an earlier one.
  final String? reversesId;
  final List<JournalEntryLine> lines;

  bool get isReversed => status == 'reversed';

  factory JournalEntry.fromJson(Json json) => JournalEntry(
    id: str(json, 'id'),
    entryNumber: strOrNull(json, 'entry_number'),
    entryDate: str(json, 'entry_date'),
    narration: str(json, 'narration'),
    reference: strOrNull(json, 'reference'),
    counterparty: strOrNull(json, 'counterparty'),
    status: strOrNull(json, 'status') ?? 'draft',
    journalCode: strOrNull(json, 'journal_code') ?? '',
    totalDebit: money(json, 'total_debit'),
    totalCredit: money(json, 'total_credit'),
    cashDirection: strOrNull(json, 'cash_direction'),
    cashAmount: money(json, 'cash_amount'),
    reversesId: strOrNull(json, 'reverses_id'),
    lines: listOf(json, 'lines', JournalEntryLine.fromJson),
  );
}

class TrialBalanceRow {
  const TrialBalanceRow({
    required this.accountId,
    required this.code,
    required this.name,
    required this.accountType,
    required this.debit,
    required this.credit,
    required this.grossDebit,
    required this.grossCredit,
    required this.parties,
  });

  final String accountId;
  final String code;
  final String name;
  final AccountType accountType;
  final String debit;
  final String credit;

  /// Movement through the account before netting. Non-zero with a nil net means
  /// the activity cancelled out - usually a reversal.
  final String grossDebit;
  final String grossCredit;

  /// The parties this account has dealt with, as typed into Billing's From/To
  /// field. Empty when its entries named nobody. Not split by direction - that
  /// belongs to a transaction, not a balance.
  final List<String> parties;

  factory TrialBalanceRow.fromJson(Json json) => TrialBalanceRow(
    accountId: str(json, 'account_id'),
    code: str(json, 'code'),
    name: str(json, 'name'),
    accountType: AccountType.parse(str(json, 'account_type')),
    debit: money(json, 'debit'),
    credit: money(json, 'credit'),
    grossDebit: money(json, 'gross_debit'),
    grossCredit: money(json, 'gross_credit'),
    parties: stringList(json, 'parties'),
  );
}

class TrialBalance {
  const TrialBalance({
    required this.asOf,
    required this.rows,
    required this.totalDebit,
    required this.totalCredit,
    required this.isBalanced,
    required this.reversedEntryCount,
  });

  final String asOf;
  final List<TrialBalanceRow> rows;
  final String totalDebit;
  final String totalCredit;
  final bool isBalanced;

  /// Entries cancelled by a reversal in this window. A reversal leaves no trace in
  /// the net figures, so without this the report cannot be reconciled against the
  /// journal.
  final int reversedEntryCount;

  factory TrialBalance.fromJson(Json json) => TrialBalance(
    asOf: str(json, 'as_of'),
    rows: listOf(json, 'rows', TrialBalanceRow.fromJson),
    totalDebit: money(json, 'total_debit'),
    totalCredit: money(json, 'total_credit'),
    isBalanced: boolOf(json, 'is_balanced'),
    reversedEntryCount: intOf(json, 'reversed_entry_count'),
  );
}

class ReportLine {
  const ReportLine({
    required this.label,
    required this.amount,
    required this.level,
    required this.isTotal,
    this.accountCode,
  });

  final String label;
  final String amount;
  final int level;
  final bool isTotal;
  final String? accountCode;

  factory ReportLine.fromJson(Json json) => ReportLine(
    label: str(json, 'label'),
    amount: money(json, 'amount'),
    level: intOf(json, 'level', 1),
    isTotal: boolOf(json, 'is_total'),
    accountCode: strOrNull(json, 'account_code'),
  );
}

class ProfitAndLoss {
  const ProfitAndLoss({
    required this.fromDate,
    required this.toDate,
    required this.income,
    required this.expenses,
    required this.totalIncome,
    required this.totalExpenses,
    required this.costOfGoodsSold,
    required this.grossProfit,
    required this.netProfit,
  });

  final String fromDate;
  final String toDate;
  final List<ReportLine> income;
  final List<ReportLine> expenses;
  final String totalIncome;
  final String totalExpenses;
  final String costOfGoodsSold;
  final String grossProfit;
  final String netProfit;

  factory ProfitAndLoss.fromJson(Json json) => ProfitAndLoss(
    fromDate: str(json, 'from_date'),
    toDate: str(json, 'to_date'),
    income: listOf(json, 'income', ReportLine.fromJson),
    expenses: listOf(json, 'expenses', ReportLine.fromJson),
    totalIncome: money(json, 'total_income'),
    totalExpenses: money(json, 'total_expenses'),
    costOfGoodsSold: money(json, 'cost_of_goods_sold'),
    grossProfit: money(json, 'gross_profit'),
    netProfit: money(json, 'net_profit'),
  );
}

/// The named windows a balance sheet can be asked for.
///
/// **A balance sheet is a position at a date, not a total over a window**, so a period only
/// decides *which date* - the last day of it. What the period actually buys you is the
/// comparative: the position the day before it opened. See the backend's
/// `statement_periods.py`.
///
/// Resolved server-side, which is why these carry no date arithmetic. An organization's year
/// may start in April, and this app working out "this quarter" for itself would diverge from
/// the web client with nothing failing.
enum StatementPeriod {
  toDate('to_date', 'As things stand'),
  thisQuarter('this_quarter', 'This quarter'),
  lastQuarter('last_quarter', 'Last quarter'),
  thisFiscalYear('this_fiscal_year', 'This financial year'),
  lastFiscalYear('last_fiscal_year', 'Last financial year'),
  custom('custom', 'Custom dates');

  const StatementPeriod(this.wire, this.label);

  final String wire;
  final String label;

  static StatementPeriod parse(String? value) =>
      StatementPeriod.values.firstWhere(
        (StatementPeriod p) => p.wire == value,
        orElse: () => StatementPeriod.toDate,
      );
}

/// A balance sheet, and optionally the position it opened from.
///
/// [comparative] is a whole sheet rather than a second amount per line, because the two dates
/// can hold different accounts - one opened mid-period - and a per-line pair would have
/// nowhere to put a row that exists on only one side.
class BalanceSheetView {
  const BalanceSheetView({
    required this.period,
    required this.periodLabel,
    required this.sheet,
    required this.currency,
    this.comparative,
  });

  final StatementPeriod period;

  /// What to call the window on screen, decided server-side so both clients agree.
  final String periodLabel;
  final BalanceSheet sheet;
  final BalanceSheet? comparative;
  final String currency;

  factory BalanceSheetView.fromJson(Json json) => BalanceSheetView(
    period: StatementPeriod.parse(strOrNull(json, 'period')),
    periodLabel: strOrNull(json, 'period_label') ?? '',
    sheet: BalanceSheet.fromJson(mapOf(json, 'sheet')),
    comparative: json['comparative'] is Map<String, dynamic>
        ? BalanceSheet.fromJson(json['comparative'] as Json)
        : null,
    currency: strOrNull(json, 'currency') ?? 'INR',
  );
}

class BalanceSheet {
  const BalanceSheet({
    required this.asOf,
    required this.assets,
    required this.liabilities,
    required this.equity,
    required this.totalAssets,
    required this.totalLiabilities,
    required this.totalEquity,
    required this.isBalanced,
  });

  final String asOf;
  final List<ReportLine> assets;
  final List<ReportLine> liabilities;
  final List<ReportLine> equity;
  final String totalAssets;
  final String totalLiabilities;
  final String totalEquity;
  final bool isBalanced;

  factory BalanceSheet.fromJson(Json json) => BalanceSheet(
    asOf: str(json, 'as_of'),
    assets: listOf(json, 'assets', ReportLine.fromJson),
    liabilities: listOf(json, 'liabilities', ReportLine.fromJson),
    equity: listOf(json, 'equity', ReportLine.fromJson),
    totalAssets: money(json, 'total_assets'),
    totalLiabilities: money(json, 'total_liabilities'),
    totalEquity: money(json, 'total_equity'),
    isBalanced: boolOf(json, 'is_balanced'),
  );
}

/// A `from_date`/`to_date` pair, as the report endpoints take it.
class DateRange {
  const DateRange({required this.fromDate, required this.toDate});

  final String fromDate;
  final String toDate;

  Map<String, dynamic> get query => <String, dynamic>{
    'from_date': fromDate,
    'to_date': toDate,
  };

  @override
  bool operator ==(Object other) =>
      other is DateRange &&
      other.fromDate == fromDate &&
      other.toDate == toDate;

  @override
  int get hashCode => Object.hash(fromDate, toDate);

  @override
  String toString() => '$fromDate..$toDate';
}
