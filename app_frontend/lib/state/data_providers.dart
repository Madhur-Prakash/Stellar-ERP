import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/accounting.dart';
import '../models/analytics.dart';
import '../models/auth.dart';
import '../models/billing.dart';
import '../models/documents.dart';
import '../models/inventory.dart';
import '../models/organization.dart';
import '../models/page.dart';
import '../models/sales.dart';
import '../models/trust.dart';
import 'providers.dart';

/// Every query in the app, in one place.
///
/// These are the `useQuery` calls of the web app, and the naming follows its query
/// keys so the two can be compared line by line. Three conventions hold throughout:
///
/// * **[bindCache] first.** It enrols the provider in the sign-out and
///   organization-switch teardown; a provider that forgets it would keep serving the
///   previous user's figures.
/// * **[retrying] around the request.** Same policy as the web app's `QueryClient`:
///   two more attempts for something transient, none at all for a 4xx.
/// * **`autoDispose` by default**, which is the closest thing to a `gcTime`: a screen
///   that is no longer watching a query stops holding its result. The exceptions are
///   marked, and they are the ones the web app pins with an infinite `staleTime` -
///   fiscal settings and the permission catalogue change at deploy time, not at
///   runtime.
///
/// Refetching on window focus is deliberately absent, as it is on the web: it is
/// jarring in a data-entry app, where the user alt-tabs to a spreadsheet constantly.

// =============================================================================
// Analytics - shared by the dashboard, the analytics screen, and the footer
// =============================================================================
/// Fiscal settings and the server's own today. Never refetched.
final FutureProvider<PeriodOptions> periodOptionsProvider =
    FutureProvider<PeriodOptions>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(analyticsApiProvider).periods());
    });

final AutoDisposeFutureProviderFamily<Dashboard, Period> dashboardProvider =
    FutureProvider.autoDispose.family<Dashboard, Period>((
      Ref ref,
      Period period,
    ) {
      bindCache(ref);
      return retrying(() => ref.read(analyticsApiProvider).dashboard(period));
    });

final AutoDisposeFutureProviderFamily<Trend, Period> trendProvider =
    FutureProvider.autoDispose.family<Trend, Period>((Ref ref, Period period) {
      bindCache(ref);
      return retrying(() => ref.read(analyticsApiProvider).trend(period));
    });

/// The trend over an explicit window, so a chart can match a filtered report.
final AutoDisposeFutureProviderFamily<Trend, DateRange> trendForRangeProvider =
    FutureProvider.autoDispose.family<Trend, DateRange>((
      Ref ref,
      DateRange range,
    ) {
      bindCache(ref);
      return retrying(
        () => ref.read(analyticsApiProvider).trend(Period.last12Months, range),
      );
    });

final AutoDisposeFutureProviderFamily<Ranking, Period> topCustomersProvider =
    FutureProvider.autoDispose.family<Ranking, Period>((
      Ref ref,
      Period period,
    ) {
      bindCache(ref);
      return retrying(
        () => ref.read(analyticsApiProvider).topCustomers(period),
      );
    });

final AutoDisposeFutureProviderFamily<Ranking, Period> topProductsProvider =
    FutureProvider.autoDispose.family<Ranking, Period>((
      Ref ref,
      Period period,
    ) {
      bindCache(ref);
      return retrying(() => ref.read(analyticsApiProvider).topProducts(period));
    });

/// Not auto-disposed: the footer watches this on every screen, and letting it drop
/// between routes would refetch it on every navigation.
final FutureProvider<ControlChecks> controlChecksProvider =
    FutureProvider<ControlChecks>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(analyticsApiProvider).controlChecks());
    });

// =============================================================================
// Organizations, members, roles, audit
// =============================================================================
final AutoDisposeFutureProvider<List<OrganizationListItem>>
organizationsProvider = FutureProvider.autoDispose<List<OrganizationListItem>>((
  Ref ref,
) {
  bindCache(ref);
  return retrying(() => ref.read(organizationsApiProvider).list());
});

final AutoDisposeFutureProvider<Organization> currentOrganizationProvider =
    FutureProvider.autoDispose<Organization>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(organizationsApiProvider).current());
    });

final AutoDisposeFutureProvider<List<Member>> membersProvider =
    FutureProvider.autoDispose<List<Member>>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(organizationsApiProvider).listMembers());
    });

final AutoDisposeFutureProvider<List<Invitation>> invitationsProvider =
    FutureProvider.autoDispose<List<Invitation>>((Ref ref) {
      bindCache(ref);
      return retrying(
        () => ref.read(organizationsApiProvider).listInvitations(),
      );
    });

final AutoDisposeFutureProvider<List<Role>> rolesProvider =
    FutureProvider.autoDispose<List<Role>>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(organizationsApiProvider).listRoles());
    });

/// The catalogue only changes on deploy, so it is not auto-disposed.
final FutureProvider<PermissionCatalogue> permissionCatalogueProvider =
    FutureProvider<PermissionCatalogue>((Ref ref) {
      bindCache(ref);
      return retrying(
        () => ref.read(organizationsApiProvider).permissionCatalogue(),
      );
    });

final FutureProvider<List<String>> auditActionsProvider =
    FutureProvider<List<String>>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(organizationsApiProvider).auditActions());
    });

/// The audit trail, cursor-paginated.
///
/// A notifier rather than a `FutureProvider` because the pages accumulate: the screen
/// shows everything loaded so far and appends on "Load more", which a provider that
/// recomputes from scratch cannot express.
class AuditFilter {
  const AuditFilter({this.action, this.severity});

  final String? action;
  final String? severity;

  @override
  bool operator ==(Object other) =>
      other is AuditFilter &&
      other.action == action &&
      other.severity == severity;

  @override
  int get hashCode => Object.hash(action, severity);
}

class AuditFeed {
  const AuditFeed({
    required this.entries,
    required this.hasMore,
    required this.isLoadingMore,
  });

  final List<AuditEntry> entries;
  final bool hasMore;
  final bool isLoadingMore;
}

class AuditNotifier extends StateNotifier<AsyncValue<AuditFeed>> {
  AuditNotifier(this._ref, this._filter)
    : super(const AsyncValue<AuditFeed>.loading()) {
    _load();
  }

  final Ref _ref;
  final AuditFilter _filter;
  String? _cursor;

  Future<void> _load() async {
    try {
      final CursorPage<AuditEntry> page = await retrying(
        () => _ref
            .read(organizationsApiProvider)
            .listAudit(action: _filter.action, severity: _filter.severity),
      );
      _cursor = page.nextCursor;
      state = AsyncValue<AuditFeed>.data(
        AuditFeed(
          entries: page.items,
          hasMore: page.nextCursor != null,
          isLoadingMore: false,
        ),
      );
    } catch (error, stack) {
      state = AsyncValue<AuditFeed>.error(error, stack);
    }
  }

  Future<void> loadMore() async {
    final AuditFeed? current = state.valueOrNull;
    if (current == null || !current.hasMore || current.isLoadingMore) return;

    state = AsyncValue<AuditFeed>.data(
      AuditFeed(
        entries: current.entries,
        hasMore: current.hasMore,
        isLoadingMore: true,
      ),
    );

    try {
      final CursorPage<AuditEntry> page = await retrying(
        () => _ref
            .read(organizationsApiProvider)
            .listAudit(
              cursor: _cursor,
              action: _filter.action,
              severity: _filter.severity,
            ),
      );
      _cursor = page.nextCursor;
      state = AsyncValue<AuditFeed>.data(
        AuditFeed(
          entries: <AuditEntry>[...current.entries, ...page.items],
          hasMore: page.nextCursor != null,
          isLoadingMore: false,
        ),
      );
    } catch (error, stack) {
      state = AsyncValue<AuditFeed>.error(error, stack);
    }
  }
}

final AutoDisposeStateNotifierProviderFamily<
  AuditNotifier,
  AsyncValue<AuditFeed>,
  AuditFilter
>
auditFeedProvider = StateNotifierProvider.autoDispose
    .family<AuditNotifier, AsyncValue<AuditFeed>, AuditFilter>((
      Ref ref,
      AuditFilter filter,
    ) {
      bindCache(ref);
      return AuditNotifier(ref, filter);
    });

// =============================================================================
// Auth and profile
// =============================================================================
/// Cached for the session: the policy changes at deploy time, not at runtime, so
/// re-requesting it per keystroke or per screen is waste.
final FutureProvider<PasswordPolicy> passwordPolicyProvider =
    FutureProvider<PasswordPolicy>((Ref ref) {
      return retrying(() => ref.read(authApiProvider).passwordPolicy());
    });

final AutoDisposeFutureProvider<List<SessionInfo>> sessionsProvider =
    FutureProvider.autoDispose<List<SessionInfo>>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(authApiProvider).listSessions());
    });

final AutoDisposeFutureProvider<UserProfile> userProfileProvider =
    FutureProvider.autoDispose<UserProfile>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(usersApiProvider).me());
    });

final AutoDisposeFutureProvider<UserStats> userStatsProvider =
    FutureProvider.autoDispose<UserStats>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(usersApiProvider).stats());
    });

// =============================================================================
// Accounting
// =============================================================================
/// Balances are point-in-time, so only the end of a range applies - "cash over March"
/// is not a number. The parameter is therefore a single `as_of` date.
final AutoDisposeFutureProviderFamily<List<Account>, String> accountsProvider =
    FutureProvider.autoDispose.family<List<Account>, String>((
      Ref ref,
      String asOf,
    ) {
      bindCache(ref);
      return retrying(
        () => ref.read(accountingApiProvider).accounts(asOf: asOf),
      );
    });

final AutoDisposeFutureProviderFamily<ProfitAndLoss, DateRange>
profitAndLossProvider = FutureProvider.autoDispose
    .family<ProfitAndLoss, DateRange>((Ref ref, DateRange range) {
      bindCache(ref);
      return retrying(
        () => ref.read(accountingApiProvider).profitAndLoss(range),
      );
    });

final AutoDisposeFutureProvider<TrialBalance> trialBalanceProvider =
    FutureProvider.autoDispose<TrialBalance>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(accountingApiProvider).trialBalance());
    });

/// The window a balance sheet is being asked for, as one value so the family has one key.
class BalanceSheetQuery {
  const BalanceSheetQuery({
    this.period = StatementPeriod.thisFiscalYear,
    this.asOf,
    this.compareTo,
  });

  final StatementPeriod period;
  final String? asOf;
  final String? compareTo;

  BalanceSheetQuery copyWith({
    StatementPeriod? period,
    String? asOf,
    String? compareTo,
  }) => BalanceSheetQuery(
    period: period ?? this.period,
    asOf: asOf ?? this.asOf,
    compareTo: compareTo ?? this.compareTo,
  );

  @override
  bool operator ==(Object other) =>
      other is BalanceSheetQuery &&
      other.period == period &&
      other.asOf == asOf &&
      other.compareTo == compareTo;

  @override
  int get hashCode => Object.hash(period, asOf, compareTo);
}

final AutoDisposeFutureProviderFamily<BalanceSheetView, BalanceSheetQuery>
balanceSheetViewProvider = FutureProvider.autoDispose
    .family<BalanceSheetView, BalanceSheetQuery>((
      Ref ref,
      BalanceSheetQuery query,
    ) {
      bindCache(ref);
      final bool custom = query.period == StatementPeriod.custom;
      return retrying(
        () => ref
            .read(accountingApiProvider)
            .balanceSheetView(
              period: query.period,
              // Only for a custom window - see `balanceSheetView`.
              asOf: custom ? query.asOf : null,
              compareTo: custom ? query.compareTo : null,
            ),
      );
    });

final AutoDisposeFutureProvider<BalanceSheet> balanceSheetProvider =
    FutureProvider.autoDispose<BalanceSheet>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(accountingApiProvider).balanceSheet());
    });

final AutoDisposeFutureProviderFamily<Paged<JournalEntry>, int>
journalEntriesProvider = FutureProvider.autoDispose
    .family<Paged<JournalEntry>, int>((Ref ref, int page) {
      bindCache(ref);
      return retrying(
        () => ref.read(accountingApiProvider).entries(page: page),
      );
    });

// =============================================================================
// Billing
// =============================================================================
final AutoDisposeFutureProvider<BillingOptions> billingOptionsProvider =
    FutureProvider.autoDispose<BillingOptions>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(billingApiProvider).options());
    });

final AutoDisposeFutureProvider<BillingSummary> billingSummaryProvider =
    FutureProvider.autoDispose<BillingSummary>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(billingApiProvider).summary());
    });

/// Every place money can sit, keyed by whether archived ones are wanted.
///
/// Separate from [billingOptionsProvider] because only this one can be asked for archived
/// accounts. That payload feeds the recording form's pickers and must never carry one.
final AutoDisposeFutureProviderFamily<List<MoneyAccount>, bool>
moneyAccountsProvider = FutureProvider.autoDispose
    .family<List<MoneyAccount>, bool>((Ref ref, bool includeArchived) {
      bindCache(ref);
      return retrying(
        () => ref
            .read(billingApiProvider)
            .moneyAccounts(includeArchived: includeArchived),
      );
    });

/// One account's bank details, **including the full account number.**
///
/// Its own provider keyed by account id rather than part of [billingOptionsProvider],
/// mirroring the API: decrypting a number is a deliberate request, not something every
/// load of the billing screen does for every account.
final AutoDisposeFutureProviderFamily<BankDetails, String> bankDetailsProvider =
    FutureProvider.autoDispose.family<BankDetails, String>((
      Ref ref,
      String accountId,
    ) {
      bindCache(ref);
      return retrying(
        () => ref.read(billingApiProvider).bankDetails(accountId),
      );
    });

/// Cards on file, keyed by whether archived ones are wanted.
///
/// Separate from [billingOptionsProvider] rather than folded into it, because that
/// payload feeds the pickers and an archived card must never reach one. Keeping
/// them apart means "show archived" cannot leak a dead card into the form.
final AutoDisposeFutureProviderFamily<List<PaymentCard>, bool>
billingCardsProvider = FutureProvider.autoDispose
    .family<List<PaymentCard>, bool>((Ref ref, bool includeArchived) {
      bindCache(ref);
      return retrying(
        () => ref
            .read(billingApiProvider)
            .cards(includeArchived: includeArchived),
      );
    });

/// The day book's filters, as one value so the provider family has a single key.
class BillingQuery {
  const BillingQuery({this.page = 1, this.direction, this.search = ''});

  final int page;
  final Direction? direction;
  final String search;

  BillingQuery copyWith({
    int? page,
    Direction? direction,
    String? search,
    bool clearDirection = false,
  }) {
    return BillingQuery(
      page: page ?? this.page,
      direction: clearDirection ? null : (direction ?? this.direction),
      search: search ?? this.search,
    );
  }

  @override
  bool operator ==(Object other) =>
      other is BillingQuery &&
      other.page == page &&
      other.direction == direction &&
      other.search == search;

  @override
  int get hashCode => Object.hash(page, direction, search);
}

final AutoDisposeFutureProviderFamily<Paged<BillingEntry>, BillingQuery>
billingEntriesProvider = FutureProvider.autoDispose
    .family<Paged<BillingEntry>, BillingQuery>((Ref ref, BillingQuery query) {
      bindCache(ref);
      return retrying(
        () => ref
            .read(billingApiProvider)
            .list(
              page: query.page,
              direction: query.direction,
              query: query.search.isEmpty ? null : query.search,
            ),
      );
    });

// =============================================================================
// Sales
// =============================================================================
class InvoiceQuery {
  const InvoiceQuery({this.page = 1, this.overdueOnly = false});

  final int page;
  final bool overdueOnly;

  @override
  bool operator ==(Object other) =>
      other is InvoiceQuery &&
      other.page == page &&
      other.overdueOnly == overdueOnly;

  @override
  int get hashCode => Object.hash(page, overdueOnly);
}

final AutoDisposeFutureProviderFamily<Paged<Invoice>, InvoiceQuery>
invoicesProvider = FutureProvider.autoDispose
    .family<Paged<Invoice>, InvoiceQuery>((Ref ref, InvoiceQuery query) {
      bindCache(ref);
      return retrying(
        () => ref
            .read(salesApiProvider)
            .invoices(
              page: query.page,
              overdueOnly: query.overdueOnly ? true : null,
            ),
      );
    });

class SearchQuery {
  const SearchQuery({this.page = 1, this.search = ''});

  final int page;
  final String search;

  @override
  bool operator ==(Object other) =>
      other is SearchQuery && other.page == page && other.search == search;

  @override
  int get hashCode => Object.hash(page, search);
}

final AutoDisposeFutureProviderFamily<Paged<Customer>, SearchQuery>
customersProvider = FutureProvider.autoDispose
    .family<Paged<Customer>, SearchQuery>((Ref ref, SearchQuery query) {
      bindCache(ref);
      return retrying(
        () => ref
            .read(salesApiProvider)
            .customers(
              page: query.page,
              query: query.search.isEmpty ? null : query.search,
            ),
      );
    });

/// Every customer, for the invoice composer's picker.
final AutoDisposeFutureProvider<Paged<Customer>> allCustomersProvider =
    FutureProvider.autoDispose<Paged<Customer>>((Ref ref) {
      bindCache(ref);
      return retrying(
        () => ref.read(salesApiProvider).customers(pageSize: 200),
      );
    });

final AutoDisposeFutureProviderFamily<Paged<Payment>, int> paymentsProvider =
    FutureProvider.autoDispose.family<Paged<Payment>, int>((Ref ref, int page) {
      bindCache(ref);
      return retrying(() => ref.read(salesApiProvider).payments(page: page));
    });

final AutoDisposeFutureProvider<Ageing> receivablesAgeingProvider =
    FutureProvider.autoDispose<Ageing>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(salesApiProvider).ageing());
    });

// =============================================================================
// Inventory and purchasing
// =============================================================================
final AutoDisposeFutureProviderFamily<Paged<Supplier>, int> suppliersProvider =
    FutureProvider.autoDispose.family<Paged<Supplier>, int>((
      Ref ref,
      int page,
    ) {
      bindCache(ref);
      return retrying(
        () => ref.read(inventoryApiProvider).suppliers(page: page),
      );
    });

final AutoDisposeFutureProvider<Paged<Supplier>> allSuppliersProvider =
    FutureProvider.autoDispose<Paged<Supplier>>((Ref ref) {
      bindCache(ref);
      return retrying(
        () => ref.read(inventoryApiProvider).suppliers(pageSize: 200),
      );
    });

final AutoDisposeFutureProviderFamily<Paged<Product>, SearchQuery>
productsProvider = FutureProvider.autoDispose
    .family<Paged<Product>, SearchQuery>((Ref ref, SearchQuery query) {
      bindCache(ref);
      return retrying(
        () => ref
            .read(inventoryApiProvider)
            .products(
              page: query.page,
              query: query.search.isEmpty ? null : query.search,
            ),
      );
    });

final AutoDisposeFutureProvider<Paged<Product>> allProductsProvider =
    FutureProvider.autoDispose<Paged<Product>>((Ref ref) {
      bindCache(ref);
      return retrying(
        () => ref.read(inventoryApiProvider).products(pageSize: 200),
      );
    });

final AutoDisposeFutureProvider<List<Warehouse>> warehousesProvider =
    FutureProvider.autoDispose<List<Warehouse>>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(inventoryApiProvider).warehouses());
    });

final AutoDisposeFutureProvider<List<StockLevel>> stockLevelsProvider =
    FutureProvider.autoDispose<List<StockLevel>>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(inventoryApiProvider).levels());
    });

final AutoDisposeFutureProvider<StockValuation> stockValuationProvider =
    FutureProvider.autoDispose<StockValuation>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(inventoryApiProvider).valuation());
    });

final AutoDisposeFutureProvider<List<ReorderRow>> reorderProvider =
    FutureProvider.autoDispose<List<ReorderRow>>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(inventoryApiProvider).reorderReport());
    });

final AutoDisposeFutureProviderFamily<Paged<StockMovement>, int>
movementsProvider = FutureProvider.autoDispose
    .family<Paged<StockMovement>, int>((Ref ref, int page) {
      bindCache(ref);
      return retrying(
        () => ref.read(inventoryApiProvider).movements(page: page),
      );
    });

final AutoDisposeFutureProviderFamily<Paged<Bill>, int> billsProvider =
    FutureProvider.autoDispose.family<Paged<Bill>, int>((Ref ref, int page) {
      bindCache(ref);
      return retrying(() => ref.read(inventoryApiProvider).bills(page: page));
    });

final AutoDisposeFutureProvider<Ageing> payablesAgeingProvider =
    FutureProvider.autoDispose<Ageing>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(inventoryApiProvider).payablesAgeing());
    });

// =============================================================================
// Documents
// =============================================================================
/// Installed software does not change while the app is open.
final FutureProvider<OcrCapabilities> ocrCapabilitiesProvider =
    FutureProvider<OcrCapabilities>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(documentsApiProvider).capabilities());
    });

class DocumentQuery {
  const DocumentQuery({
    this.page = 1,
    this.search = '',
    this.onlyReview = false,
  });

  final int page;
  final String search;
  final bool onlyReview;

  @override
  bool operator ==(Object other) =>
      other is DocumentQuery &&
      other.page == page &&
      other.search == search &&
      other.onlyReview == onlyReview;

  @override
  int get hashCode => Object.hash(page, search, onlyReview);
}

final AutoDisposeFutureProviderFamily<Paged<DocumentSummary>, DocumentQuery>
documentsProvider = FutureProvider.autoDispose
    .family<Paged<DocumentSummary>, DocumentQuery>((
      Ref ref,
      DocumentQuery query,
    ) {
      bindCache(ref);
      return retrying(
        () => ref
            .read(documentsApiProvider)
            .list(
              page: query.page,
              query: query.search.isEmpty ? null : query.search,
              needsReview: query.onlyReview ? true : null,
            ),
      );
    });

final AutoDisposeFutureProviderFamily<ScannedDocument, String>
documentProvider = FutureProvider.autoDispose.family<ScannedDocument, String>((
  Ref ref,
  String id,
) {
  bindCache(ref);
  return retrying(() => ref.read(documentsApiProvider).get(id));
});

final AutoDisposeFutureProviderFamily<DocumentText, String>
documentTextProvider = FutureProvider.autoDispose.family<DocumentText, String>((
  Ref ref,
  String id,
) {
  bindCache(ref);
  return retrying(() => ref.read(documentsApiProvider).text(id));
});

// =============================================================================
// Invalidation groups
// =============================================================================
/// Everything a ledger write invalidates.
///
/// Listed exhaustively rather than clearing the whole cache: a posting changes the
/// dashboard, the trial balance, and the control-account reconciliation, and a screen
/// still showing the old figure after an entry reads as a bug in the arithmetic rather
/// than a stale cache. Clearing everything would also drop the member list and the
/// permission catalogue, which have not changed and cost a round trip each.
void invalidateLedger(WidgetRef ref) {
  ref.invalidate(billingEntriesProvider);
  ref.invalidate(billingSummaryProvider);
  ref.invalidate(dashboardProvider);
  ref.invalidate(trendProvider);
  ref.invalidate(trendForRangeProvider);
  ref.invalidate(controlChecksProvider);
  ref.invalidate(trialBalanceProvider);
  ref.invalidate(balanceSheetProvider);
  ref.invalidate(balanceSheetViewProvider);
  ref.invalidate(profitAndLossProvider);
  ref.invalidate(accountsProvider);
  ref.invalidate(journalEntriesProvider);
  ref.invalidate(topCustomersProvider);
  ref.invalidate(topProductsProvider);
}

/// Everything registering or archiving a card invalidates.
///
/// The card list and the pickers, always. The ledger too, but only for a *new*
/// credit card - that creates a liability account, so the chart of accounts and the
/// trial balance have genuinely changed. Archiving one changes neither, and calling
/// [invalidateLedger] for it would refetch a dozen reports to show identical
/// figures; the caller decides via [ledgerChanged].
void invalidateCards(WidgetRef ref, {bool ledgerChanged = false}) {
  ref.invalidate(billingCardsProvider);
  ref.invalidate(billingOptionsProvider);
  if (ledgerChanged) invalidateLedger(ref);
}

/// Everything editing an account's bank details invalidates.
///
/// The details themselves and the picker payload, which carries the bank name and the last
/// four digits for the line under each account. **Not the ledger** - a bank name is a
/// description of an account, not a posting, so no balance or report has changed.
void invalidateBankDetails(WidgetRef ref) {
  ref.invalidate(bankDetailsProvider);
  ref.invalidate(moneyAccountsProvider);
  ref.invalidate(billingOptionsProvider);
  ref.invalidate(accountsProvider);
}

/// Everything archiving or restoring an account invalidates.
///
/// The account lists and the pickers built from them. **Not the ledger** - archiving posts
/// nothing, so no balance or report changes; the account simply stops being offered.
void invalidateMoneyAccounts(WidgetRef ref) {
  ref.invalidate(moneyAccountsProvider);
  ref.invalidate(billingOptionsProvider);
  ref.invalidate(accountsProvider);
}

/// Everything a stock or product change invalidates.
///
/// Stock is an asset, so a movement changes the balance sheet, the dashboard, and the
/// reconciliation as well as the stock screens.
void invalidateInventory(WidgetRef ref) {
  ref.invalidate(productsProvider);
  ref.invalidate(allProductsProvider);
  ref.invalidate(stockLevelsProvider);
  ref.invalidate(stockValuationProvider);
  ref.invalidate(movementsProvider);
  ref.invalidate(warehousesProvider);
  ref.invalidate(reorderProvider);
  ref.invalidate(accountsProvider);
  ref.invalidate(trialBalanceProvider);
  ref.invalidate(dashboardProvider);
  ref.invalidate(controlChecksProvider);
}

/// Everything an invoice or bill posting invalidates.
void invalidateDocuments(WidgetRef ref) {
  ref.invalidate(invoicesProvider);
  ref.invalidate(billsProvider);
  ref.invalidate(receivablesAgeingProvider);
  ref.invalidate(payablesAgeingProvider);
  ref.invalidate(documentsProvider);
  ref.invalidate(trialBalanceProvider);
  ref.invalidate(dashboardProvider);
  ref.invalidate(controlChecksProvider);
}

// =============================================================================
// Ledger 3 - the proof ledger
// =============================================================================
/// Sealing status, including the chain's own view.
///
/// **Not `autoDispose`-cached for long**, and the reason is specific to this
/// screen: the status call reads the Stellar contract on every request, so a stale
/// answer here is not merely old, it is misleading. A business checking whether its
/// books are sealed needs now.
final AutoDisposeFutureProvider<AttestationStatus> attestationStatusProvider =
    FutureProvider.autoDispose<AttestationStatus>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(trustApiProvider).status());
    });

/// The organization's seal history, newest first.
final AutoDisposeFutureProvider<SealPage> sealHistoryProvider =
    FutureProvider.autoDispose<SealPage>((Ref ref) {
      bindCache(ref);
      return retrying(() => ref.read(trustApiProvider).seals());
    });
