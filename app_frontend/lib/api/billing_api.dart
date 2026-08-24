import '../core/api_client.dart';
import '../models/billing.dart';
import '../models/json.dart';
import '../models/page.dart';

/// Billing bindings - money in and money out.
///
/// Amounts are `String`, as everywhere else: these figures post to the ledger.
class BillingApi {
  const BillingApi(this._client);

  final ApiClient _client;

  Future<BillingOptions> options() async =>
      BillingOptions.fromJson(await _client.get<Json>('/billing/options'));

  Future<Paged<BillingEntry>> list({
    int page = 1,
    int pageSize = 25,
    Direction? direction,
    String? query,
  }) async => Paged<BillingEntry>.fromJson(
    await _client.get<Json>(
      '/billing',
      query: <String, dynamic>{
        'page': page,
        'page_size': pageSize,
        'direction': direction?.wire,
        'q': query,
      },
    ),
    BillingEntry.fromJson,
  );

  Future<BillingSummary> summary() async =>
      BillingSummary.fromJson(await _client.get<Json>('/billing/summary'));

  Future<BillingEntry> record({
    required Direction direction,
    required String amount,
    required String description,
    required String party,
    String? entryDate,
    String? categoryId,
    String? moneyAccountId,
    String? reference,
  }) async => BillingEntry.fromJson(
    await _client.post<Json>(
      '/billing',
      body: <String, dynamic>{
        'direction': direction.wire,
        'amount': amount,
        'description': description,
        // Required - the API rejects a blank.
        'party': party,
        'entry_date': ?entryDate,
        if (categoryId != null && categoryId.isNotEmpty)
          'category_id': categoryId,
        if (moneyAccountId != null && moneyAccountId.isNotEmpty)
          'money_account_id': moneyAccountId,
        if (reference != null && reference.isNotEmpty) 'reference': reference,
      },
    ),
  );

  /// Add a category from a name alone.
  ///
  /// The account code, parent group, and subtype are derived server-side - nobody
  /// should need to understand the chart of accounts to file a payment under a name
  /// the built-in list does not have.
  Future<Category> createCategory(String name, Direction direction) async =>
      Category.fromJson(
        await _client.post<Json>(
          '/billing/categories',
          body: <String, dynamic>{'name': name, 'direction': direction.wire},
        ),
      );

  /// Add a place money can sit - a second bank, a UPI wallet, a partner's petty
  /// cash. The seeded chart only has one till and one current account.
  ///
  /// The bank fields are optional and are **ignored for a cash account**, which has
  /// no bank, no number and no holder.
  Future<MoneyAccount> createMoneyAccount(
    String name,
    MoneyKind kind, {
    String? bankName,
    String? holderName,
    String? accountNumber,
  }) async => MoneyAccount.fromJson(
    await _client.post<Json>(
      '/billing/money-accounts',
      body: <String, dynamic>{
        'name': name,
        'kind': kind.wire,
        if (bankName != null && bankName.isNotEmpty) 'bank_name': bankName,
        if (holderName != null && holderName.isNotEmpty)
          'holder_name': holderName,
        if (accountNumber != null && accountNumber.isNotEmpty)
          'account_number': accountNumber,
      },
    ),
  );

  /// Everywhere money can sit, optionally including archived accounts.
  ///
  /// Separate from [options] because that payload feeds the recording form's pickers and
  /// must never carry an archived account - a picker offering a closed bank account is a
  /// picker that posts to it.
  Future<List<MoneyAccount>> moneyAccounts({
    bool includeArchived = false,
  }) async {
    final List<dynamic> rows = await _client.get<List<dynamic>>(
      '/billing/money-accounts',
      query: <String, dynamic>{if (includeArchived) 'include_archived': true},
    );
    return rows.cast<Json>().map(MoneyAccount.fromJson).toList(growable: false);
  }

  /// Stop offering an account without deleting it - entries still name it.
  ///
  /// Refused for a seeded account, which later modules post to by role. Check
  /// [MoneyAccount.canArchive] before offering the control.
  Future<MoneyAccount> archiveMoneyAccount(String id) async =>
      MoneyAccount.fromJson(
        await _client.post<Json>('/billing/money-accounts/$id/archive'),
      );

  Future<MoneyAccount> restoreMoneyAccount(String id) async =>
      MoneyAccount.fromJson(
        await _client.post<Json>('/billing/money-accounts/$id/restore'),
      );

  /// One account's details, **with the account number in full.**
  ///
  /// A separate request from [options] on purpose: decrypting an account number is a
  /// deliberate act with its own permission check, rather than something that rides
  /// along on every load of the billing screen for every account.
  Future<BankDetails> bankDetails(String accountId) async =>
      BankDetails.fromJson(
        await _client.get<Json>('/billing/money-accounts/$accountId/details'),
      );

  /// Set which bank an account is at, whose it is, and its number.
  ///
  /// A `PUT` that replaces the whole set, so passing null or empty **clears** a field -
  /// which is how a number entered by mistake is removed. This is also the only way the
  /// seeded "Primary Bank Account" ever gets its details, since the chart template
  /// creates it before anyone has said which bank it is.
  ///
  /// An empty value is **omitted rather than sent as `""`**. Both mean "cleared" to a
  /// `PUT`, but the account number has a minimum length, so an empty string would be
  /// rejected as too short - turning "remove this number" into a validation error.
  Future<BankDetails> saveBankDetails(
    String accountId, {
    String? name,
    String? bankName,
    String? holderName,
    String? accountNumber,
  }) async => BankDetails.fromJson(
    await _client.put<Json>(
      '/billing/money-accounts/$accountId/details',
      body: <String, dynamic>{
        // Omitted when blank, which the server reads as "leave the name alone" - a blank
        // name is not a rename, and an account has to be called something.
        if (name != null && name.isNotEmpty) 'name': name,
        if (bankName != null && bankName.isNotEmpty) 'bank_name': bankName,
        if (holderName != null && holderName.isNotEmpty)
          'holder_name': holderName,
        if (accountNumber != null && accountNumber.isNotEmpty)
          'account_number': accountNumber,
      },
    ),
  );

  Future<List<PaymentCard>> cards({bool includeArchived = false}) async {
    final List<dynamic> rows = await _client.get<List<dynamic>>(
      '/billing/cards',
      query: <String, dynamic>{if (includeArchived) 'include_archived': true},
    );
    return rows.cast<Json>().map(PaymentCard.fromJson).toList(growable: false);
  }

  /// Register a card from its number.
  ///
  /// **The number goes up once and is never stored** - not by the server, and not
  /// here. [PaymentCard] has no field for it, so there is nowhere for it to end up
  /// in app state; only the network and the last four digits come back. The caller
  /// is expected to clear whatever field it was typed into as soon as this
  /// returns.
  Future<PaymentCard> addCard({
    required String label,
    required CardKind kind,
    required String cardNumber,
    String? holderName,
    String? bankAccountId,
  }) async => PaymentCard.fromJson(
    await _client.post<Json>(
      '/billing/cards',
      body: <String, dynamic>{
        'label': label,
        'kind': kind.wire,
        'card_number': cardNumber,
        if (holderName != null && holderName.isNotEmpty)
          'holder_name': holderName,
        // Required for a debit card, ignored for a credit card: a debit card is a
        // way of using an account you already have, so it names that account
        // instead of creating one that would double-count the same money.
        if (kind == CardKind.debit &&
            bankAccountId != null &&
            bankAccountId.isNotEmpty)
          'bank_account_id': bankAccountId,
      },
    ),
  );

  /// Correct a card's name, its holder, or its number.
  ///
  /// A `PATCH`, so an omitted field is left alone. **The kind is not editable** - a credit
  /// card owns a liability account and a debit card points at a bank account, so switching
  /// would either orphan an account with postings or start filing card spending as money
  /// leaving a bank account that never lost it.
  ///
  /// A corrected number is read and discarded exactly as on create, and can change the
  /// derived network as well as the last four digits.
  Future<PaymentCard> updateCard(
    String id, {
    String? label,
    String? holderName,
    String? cardNumber,
  }) async => PaymentCard.fromJson(
    await _client.patch<Json>(
      '/billing/cards/$id',
      body: <String, dynamic>{
        'label': ?label,
        // Sent even when empty: that is how the holder's name gets cleared. Omitting it
        // would mean "leave it alone", which is a different instruction.
        'holder_name': ?holderName,
        if (cardNumber != null && cardNumber.isNotEmpty)
          'card_number': cardNumber,
      },
    ),
  );

  /// Remove a card entirely.
  ///
  /// **Refused once anything has been recorded on it** - archive it instead, because an
  /// entry names the card it was made on. [PaymentCard.canDelete] says which case applies.
  Future<void> deleteCard(String id) async =>
      _client.delete<void>('/billing/cards/$id');

  /// Remove an account entirely.
  ///
  /// Refused if it has postings, is seeded, or has a card drawing on it -
  /// [MoneyAccount.canDelete] covers all three.
  Future<void> deleteMoneyAccount(String id) async =>
      _client.delete<void>('/billing/money-accounts/$id');

  /// Stop offering a card without deleting it - past entries still name it.
  Future<PaymentCard> archiveCard(String id) async => PaymentCard.fromJson(
    await _client.post<Json>('/billing/cards/$id/archive'),
  );

  Future<PaymentCard> restoreCard(String id) async => PaymentCard.fromJson(
    await _client.post<Json>('/billing/cards/$id/restore'),
  );

  /// Move money between two of your own accounts.
  ///
  /// No category, and that is not an omission: moving your own money is neither
  /// earning nor spending it, so there is no income or expense line for it to go
  /// against. It is tagged so the money-in and money-out totals ignore it -
  /// counting a transfer would show income that never arrived from anywhere.
  Future<Transfer> transfer({
    required String fromAccountId,
    required String toAccountId,
    required String amount,
    String? entryDate,
    String? description,
  }) async => Transfer.fromJson(
    await _client.post<Json>(
      '/billing/transfers',
      body: <String, dynamic>{
        'from_account_id': fromAccountId,
        'to_account_id': toAccountId,
        'amount': amount,
        'entry_date': ?entryDate,
        if (description != null && description.isNotEmpty)
          'description': description,
      },
    ),
  );

  /// Cancel an entry by posting its mirror image.
  ///
  /// There is no delete and no edit - a posted ledger entry is immutable, so an
  /// opposite entry is the only honest undo.
  Future<BillingEntry> reverse(String id, {String? reason}) async =>
      BillingEntry.fromJson(
        await _client.post<Json>(
          '/billing/$id/reverse',
          body: <String, dynamic>{
            if (reason != null && reason.isNotEmpty) 'reason': reason,
          },
        ),
      );
}
