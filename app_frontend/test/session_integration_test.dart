import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stellarerp_desktop/api/auth_api.dart';
import 'package:stellarerp_desktop/core/api_client.dart';
import 'package:stellarerp_desktop/core/env.dart';
import 'package:stellarerp_desktop/models/auth.dart';

/// The session round-trip, against a real backend.
///
/// **This is the one part of the client that cannot be proved by a unit test**, and it is
/// also the part most likely to be quietly wrong. The refresh token never appears in a
/// response body - it arrives as a `Set-Cookie` header scoped to `path=/api/v1/auth` with
/// `SameSite=Strict`, and the app is expected to hold it in a jar it cannot read and present
/// it back on `/auth/refresh`. A mocked test would only assert that the code does what it
/// was written to do; whether a Dart cookie jar actually honours that path scope, and
/// whether the backend accepts what it sends back, is a question only the real server can
/// answer.
///
/// So this drives the whole flow through the production [ApiClient]: register, verify with
/// the token out of the email, sign in, then **discard the access token and restore the
/// session from the cookie alone**. That last step is what a user experiences as "it
/// remembered me", and it is the step that has no unit-testable surface.
///
/// **Skipped when the stack is not up, and when there is no inbox to read.** `flutter test`
/// must stay runnable on a machine with no Docker, so the test probes both and marks itself
/// skipped rather than failing. Email is Gmail-only, so the default stack has no local
/// inbox and this test skips - see the note at the probe below.
///
/// The probes are inside the body rather than in `skip:` because `skip:` is evaluated when the
/// test is *declared*, before any `setUpAll` has run - so a reachability check there always
/// reads false and the test never runs even with the stack up. Bring the stack up with
/// `make up` to include it.
void main() {
  const String mailpit = 'http://localhost:8025';

  test(
    'restores a session from the refresh cookie after the access token is gone',
    () async {
      final Dio probe = Dio(
        BaseOptions(
          connectTimeout: const Duration(seconds: 3),
          receiveTimeout: const Duration(seconds: 5),
          validateStatus: (_) => true,
        ),
      );

      bool stackUp = false;
      try {
        final Response<dynamic> health = await probe.get<dynamic>(
          '${Env.apiBaseUrl}/health',
        );
        stackUp = health.statusCode == 200;
      } catch (_) {
        stackUp = false;
      }

      if (!stackUp) {
        markTestSkipped(
          'Backend not reachable at ${Env.apiBaseUrl} - run `make up`',
        );
        return;
      }

      // ---- An inbox to read the token out of -------------------------------
      // The backend sends through the Gmail API and nothing else, so the default stack
      // has no local inbox and this test cannot complete: sign-in requires a verified
      // email, and the only way to verify one is a link that now goes to a real mailbox.
      //
      // Skipped rather than failed. A red suite on every machine would be reporting a
      // dependency that was removed on purpose, not a defect. Point `mailpit` at any
      // inbox exposing Mailpit's search API to run it again.
      bool inboxUp = false;
      try {
        final Response<dynamic> ping = await probe.get<dynamic>(
          '$mailpit/api/v1/info',
        );
        inboxUp = ping.statusCode == 200;
      } catch (_) {
        inboxUp = false;
      }

      if (!inboxUp) {
        markTestSkipped(
          'No local inbox at $mailpit - email is Gmail-only, so the verification token '
          'cannot be read back. See docs/development.md.',
        );
        return;
      }

      // A fresh address per run: registration is idempotent only in the sense that a
      // repeat is refused, and a test that fails the second time it is run is useless.
      final String stamp = DateTime.now().microsecondsSinceEpoch.toString();
      final String email = 'qa-$stamp@example.com';
      // Deliberately unrelated to the name and the address. The policy has a blocklist
      // backstop that also rejects a password containing the user's own name or email, so
      // anything built from "Desktop Tester" or the address is refused - which is the check
      // working, not a bad password.
      const String password = r'Kx7!vqmZ#4tr';

      final Directory jarDir = Directory.systemTemp.createTempSync(
        'stellarerp-session-test',
      );
      addTearDown(() => jarDir.deleteSync(recursive: true));

      final ApiClient client = await ApiClient.create(
        storageDirectory: jarDir.path,
      );
      final AuthApi auth = AuthApi(client);

      // ---- Register -------------------------------------------------------
      final RegisterResponse registered = await auth.register(
        email: email,
        password: password,
        fullName: 'Jhon Doe',
        organizationName: 'Desktop Test Co',
      );
      expect(registered.email, email);
      expect(
        registered.emailVerificationRequired,
        isTrue,
        reason: 'the backend requires verification before sign-in',
      );

      // ---- Verify, using the token out of the actual email ----------------
      final String token = await _verificationToken(probe, mailpit, email);
      await auth.verifyEmail(token);

      // ---- Sign in --------------------------------------------------------
      // `rememberMe: true` mirrors the desktop client's default - the backend reads it as a
      // session lifetime, and testing the other branch would test a setting the app does
      // not ship with.
      final LoginResult result = await auth.login(
        email: email,
        password: password,
        rememberMe: true,
      );
      final TokenResponse tokens = switch (result) {
        LoginTokens(tokens: final TokenResponse t) => t,
        LoginChallenge() => fail(
          'a fresh account has no second factor enabled',
        ),
      };
      client.accessToken = tokens.accessToken;

      final AuthenticatedUser signedIn = await auth.me();
      expect(signedIn.email, email);
      expect(
        signedIn.activeOrganization?.name,
        'Desktop Test Co',
        reason:
            'registering with an organization name should create and select it',
      );
      // The session payload has to carry the locale settings every formatter reads.
      expect(signedIn.activeOrganization?.currency, isNotEmpty);
      expect(signedIn.activeOrganization?.timezone, isNotEmpty);

      // ---- The actual subject: restore from the cookie alone ---------------
      // Exactly what happens when the app is quit and reopened. The access token dies
      // with the process; only the jar survives.
      client.accessToken = null;
      final bool restored = await client.bootstrapSession();
      expect(
        restored,
        isTrue,
        reason: 'the persisted refresh cookie should mint a new access token',
      );
      expect(client.accessToken, isNotNull);
      expect(
        client.accessToken,
        isNot(tokens.accessToken),
        reason: 'refresh must mint a new token, not replay the old one',
      );

      final AuthenticatedUser afterRestore = await auth.me();
      expect(afterRestore.id, signedIn.id);

      // ---- And that a *different* client on the same jar restores too --------
      // This is the one the user actually feels: quit the app, open it again, still signed
      // in. A second `bootstrapSession` on the same instance would only prove the in-memory
      // single-flight future works; a fresh `ApiClient` has to read the cookie back off
      // disk, which is what a relaunched process does.
      final ApiClient relaunched = await ApiClient.create(
        storageDirectory: jarDir.path,
      );
      expect(
        await relaunched.bootstrapSession(),
        isTrue,
        reason:
            'a newly constructed client must restore from the persisted cookie',
      );
      final AuthenticatedUser afterRelaunch = await AuthApi(relaunched).me();
      expect(afterRelaunch.id, signedIn.id);

      // ---- And that a sign-out really ends it ------------------------------
      await AuthApi(relaunched).logout();
      await relaunched.clearSession();
      expect(
        await relaunched.bootstrapSession(),
        isFalse,
        reason: 'a cleared jar has no cookie to exchange',
      );

      // Same jar, so a fresh client after sign-out must not get back in either.
      final ApiClient afterSignOut = await ApiClient.create(
        storageDirectory: jarDir.path,
      );
      expect(
        await afterSignOut.bootstrapSession(),
        isFalse,
        reason: 'signing out must not leave a usable cookie behind on disk',
      );
    },
    timeout: const Timeout(Duration(seconds: 90)),
  );
}

/// Pull the verification token out of the email the backend actually sent.
///
/// Through Mailpit rather than by reading the database: the token in the email is the one a
/// user would click, and asserting on anything else would leave the emailed link untested.
/// The backend's own logging masks it, which is why Mailpit is the only route.
Future<String> _verificationToken(
  Dio probe,
  String mailpit,
  String email,
) async {
  for (int attempt = 0; attempt < 10; attempt++) {
    final Response<dynamic> list = await probe.get<dynamic>(
      '$mailpit/api/v1/search',
      queryParameters: <String, dynamic>{'query': 'to:$email'},
    );
    final Object? messages = (list.data as Map<String, dynamic>?)?['messages'];
    if (messages is List && messages.isNotEmpty) {
      final String id =
          (messages.first as Map<String, dynamic>)['ID'] as String;
      final Response<dynamic> message = await probe.get<dynamic>(
        '$mailpit/api/v1/message/$id',
      );
      final Map<String, dynamic> body = message.data as Map<String, dynamic>;
      final String text = '${body['Text'] ?? ''}${body['HTML'] ?? ''}';

      final RegExpMatch? match = RegExp(
        r'token=([A-Za-z0-9._\-]+)',
      ).firstMatch(text);
      if (match != null) return match.group(1)!;
      fail('the verification email carried no token:\n$text');
    }
    await Future<void>.delayed(const Duration(milliseconds: 500));
  }
  fail('no verification email arrived for $email');
}
