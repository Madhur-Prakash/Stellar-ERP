import '../core/api_client.dart';
import '../models/accounting.dart';
import '../models/json.dart';
import '../models/page.dart';

/// Accounting bindings - chart of accounts, journal entries, and the statements.
class AccountingApi {
  const AccountingApi(this._client);

  final ApiClient _client;

  /// `asOf` reports balances at a past date, so a chart can match a filtered
  /// report.
  Future<List<Account>> accounts({String? asOf, bool? postableOnly}) async {
    final List<dynamic> raw = await _client.get<List<dynamic>>(
      '/accounts',
      query: <String, dynamic>{'as_of': asOf, 'postable_only': postableOnly},
    );
    return raw.cast<Json>().map(Account.fromJson).toList(growable: false);
  }

  Future<Paged<JournalEntry>> entries({
    int page = 1,
    int pageSize = 25,
    String? status,
  }) async => Paged<JournalEntry>.fromJson(
    await _client.get<Json>(
      '/journal-entries',
      query: <String, dynamic>{
        'page': page,
        'page_size': pageSize,
        'status': status,
      },
    ),
    JournalEntry.fromJson,
  );

  Future<JournalEntry> reverseEntry(String id, {String? narration}) async =>
      JournalEntry.fromJson(
        await _client.post<Json>(
          '/journal-entries/$id/reverse',
          body: <String, dynamic>{'narration': ?narration},
        ),
      );

  Future<TrialBalance> trialBalance({String? asOf}) async =>
      TrialBalance.fromJson(
        await _client.get<Json>(
          '/reports/trial-balance',
          query: <String, dynamic>{'as_of': asOf},
        ),
      );

  Future<ProfitAndLoss> profitAndLoss(DateRange range) async =>
      ProfitAndLoss.fromJson(
        await _client.get<Json>('/reports/profit-and-loss', query: range.query),
      );

  /// The balance sheet for a window, with the position it opened from.
  ///
  /// Dates are only sent for [StatementPeriod.custom]: on a named period the server owns
  /// both, and passing a half-filled pair would silently override the period asked for.
  Future<BalanceSheetView> balanceSheetView({
    StatementPeriod period = StatementPeriod.thisFiscalYear,
    String? asOf,
    String? compareTo,
    bool comparative = true,
  }) async => BalanceSheetView.fromJson(
    await _client.get<Json>(
      '/reports/balance-sheet/view',
      query: <String, dynamic>{
        'period': period.wire,
        'as_of': ?asOf,
        'compare_to': ?compareTo,
        'comparative': comparative,
      },
    ),
  );

  /// The same statement as a spreadsheet or a PDF, as raw bytes.
  ///
  /// Built from the same call that renders the screen, so a saved file cannot disagree with
  /// what was on display when the button was pressed.
  Future<List<int>> exportBalanceSheet({
    required String format,
    StatementPeriod period = StatementPeriod.thisFiscalYear,
    String? asOf,
    String? compareTo,
    bool comparative = true,
  }) => _client.bytes(
    '/reports/balance-sheet/export',
    query: <String, dynamic>{
      'format': format,
      'period': period.wire,
      'as_of': ?asOf,
      'compare_to': ?compareTo,
      'comparative': comparative,
    },
  );

  Future<BalanceSheet> balanceSheet({String? asOf}) async =>
      BalanceSheet.fromJson(
        await _client.get<Json>(
          '/reports/balance-sheet',
          query: <String, dynamic>{'as_of': asOf},
        ),
      );
}
