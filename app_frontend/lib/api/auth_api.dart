import '../core/api_client.dart';
import '../models/auth.dart';
import '../models/json.dart';
import '../models/page.dart';

/// Auth endpoint bindings.
///
/// A thin, typed layer over the HTTP client. Keeping the URLs here means a route
/// rename touches one file, and screens never contain string paths - so a typo is a
/// compile error at the binding rather than a 404 at runtime.
class AuthApi {
  const AuthApi(this._client);

  final ApiClient _client;

  Future<RegisterResponse> register({
    required String email,
    required String password,
    required String fullName,
    String? organizationName,
    String? invitationToken,
  }) async {
    final Json json = await _client.post<Json>(
      '/auth/register',
      body: <String, dynamic>{
        'email': email,
        'password': password,
        'full_name': fullName,
        // Only one of these is sent - the server rejects both together.
        if (invitationToken != null)
          'invitation_token': invitationToken
        else if (organizationName != null && organizationName.isNotEmpty)
          'organization_name': organizationName,
      },
    );
    return RegisterResponse.fromJson(json);
  }

  Future<LoginResult> login({
    required String email,
    required String password,
    bool rememberMe = false,
  }) async {
    final Json json = await _client.post<Json>(
      '/auth/login',
      body: <String, dynamic>{
        'email': email,
        'password': password,
        'remember_me': rememberMe,
      },
    );
    return LoginResult.fromJson(json);
  }

  Future<TokenResponse> loginTwoFactor({
    required String challengeId,
    required String code,
    bool rememberMe = false,
  }) async {
    final Json json = await _client.post<Json>(
      '/auth/login/2fa',
      body: <String, dynamic>{
        'challenge_id': challengeId,
        'code': code,
        'remember_me': rememberMe,
      },
    );
    return TokenResponse.fromJson(json);
  }

  Future<void> logout({bool allDevices = false}) => _client.post<Json>(
    '/auth/logout',
    body: <String, dynamic>{'all_devices': allDevices},
  );

  Future<AuthenticatedUser> me() async =>
      AuthenticatedUser.fromJson(await _client.get<Json>('/auth/me'));

  Future<MessageResponse> verifyEmail(String token) async =>
      MessageResponse.fromJson(
        await _client.post<Json>(
          '/auth/verify-email',
          body: <String, dynamic>{'token': token},
        ),
      );

  Future<void> resendVerification(String email) => _client.post<Json>(
    '/auth/resend-verification',
    body: <String, dynamic>{'email': email},
  );

  Future<void> forgotPassword(String email) => _client.post<Json>(
    '/auth/forgot-password',
    body: <String, dynamic>{'email': email},
  );

  Future<void> resetPassword({
    required String email,
    required String code,
    required String newPassword,
  }) => _client.post<Json>(
    '/auth/reset-password',
    body: <String, dynamic>{
      'email': email,
      'code': code,
      'new_password': newPassword,
    },
  );

  Future<void> changePassword({
    required String currentPassword,
    required String newPassword,
  }) => _client.post<Json>(
    '/auth/change-password',
    body: <String, dynamic>{
      'current_password': currentPassword,
      'new_password': newPassword,
    },
  );

  Future<void> requestMagicLink(String email) => _client.post<Json>(
    '/auth/magic-link',
    body: <String, dynamic>{'email': email},
  );

  /// Start a sign-in that finishes *here*, not in whatever browser opens the mail.
  ///
  /// [requestMagicLink] is the browser's endpoint: it sends a link and this app never
  /// hears about it again. This one returns a handle to poll, so opening the link
  /// signs this app in too.
  Future<DeviceSignInStarted> startDeviceSignIn(String email) async =>
      DeviceSignInStarted.fromJson(
        await _client.post<Json>(
          '/auth/magic-link/device',
          body: <String, dynamic>{'email': email},
        ),
      );

  /// Ask whether the emailed link has been opened yet.
  ///
  /// Throws [ApiError] with a 401 once the handle has expired or been claimed, which
  /// is the signal to stop polling rather than a failure to report as one.
  Future<DeviceSignInPoll> pollDeviceSignIn(String deviceHandle) async =>
      DeviceSignInPoll.fromJson(
        await _client.post<Json>(
          '/auth/magic-link/device/poll',
          body: <String, dynamic>{'device_handle': deviceHandle},
        ),
      );

  /// Consume a sign-in link.
  ///
  /// Two shapes: tokens when *this* client asked for the link, or an approval when
  /// another client did - whoever requested it is who gets signed in.
  Future<MagicLinkVerifyResult> verifyMagicLink(String token) async =>
      MagicLinkVerifyResult.fromJson(
        await _client.post<Json>(
          '/auth/magic-link/verify',
          body: <String, dynamic>{'token': token},
        ),
      );

  Future<void> requestOtp(String email) =>
      _client.post<Json>('/auth/otp', body: <String, dynamic>{'email': email});

  Future<TokenResponse> verifyOtp({
    required String email,
    required String code,
  }) async => TokenResponse.fromJson(
    await _client.post<Json>(
      '/auth/otp/verify',
      body: <String, dynamic>{'email': email, 'code': code},
    ),
  );

  Future<PasswordPolicy> passwordPolicy() async =>
      PasswordPolicy.fromJson(await _client.get<Json>('/auth/password-policy'));

  // --- Two-factor ---
  Future<TwoFactorSetup> beginTwoFactorSetup() async =>
      TwoFactorSetup.fromJson(await _client.post<Json>('/auth/2fa/setup'));

  Future<TwoFactorEnableResponse> enableTwoFactor(String code) async =>
      TwoFactorEnableResponse.fromJson(
        await _client.post<Json>(
          '/auth/2fa/enable',
          body: <String, dynamic>{'code': code},
        ),
      );

  Future<void> disableTwoFactor(String password) => _client.post<Json>(
    '/auth/2fa/disable',
    body: <String, dynamic>{'password': password},
  );

  // --- Sessions ---
  Future<List<SessionInfo>> listSessions() async {
    final List<dynamic> raw = await _client.get<List<dynamic>>(
      '/auth/sessions',
    );
    return raw.cast<Json>().map(SessionInfo.fromJson).toList(growable: false);
  }

  Future<void> revokeSession(String sessionId) =>
      _client.delete<Json>('/auth/sessions/$sessionId');

  /// Returns a *new* access token: permissions are per-organization and embedded
  /// in the token, so the old one cannot be reused.
  Future<TokenResponse> switchOrganization(String organizationId) async =>
      TokenResponse.fromJson(
        await _client.post<Json>('/auth/switch-organization/$organizationId'),
      );
}
