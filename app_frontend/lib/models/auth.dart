import 'json.dart';

/// Auth and user contracts, mirroring `backend/app/modules/auth/schemas.py`.
///
/// **No refresh token appears anywhere in this file**, and that is not an
/// oversight. The backend never returns one in a response body - it travels only as
/// an `HttpOnly` cookie - so a field for it would be permanently null and would
/// invite someone to try storing it.
class OrganizationSummary {
  const OrganizationSummary({
    required this.id,
    required this.name,
    required this.slug,
    this.logoUrl,
    required this.roleName,
    required this.roleSlug,
    required this.isOwner,
    required this.currency,
    required this.timezone,
    required this.fiscalYearStartMonth,
  });

  final String id;
  final String name;
  final String slug;
  final String? logoUrl;
  final String roleName;
  final String roleSlug;
  final bool isOwner;

  /// How this organization's figures and dates are rendered. On the session
  /// payload because every screen needs them before its first paint.
  final String currency;
  final String timezone;
  final int fiscalYearStartMonth;

  factory OrganizationSummary.fromJson(Json json) => OrganizationSummary(
    id: str(json, 'id'),
    name: str(json, 'name'),
    slug: str(json, 'slug'),
    logoUrl: strOrNull(json, 'logo_url'),
    roleName: str(json, 'role_name'),
    roleSlug: str(json, 'role_slug'),
    isOwner: boolOf(json, 'is_owner'),
    currency: strOrNull(json, 'currency') ?? 'INR',
    timezone: strOrNull(json, 'timezone') ?? 'Asia/Kolkata',
    fiscalYearStartMonth: intOf(json, 'fiscal_year_start_month', 4),
  );
}

class AuthenticatedUser {
  const AuthenticatedUser({
    required this.id,
    required this.email,
    required this.fullName,
    this.avatarUrl,
    required this.initials,
    required this.isEmailVerified,
    required this.isTwoFactorEnabled,
    required this.isSuperuser,
    required this.locale,
    required this.timezone,
    required this.theme,
    this.lastLoginAt,
    this.activeOrganization,
    required this.organizations,
    required this.permissions,
  });

  final String id;
  final String email;
  final String fullName;
  final String? avatarUrl;
  final String initials;
  final bool isEmailVerified;
  final bool isTwoFactorEnabled;
  final bool isSuperuser;
  final String locale;
  final String timezone;
  final String theme;
  final String? lastLoginAt;
  final OrganizationSummary? activeOrganization;
  final List<OrganizationSummary> organizations;

  /// Expanded permission slugs for the active organization.
  final List<String> permissions;

  String get firstName =>
      fullName.trim().split(RegExp(r'\s+')).firstOrNull ?? fullName;

  factory AuthenticatedUser.fromJson(Json json) {
    final Object? active = json['active_organization'];
    return AuthenticatedUser(
      id: str(json, 'id'),
      email: str(json, 'email'),
      fullName: str(json, 'full_name'),
      avatarUrl: strOrNull(json, 'avatar_url'),
      initials: strOrNull(json, 'initials') ?? '',
      isEmailVerified: boolOf(json, 'is_email_verified'),
      isTwoFactorEnabled: boolOf(json, 'is_two_factor_enabled'),
      isSuperuser: boolOf(json, 'is_superuser'),
      locale: strOrNull(json, 'locale') ?? 'en',
      timezone: strOrNull(json, 'timezone') ?? 'Asia/Kolkata',
      theme: strOrNull(json, 'theme') ?? 'system',
      lastLoginAt: strOrNull(json, 'last_login_at'),
      activeOrganization: active is Map
          ? OrganizationSummary.fromJson(active.cast<String, dynamic>())
          : null,
      organizations: listOf(
        json,
        'organizations',
        OrganizationSummary.fromJson,
      ),
      permissions: stringList(json, 'permissions'),
    );
  }
}

class TokenResponse {
  const TokenResponse({
    required this.accessToken,
    required this.expiresIn,
    required this.sessionId,
    required this.user,
    required this.mustChangePassword,
  });

  final String accessToken;
  final int expiresIn;
  final String sessionId;
  final AuthenticatedUser user;
  final bool mustChangePassword;

  factory TokenResponse.fromJson(Json json) => TokenResponse(
    accessToken: str(json, 'access_token'),
    expiresIn: intOf(json, 'expires_in'),
    sessionId: str(json, 'session_id'),
    user: AuthenticatedUser.fromJson(mapOf(json, 'user')),
    mustChangePassword: boolOf(json, 'must_change_password'),
  );
}

/// Returned by `/auth/login` when a second factor is outstanding.
class TwoFactorChallenge {
  const TwoFactorChallenge({required this.challengeId, required this.message});

  final String challengeId;
  final String message;

  factory TwoFactorChallenge.fromJson(Json json) => TwoFactorChallenge(
    challengeId: str(json, 'challenge_id'),
    message:
        strOrNull(json, 'message') ??
        'Enter the code from your authenticator app',
  );
}

/// `/auth/login` answers with one of two shapes.
///
/// A sealed pair rather than a nullable-fields object, so the call site has to
/// handle both - which is the point: forgetting the challenge branch would silently
/// break sign-in for every user with 2FA enabled.
sealed class LoginResult {
  const LoginResult();

  /// The discriminator the backend sets on the challenge shape.
  static LoginResult fromJson(Json json) {
    if (json['two_factor_required'] == true) {
      return LoginChallenge(TwoFactorChallenge.fromJson(json));
    }
    return LoginTokens(TokenResponse.fromJson(json));
  }
}

class LoginTokens extends LoginResult {
  const LoginTokens(this.tokens);
  final TokenResponse tokens;
}

class LoginChallenge extends LoginResult {
  const LoginChallenge(this.challenge);
  final TwoFactorChallenge challenge;
}

/// `/auth/magic-link/verify` answers with one of two shapes.
///
/// Sealed so the approval branch cannot be dropped: a link another client requested
/// signs *that* client in, and treating the approval as tokens would crash on a
/// missing `access_token`.
sealed class MagicLinkVerifyResult {
  const MagicLinkVerifyResult();

  static MagicLinkVerifyResult fromJson(Json json) {
    if (json['device_approved'] == true) {
      return MagicLinkDeviceApproved(
        userCode: str(json, 'user_code'),
        message:
            strOrNull(json, 'message') ??
            'Your app is signing in now. You can close this tab.',
      );
    }
    return MagicLinkSignedIn(TokenResponse.fromJson(json));
  }
}

class MagicLinkSignedIn extends MagicLinkVerifyResult {
  const MagicLinkSignedIn(this.tokens);
  final TokenResponse tokens;
}

/// The link belonged to another client. Nothing is signed in here.
class MagicLinkDeviceApproved extends MagicLinkVerifyResult {
  const MagicLinkDeviceApproved({
    required this.userCode,
    required this.message,
  });
  final String userCode;
  final String message;
}

/// A sign-in this app started but cannot finish on its own.
///
/// The emailed link opens in a browser, so the app holds [deviceHandle] and polls
/// until the link is opened - see `DeviceSignInStore` on the backend. [userCode] is
/// shown on screen so the person reading the mail can tell it refers to this device.
class DeviceSignInStarted {
  const DeviceSignInStarted({
    required this.deviceHandle,
    required this.userCode,
    required this.expiresInSeconds,
    required this.pollIntervalSeconds,
  });

  /// A credential. Held in memory for the life of the screen and never persisted.
  final String deviceHandle;
  final String userCode;
  final int expiresInSeconds;
  final int pollIntervalSeconds;

  factory DeviceSignInStarted.fromJson(Json json) => DeviceSignInStarted(
    deviceHandle: str(json, 'device_handle'),
    userCode: str(json, 'user_code'),
    expiresInSeconds: intOf(json, 'expires_in_seconds'),
    // Served by the backend rather than hard-coded, so the cadence can change
    // without shipping a new build. Floor of 1s in case of a bad value.
    pollIntervalSeconds: intOf(json, 'poll_interval_seconds').clamp(1, 60),
  );
}

/// `/auth/magic-link/device/poll` answers with one of three shapes.
///
/// Sealed for the same reason as [LoginResult]: the pending branch is the common
/// one, and a call site that forgot the challenge branch would strand every 2FA user
/// on a spinner that never resolves.
sealed class DeviceSignInPoll {
  const DeviceSignInPoll();

  static DeviceSignInPoll fromJson(Json json) {
    if (json['status'] == 'pending') return const DeviceSignInPending();
    if (json['two_factor_required'] == true) {
      return DeviceSignInChallenge(TwoFactorChallenge.fromJson(json));
    }
    return DeviceSignInTokens(TokenResponse.fromJson(json));
  }
}

class DeviceSignInPending extends DeviceSignInPoll {
  const DeviceSignInPending();
}

class DeviceSignInTokens extends DeviceSignInPoll {
  const DeviceSignInTokens(this.tokens);
  final TokenResponse tokens;
}

class DeviceSignInChallenge extends DeviceSignInPoll {
  const DeviceSignInChallenge(this.challenge);
  final TwoFactorChallenge challenge;
}

class RegisterResponse {
  const RegisterResponse({
    required this.email,
    required this.emailVerificationRequired,
    required this.message,
  });

  final String email;
  final bool emailVerificationRequired;
  final String message;

  factory RegisterResponse.fromJson(Json json) => RegisterResponse(
    email: str(json, 'email'),
    emailVerificationRequired: boolOf(json, 'email_verification_required'),
    message: strOrNull(json, 'message') ?? 'Account created.',
  );
}

/// Mirrors the backend's `describe_policy()`.
///
/// Served by `GET /auth/password-policy` so the hints on screen can never
/// contradict what the server enforces.
class PasswordPolicy {
  const PasswordPolicy({
    required this.minLength,
    required this.maxLength,
    required this.requiresUppercase,
    required this.requiresLowercase,
    required this.requiresSpecial,
    required this.requiresDigit,
    required this.specialCharacters,
    required this.rules,
  });

  final int minLength;
  final int maxLength;
  final bool requiresUppercase;
  final bool requiresLowercase;
  final bool requiresSpecial;
  final bool requiresDigit;
  final String specialCharacters;
  final List<String> rules;

  factory PasswordPolicy.fromJson(Json json) => PasswordPolicy(
    minLength: intOf(json, 'min_length', 6),
    maxLength: intOf(json, 'max_length', 128),
    requiresUppercase: boolOf(json, 'requires_uppercase'),
    requiresLowercase: boolOf(json, 'requires_lowercase'),
    requiresSpecial: boolOf(json, 'requires_special'),
    requiresDigit: boolOf(json, 'requires_digit'),
    specialCharacters: strOrNull(json, 'special_characters') ?? '',
    rules: stringList(json, 'rules'),
  );
}

class TwoFactorSetup {
  const TwoFactorSetup({required this.secret, required this.qrCode});

  final String secret;

  /// A PNG `data:` URI - inline so the secret never becomes a fetchable URL.
  final String qrCode;

  factory TwoFactorSetup.fromJson(Json json) =>
      TwoFactorSetup(secret: str(json, 'secret'), qrCode: str(json, 'qr_code'));
}

class TwoFactorEnableResponse {
  const TwoFactorEnableResponse({required this.recoveryCodes});

  /// Shown exactly once.
  final List<String> recoveryCodes;

  factory TwoFactorEnableResponse.fromJson(Json json) =>
      TwoFactorEnableResponse(
        recoveryCodes: stringList(json, 'recovery_codes'),
      );
}

class SessionInfo {
  const SessionInfo({
    required this.id,
    this.ipAddress,
    this.deviceLabel,
    this.deviceType,
    required this.loginMethod,
    required this.createdAt,
    this.lastUsedAt,
    required this.isCurrent,
  });

  final String id;
  final String? ipAddress;
  final String? deviceLabel;
  final String? deviceType;
  final String loginMethod;
  final String createdAt;
  final String? lastUsedAt;
  final bool isCurrent;

  factory SessionInfo.fromJson(Json json) => SessionInfo(
    id: str(json, 'id'),
    ipAddress: strOrNull(json, 'ip_address'),
    deviceLabel: strOrNull(json, 'device_label'),
    deviceType: strOrNull(json, 'device_type'),
    loginMethod: strOrNull(json, 'login_method') ?? 'password',
    createdAt: str(json, 'created_at'),
    lastUsedAt: strOrNull(json, 'last_used_at'),
    isCurrent: boolOf(json, 'is_current'),
  );
}

class UserProfile {
  const UserProfile({
    required this.id,
    required this.email,
    required this.fullName,
    this.phone,
    required this.isEmailVerified,
    required this.theme,
  });

  final String id;
  final String email;
  final String fullName;
  final String? phone;
  final bool isEmailVerified;
  final String theme;

  factory UserProfile.fromJson(Json json) => UserProfile(
    id: str(json, 'id'),
    email: str(json, 'email'),
    fullName: str(json, 'full_name'),
    phone: strOrNull(json, 'phone'),
    isEmailVerified: boolOf(json, 'is_email_verified'),
    theme: strOrNull(json, 'theme') ?? 'system',
  );
}

class UserStats {
  const UserStats({
    required this.activeSessions,
    required this.organizations,
    required this.recoveryCodesRemaining,
  });

  final int activeSessions;
  final int organizations;
  final int recoveryCodesRemaining;

  factory UserStats.fromJson(Json json) => UserStats(
    activeSessions: intOf(json, 'active_sessions'),
    organizations: intOf(json, 'organizations'),
    recoveryCodesRemaining: intOf(json, 'recovery_codes_remaining'),
  );
}
