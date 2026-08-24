import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/auth_api.dart';
import '../core/api_client.dart';
import '../core/locale_settings.dart';
import '../models/auth.dart';
import 'providers.dart';

/// Session state for the app.
///
/// On start it attempts one silent refresh: the access token lives in memory and is
/// gone after a quit, but the persisted refresh cookie is not, so exchanging it
/// restores the session without the user re-entering anything. Failure just means
/// "signed out", which is the ordinary first-launch case.
///
/// [isLoading] exists so route guards can distinguish "not signed in" from "we do
/// not know yet". Without it, every launch would bounce an authenticated user to the
/// sign-in screen for a frame before the refresh completes - and worse, a deep link
/// would be resolved against the wrong answer.
class AuthState {
  const AuthState({this.user, this.isLoading = true});

  final AuthenticatedUser? user;

  /// True until the initial session restore settles. Gate routing on this.
  final bool isLoading;

  bool get isAuthenticated => user != null;

  OrganizationSummary? get organization => user?.activeOrganization;

  /// Permission check for conditionally rendering UI.
  ///
  /// The server enforces every one of these on every request; this only decides
  /// whether to *offer* something. Hiding a control the caller cannot use is better
  /// than showing one that 403s.
  bool can(String permission) =>
      user?.permissions.contains(permission) ?? false;

  AuthState copyWith({
    AuthenticatedUser? user,
    bool? isLoading,
    bool clearUser = false,
  }) {
    return AuthState(
      user: clearUser ? null : (user ?? this.user),
      isLoading: isLoading ?? this.isLoading,
    );
  }
}

class AuthController extends StateNotifier<AuthState> {
  AuthController(this._client, this._api, this._ref)
    : super(const AuthState()) {
    // Called by the HTTP layer when a refresh fails - the session is genuinely
    // over, not merely stale.
    _client.onSessionExpired = _clearLocally;
    _restore();
  }

  final ApiClient _client;
  final AuthApi _api;
  final Ref _ref;

  Future<void> _restore() async {
    try {
      final bool restored = await _client.bootstrapSession();
      if (!restored) {
        state = const AuthState(isLoading: false);
        return;
      }
      final AuthenticatedUser principal = await _api.me();
      _applyPrincipal(principal);
      state = state.copyWith(isLoading: false);
    } catch (_) {
      await _client.clearSession();
      _resetLocale();
      state = const AuthState(isLoading: false);
    }
  }

  /// Set the principal, and adopt their organization's currency, timezone, and
  /// financial year at the same moment.
  ///
  /// One method rather than a state assignment plus a reminder, because the two must
  /// never drift: the formatters read those settings, so a principal set without them
  /// renders every amount on the next frame in the wrong currency.
  void _applyPrincipal(AuthenticatedUser principal) {
    final OrganizationSummary? organization = principal.activeOrganization;
    if (organization == null) {
      setLocaleSettings(reset: true);
    } else {
      setLocaleSettings(
        currency: organization.currency,
        timezone: organization.timezone,
        fiscalYearStartMonth: organization.fiscalYearStartMonth,
      );
    }
    state = AuthState(user: principal, isLoading: false);
  }

  void _resetLocale() => setLocaleSettings(reset: true);

  /// Store the result of a successful sign-in.
  void applySession(TokenResponse tokens) {
    _client.accessToken = tokens.accessToken;
    _applyPrincipal(tokens.user);
  }

  /// How long the server gets to acknowledge a sign-out before the client stops caring.
  ///
  /// Short on purpose. The refresh token expires on its own, so a server that will not
  /// answer in this long is not worth making the user watch a dead button for - and the
  /// alternative is Dio's 30-second receive timeout, which is indistinguishable from the
  /// app being broken.
  static const Duration _logoutGrace = Duration(seconds: 3);

  /// Leave. **Locally first, then tell the server.**
  ///
  /// The ordering is the whole point, and it is the opposite of the obvious one. Awaiting
  /// the network call before tearing down meant that whenever the API was slow or
  /// unreachable, pressing "Sign out" did nothing at all for as long as Dio would wait -
  /// no spinner, no error, because the button discards this future. Sign-out is a local
  /// act; the request is a courtesy.
  ///
  /// **The credentials outlive the UI teardown deliberately.** The access token and the
  /// cookie jar are left intact until the request resolves, because a logout that arrived
  /// unauthenticated would revoke nothing and leave the refresh token valid for its full
  /// lifetime - thirty days for a session that ticked "keep me signed in". So the screen
  /// returns to sign-in immediately while the revocation is still in flight, and the jar
  /// is emptied once it lands.
  Future<void> signOut({bool allDevices = false}) async {
    // Releases the UI: the router's guard re-runs on this and lands on sign-in.
    _forgetPrincipal();

    try {
      await _api.logout(allDevices: allDevices).timeout(_logoutGrace);
    } catch (_) {
      // Includes the timeout, and a 401 from a session the server had already dropped.
      // Neither changes what happens next: the teardown below is what ends the session on
      // this machine, and it runs either way.
    }

    // Awaited rather than fire-and-forget: the user asked to leave, so the cookie must be
    // off disk before this future completes.
    await _client.clearSession();
  }

  /// Re-fetch the principal after a change to profile, organization, or permissions.
  Future<void> refresh() async {
    try {
      _applyPrincipal(await _api.me());
    } catch (_) {
      await _client.clearSession();
      _clearLocally();
    }
  }

  Future<void> switchOrganization(String organizationId) async {
    final TokenResponse tokens = await _api.switchOrganization(organizationId);
    _client.accessToken = tokens.accessToken;
    _applyPrincipal(tokens.user);
    // Every cached query was scoped to the previous organization, so all of them
    // are wrong now rather than merely stale.
    clearCache(_ref);
  }

  /// Forget who was signed in, **without touching the stored credentials.**
  ///
  /// Synchronous and complete on return, which is what lets [signOut] put the user back on
  /// the sign-in screen before the network has been heard from. Split out from
  /// [_clearLocally] precisely because of that one caller: it still needs the cookie jar
  /// for a moment longer in order to revoke the refresh token.
  void _forgetPrincipal() {
    _resetLocale();
    state = const AuthState(isLoading: false);
    // Drop every cached query: leaving another user's figures in memory after a
    // sign-out on a shared machine would show them to the next person.
    clearCache(_ref);
  }

  /// Tear down after the session has ended without the user asking - an expired or revoked
  /// refresh token.
  ///
  /// **The jar is emptied too, and that is not just tidiness.** The cookie that failed is
  /// dead: the backend has either expired it or revoked its whole lineage after detecting
  /// reuse. Leaving it on disk means every subsequent launch spends a round trip presenting
  /// a credential that is guaranteed to be refused, and the sign-in screen arrives a beat
  /// later than it needs to for no reason.
  void _clearLocally() {
    // Fire-and-forget: this is called from the HTTP layer's synchronous callback, which
    // cannot wait on a disk write. Unlike `signOut`, this path has no reason to keep the
    // credentials - the cookie it would present has already been refused.
    unawaited(_client.clearSession());
    _forgetPrincipal();
  }
}

final StateNotifierProvider<AuthController, AuthState> authControllerProvider =
    StateNotifierProvider<AuthController, AuthState>(
      (Ref ref) => AuthController(
        ref.watch(apiClientProvider),
        ref.watch(authApiProvider),
        ref,
      ),
    );
