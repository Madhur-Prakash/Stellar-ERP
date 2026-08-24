import 'dart:async';
import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:personalerp_desktop/api/auth_api.dart';
import 'package:personalerp_desktop/core/api_client.dart';
import 'package:personalerp_desktop/models/auth.dart';
import 'package:personalerp_desktop/state/auth_controller.dart';
import 'package:personalerp_desktop/state/providers.dart';

/// Signing out when the server will not answer.
///
/// This is a regression test for a bug a user hit, and the failure mode is worth stating
/// because it looks like nothing at all: `signOut` used to `await` the logout request
/// *before* tearing down, the sign-out button discards the future it returns, and Dio waits
/// 30 seconds before giving up. So against a stalled API, pressing "Sign out" produced no
/// redirect, no spinner, and no error - indistinguishable from a dead button, and easy to
/// read as "the app is not connected to the backend".
///
/// The fix inverts the order: forget the principal first, then tell the server on a short
/// leash. These tests pin both halves - that the teardown does not wait, and that the
/// request is still made with credentials attached so the refresh token really is revoked.
/// An API whose logout never returns, standing in for an unreachable or wedged server.
///
/// A never-completing future rather than a delay, so the test cannot pass by merely being
/// slower than a timer - if the implementation awaits this at all, it waits forever.
final class HangingAuthApi extends AuthApi {
  HangingAuthApi(super.client);

  int logoutCalls = 0;
  bool? allDevicesSeen;

  @override
  Future<void> logout({bool allDevices = false}) {
    logoutCalls++;
    allDevicesSeen = allDevices;
    return Completer<void>().future;
  }
}

void main() {
  const AuthenticatedUser someone = AuthenticatedUser(
    id: 'user-1',
    email: 'priya@example.com',
    fullName: 'Jhon Doe',
    initials: 'PS',
    isEmailVerified: true,
    isTwoFactorEnabled: false,
    isSuperuser: false,
    locale: 'en-IN',
    timezone: 'Asia/Kolkata',
    theme: 'system',
    organizations: <OrganizationSummary>[],
    permissions: <String>['journal:read'],
  );

  const TokenResponse session = TokenResponse(
    accessToken: 'access-token',
    expiresIn: 900,
    sessionId: 'session-1',
    user: someone,
    mustChangePassword: false,
  );

  late Directory jarDir;
  late ApiClient client;
  late HangingAuthApi api;
  late ProviderContainer container;

  setUp(() async {
    jarDir = Directory.systemTemp.createTempSync('signout_jar');
    // A real client, so `clearSession` exercises the actual cookie jar. Pointed at a port
    // nothing listens on: the constructor's silent restore must fail, which is the
    // ordinary signed-out first launch.
    client = await ApiClient.create(storageDirectory: jarDir.path);
    api = HangingAuthApi(client);
    container = ProviderContainer(
      overrides: <Override>[
        apiClientProvider.overrideWithValue(client),
        authApiProvider.overrideWithValue(api),
      ],
    );
  });

  tearDown(() {
    container.dispose();
    if (jarDir.existsSync()) jarDir.deleteSync(recursive: true);
  });

  test('the user is signed out before the server is heard from', () async {
    final AuthController controller = container.read(
      authControllerProvider.notifier,
    );
    controller.applySession(session);
    expect(controller.state.isAuthenticated, isTrue);
    expect(client.accessToken, 'access-token');

    // Deliberately not awaited - this is what the sign-out button does.
    final Future<void> pending = controller.signOut();

    // The assertion that would have failed before the fix. No `await` between starting
    // the sign-out and checking: the teardown must already have happened, because the
    // router's guard runs off this state and the user is owed an immediate answer.
    expect(
      controller.state.isAuthenticated,
      isFalse,
      reason: 'sign-out must not wait on the network to release the UI',
    );
    expect(controller.state.isLoading, isFalse);

    // ...and the request went out anyway.
    expect(api.logoutCalls, 1);

    // The future itself settles once the grace period lapses, rather than hanging for
    // Dio's receive timeout or forever.
    await expectLater(pending.timeout(const Duration(seconds: 10)), completes);
  });

  test('a wedged server does not stop the credentials being cleared', () async {
    final AuthController controller = container.read(
      authControllerProvider.notifier,
    );
    controller.applySession(session);

    await controller.signOut().timeout(const Duration(seconds: 10));

    // The access token is gone even though the logout request never resolved. This is what
    // makes the sign-out real on this machine regardless of what the server did.
    expect(client.accessToken, isNull);
    expect(controller.state.isAuthenticated, isFalse);
  });

  test('the token is still live when the revocation is sent', () async {
    final AuthController controller = container.read(
      authControllerProvider.notifier,
    );
    controller.applySession(session);

    final Future<void> pending = controller.signOut();

    // Not merely incidental ordering. A logout that arrived unauthenticated would revoke
    // nothing, leaving the refresh token valid for its full thirty days on a session that
    // ticked "keep me signed in" - so the credentials must outlive the UI teardown.
    // Asserted before awaiting, which is the window this is about.
    expect(api.logoutCalls, 1);
    expect(
      client.accessToken,
      'access-token',
      reason: 'the revocation must be able to authenticate itself',
    );

    // Settled before the fixture goes away. Left dangling, the grace period would lapse
    // after `tearDown` had deleted the jar directory, and the late `clearSession` would
    // surface as a failure in whichever test happened to be running by then - which is
    // exactly how this test first failed.
    await pending.timeout(const Duration(seconds: 10));
  });

  test('signing out everywhere passes the flag through', () async {
    final AuthController controller = container.read(
      authControllerProvider.notifier,
    );
    controller.applySession(session);

    await controller
        .signOut(allDevices: true)
        .timeout(const Duration(seconds: 10));

    expect(api.allDevicesSeen, isTrue);
    expect(controller.state.isAuthenticated, isFalse);
  });
}
