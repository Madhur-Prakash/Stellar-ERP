import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/accounting_api.dart';
import '../api/analytics_api.dart';
import '../api/auth_api.dart';
import '../api/billing_api.dart';
import '../api/documents_api.dart';
import '../api/inventory_api.dart';
import '../api/organizations_api.dart';
import '../api/sales_api.dart';
import '../core/api_client.dart';
import '../core/api_error.dart';

/// Dependency wiring.
///
/// Riverpod stands in for TanStack Query here, and the mapping is close enough to
/// be worth stating: a `FutureProvider` is a query, `ref.watch` is `useQuery`,
/// `ref.invalidate(provider)` is `invalidateQueries`, and a family parameter is the
/// tail of a query key. What Riverpod does not give for free is the retry policy and
/// the "keep the last value while refetching" behaviour, so both are supplied here
/// rather than left to differ per screen.
///
/// The client itself is injected by `main`, which is what lets the async cookie-jar
/// setup happen once before the first widget builds.
final Provider<ApiClient> apiClientProvider = Provider<ApiClient>(
  (Ref ref) => throw StateError(
    'apiClientProvider must be overridden in main() with the initialised client',
  ),
);

final Provider<AuthApi> authApiProvider = Provider<AuthApi>(
  (Ref ref) => AuthApi(ref.watch(apiClientProvider)),
);

final Provider<OrganizationsApi> organizationsApiProvider =
    Provider<OrganizationsApi>(
      (Ref ref) => OrganizationsApi(ref.watch(apiClientProvider)),
    );

final Provider<UsersApi> usersApiProvider = Provider<UsersApi>(
  (Ref ref) => UsersApi(ref.watch(apiClientProvider)),
);

final Provider<AccountingApi> accountingApiProvider = Provider<AccountingApi>(
  (Ref ref) => AccountingApi(ref.watch(apiClientProvider)),
);

final Provider<AnalyticsApi> analyticsApiProvider = Provider<AnalyticsApi>(
  (Ref ref) => AnalyticsApi(ref.watch(apiClientProvider)),
);

final Provider<BillingApi> billingApiProvider = Provider<BillingApi>(
  (Ref ref) => BillingApi(ref.watch(apiClientProvider)),
);

final Provider<SalesApi> salesApiProvider = Provider<SalesApi>(
  (Ref ref) => SalesApi(ref.watch(apiClientProvider)),
);

final Provider<InventoryApi> inventoryApiProvider = Provider<InventoryApi>(
  (Ref ref) => InventoryApi(ref.watch(apiClientProvider)),
);

final Provider<DocumentsApi> documentsApiProvider = Provider<DocumentsApi>(
  (Ref ref) => DocumentsApi(ref.watch(apiClientProvider)),
);

/// A generation counter every data provider watches, so one bump clears them all.
///
/// TanStack Query has `queryClient.clear()`, which sign-out and organization
/// switching both rely on. Riverpod has no equivalent, and enumerating every
/// provider at those two call sites is exactly the list that would go stale the next
/// time a screen was added - leaving one user's figures on screen for the next
/// person, which on a shared machine is a data leak rather than a glitch.
///
/// So the dependency is inverted: providers opt in by calling [bindCache], and
/// [clearCache] invalidates all of them at once. A `StateProvider` rather than a
/// recomputed constant, because Riverpod short-circuits an identical value and
/// nothing would rebuild.
final StateProvider<int> cacheEpochProvider = StateProvider<int>(
  (Ref ref) => 0,
);

/// Enrol a data provider in [clearCache]. Call it first, before any await.
void bindCache(Ref ref) => ref.watch(cacheEpochProvider);

/// Discard every cached query.
void clearCache(Ref ref) =>
    ref.read(cacheEpochProvider.notifier).update((int epoch) => epoch + 1);

/// The web app's query retry policy, applied to a single request.
///
/// **A client error is never retried.** A 403 or 404 will not resolve itself, and
/// retrying an auth failure just burns the rate limit. Anything that might be
/// transient - offline, timeout, 5xx - gets two more attempts with exponential
/// backoff capped at eight seconds.
///
/// Mutations do not go through this, and that is deliberate: a retried POST can
/// duplicate an invoice, and no accounting system should risk that silently.
Future<T> retrying<T>(
  Future<T> Function() request, {
  int maxAttempts = 3,
}) async {
  int attempt = 0;
  while (true) {
    try {
      return await request();
    } catch (error) {
      final ApiError normalised = ApiError.from(error);
      attempt++;
      if (attempt >= maxAttempts || !normalised.isRetryable) throw normalised;
      final int delayMs = (1000 * (1 << (attempt - 1))).clamp(1000, 8000);
      await Future<void>.delayed(Duration(milliseconds: delayMs));
    }
  }
}
