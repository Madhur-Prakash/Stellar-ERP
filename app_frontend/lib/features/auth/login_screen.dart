import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/api_error.dart';
import '../../models/auth.dart';
import '../../state/auth_controller.dart';
import '../../state/providers.dart';
import '../../theme/app_theme.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_input.dart';
import '../../widgets/primitives.dart';
import '../../widgets/toast.dart';
import 'auth_layout.dart';

/// Sign-in.
///
/// Two steps in one screen: the password form, and - only when the server says a second
/// factor is outstanding - the code form. A separate route for the second step would put
/// the challenge id in the address, and it is a short-lived credential.
class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key, this.redirectTo});

  /// Where to go after signing in. Set by the router's guard when an unauthenticated
  /// user asked for a protected screen.
  final String? redirectTo;

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final TextEditingController _email = TextEditingController();
  final TextEditingController _password = TextEditingController();
  final TextEditingController _code = TextEditingController();

  bool _showPassword = false;
  bool _submitting = false;

  /// "Keep me signed in", **on by default here where the web app leaves it off**.
  ///
  /// It is not a cosmetic default: the backend reads it as a session lifetime, giving 7 days
  /// without it and `REFRESH_TOKEN_TTL_DAYS` with it. Off is the right conservative choice
  /// in a browser, which may be a shared or public machine and whose cookie jar is not the
  /// app's to reason about. An installed desktop client is neither - it lives in a per-user
  /// application-support directory on a machine somebody chose to install it on, and being
  /// asked to sign in weekly to software running on your own desk is friction with nothing
  /// bought by it.
  ///
  /// Still a checkbox, and still honest about what it does, so a shared workstation can turn
  /// it off. What it cannot do is extend the window indefinitely: the backend deliberately
  /// preserves a session's original expiry across rotation, so re-authentication comes round
  /// eventually however this is set.
  bool _rememberMe = true;

  String? _emailError;
  String? _passwordError;
  String? _codeError;

  /// Set once the password is accepted and a TOTP code is required.
  String? _challengeId;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _code.dispose();
    super.dispose();
  }

  void _goHome() => context.go(_target);

  String get _target {
    final String? raw = widget.redirectTo;
    if (raw == null || raw.isEmpty) return '/';
    final String decoded = Uri.decodeComponent(raw);
    // Only an in-app path is honoured. A decoded value that is not one would be an open
    // redirect if it were followed - which matters less on desktop than on the web, but
    // it costs one check to be sure.
    return decoded.startsWith('/') ? decoded : '/';
  }

  Future<void> _submit() async {
    final String email = _email.text.trim();
    final String password = _password.text;

    setState(() {
      // Length is *not* validated here. The server owns the policy, and telling someone
      // their existing password is "too short" at the sign-in screen is both wrong and
      // alarming.
      _emailError = email.isEmpty
          ? 'Email is required'
          : !email.contains('@')
          ? 'Enter a valid email address'
          : null;
      _passwordError = password.isEmpty ? 'Password is required' : null;
    });
    if (_emailError != null || _passwordError != null) return;

    setState(() => _submitting = true);
    try {
      final LoginResult result = await ref
          .read(authApiProvider)
          .login(email: email, password: password, rememberMe: _rememberMe);

      switch (result) {
        case LoginChallenge(challenge: final TwoFactorChallenge challenge):
          setState(() => _challengeId = challenge.challengeId);
        case LoginTokens(tokens: final TokenResponse tokens):
          ref.read(authControllerProvider.notifier).applySession(tokens);
          if (!mounted) return;
          context.toastSuccess('Welcome back, ${tokens.user.firstName}');
          _goHome();
      }
    } catch (error) {
      if (mounted) _handleLoginError(error);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  void _handleLoginError(Object error) {
    final ApiError apiError = ApiError.from(error);

    // An unverified account is a recoverable state, not a credential failure - route the
    // user to the fix rather than showing a dead end.
    if (apiError.code == 'email_not_verified') {
      context.toastAction(
        'Verify your email to continue',
        description: 'We can send you another verification link.',
        actionLabel: 'Resend',
        onAction: () async {
          await ref
              .read(authApiProvider)
              .resendVerification(_email.text.trim());
          if (mounted) context.toastSuccess('Verification email sent');
        },
      );
      return;
    }

    if (apiError.code == 'account_locked') {
      context.toastError(
        'Account temporarily locked',
        description: apiError.message,
      );
      return;
    }

    if (apiError.isValidation && apiError.fieldErrors.isNotEmpty) {
      setState(() {
        _emailError = apiError.fieldErrors['email'];
        _passwordError = apiError.fieldErrors['password'];
      });
      return;
    }

    // Credential failures are attached to the form rather than shown as a toast: the
    // error belongs next to the fields the user has to correct.
    setState(() => _passwordError = apiError.message);
  }

  Future<void> _submitTwoFactor() async {
    final String code = _code.text.trim();
    if (code.length < 6) {
      setState(() => _codeError = 'Enter the 6-digit code');
      return;
    }

    setState(() {
      _codeError = null;
      _submitting = true;
    });

    try {
      final TokenResponse tokens = await ref
          .read(authApiProvider)
          .loginTwoFactor(
            challengeId: _challengeId!,
            code: code,
            rememberMe: _rememberMe,
          );
      ref.read(authControllerProvider.notifier).applySession(tokens);
      if (!mounted) return;
      context.toastSuccess('Signed in');
      _goHome();
    } catch (error) {
      if (mounted) {
        setState(() => _codeError = ApiError.from(error).message);
      }
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_challengeId != null) return _buildTwoFactorStep();
    return _buildPasswordStep();
  }

  // ---- Second factor step -------------------------------------------------
  Widget _buildTwoFactorStep() {
    return AuthLayout(
      title: 'Two-factor authentication',
      subtitle: const Text(
        'Enter the 6-digit code from your authenticator app.',
      ),
      child: Column(
        spacing: 16,
        children: <Widget>[
          AppInput(
            label: 'Authentication code',
            controller: _code,
            error: _codeError,
            hint: 'You can also use one of your recovery codes.',
            placeholder: '000000',
            autofocus: true,
            maxLength: 12,
            textAlign: TextAlign.center,
            leftIcon: LucideIcons.keyRound,
            textStyle: monoStyle(fontSize: 18).copyWith(letterSpacing: 5),
            onSubmitted: (_) => _submitTwoFactor(),
          ),
          AppButton(
            onPressed: _submitTwoFactor,
            loading: _submitting,
            fullWidth: true,
            size: AppButtonSize.lg,
            label: 'Verify and sign in',
          ),
          AppButton(
            onPressed: () => setState(() {
              _challengeId = null;
              _code.clear();
              _codeError = null;
            }),
            variant: AppButtonVariant.ghost,
            fullWidth: true,
            label: 'Back to sign in',
          ),
        ],
      ),
    );
  }

  // ---- Password step ------------------------------------------------------
  Widget _buildPasswordStep() {
    final AppTokens t = context.tokens;

    return AuthLayout(
      title: 'Sign in to Personal ERP',
      subtitle: const Text('Welcome back. Enter your details to continue.'),
      footer: AuthFooterPrompt(
        prompt: 'New to Personal ERP?',
        actionLabel: 'Create an account',
        onAction: () => context.go('/register'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          AppInput(
            label: 'Email',
            controller: _email,
            placeholder: 'you@company.com',
            leftIcon: LucideIcons.mail,
            error: _emailError,
            autofocus: true,
            keyboardType: TextInputType.emailAddress,
            autofillHints: const <String>[AutofillHints.email],
          ),
          const SizedBox(height: 16),
          AppInput(
            label: 'Password',
            controller: _password,
            placeholder: 'Enter your password',
            obscureText: !_showPassword,
            error: _passwordError,
            autofillHints: const <String>[AutofillHints.password],
            onSubmitted: (_) => _submit(),
            rightSlot: Padding(
              padding: const EdgeInsets.only(right: 4),
              child: AppIconButton(
                icon: _showPassword ? LucideIcons.eyeOff : LucideIcons.eye,
                // Password visibility is a genuine accessibility aid, and the control
                // must say which state it will move to.
                tooltip: _showPassword ? 'Hide password' : 'Show password',
                size: 15,
                onPressed: () => setState(() => _showPassword = !_showPassword),
              ),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            children: <Widget>[
              CheckRow(
                value: _rememberMe,
                onChanged: (bool next) => setState(() => _rememberMe = next),
                label: 'Keep me signed in',
              ),
              const Spacer(),
              AppTextLink(
                label: 'Forgot password?',
                fontWeight: FontWeight.w400,
                colour: t.contentMuted,
                // `text-content-muted hover:text-primary` on the web.
                hoverColour: t.primary,
                onTap: () => context.go('/forgot-password'),
              ),
            ],
          ),
          const SizedBox(height: 16),
          AppButton(
            onPressed: _submit,
            loading: _submitting,
            fullWidth: true,
            size: AppButtonSize.lg,
            label: 'Sign in',
          ),

          const AuthDivider(),

          // Passwordless is offered as an equal path, not a fallback: for an accountant
          // who signs in monthly, a magic link is often the faster route. So the two
          // share one variant and one row - a `secondary` beside a `ghost` reads as
          // "this one, or that lesser thing", which is not the offer. Labels are short
          // because they now sit two-up in a narrow card.
          Row(
            spacing: 8,
            children: <Widget>[
              Expanded(
                child: AppButton(
                  onPressed: () => context.go('/magic-link'),
                  variant: AppButtonVariant.secondary,
                  fullWidth: true,
                  leftIcon: LucideIcons.mail,
                  label: 'Sign-in link',
                ),
              ),
              Expanded(
                child: AppButton(
                  onPressed: () => context.go('/otp'),
                  variant: AppButtonVariant.secondary,
                  fullWidth: true,
                  // `key`, not `keyRound` - that one is the 2FA code field's icon, and
                  // these are different flows.
                  leftIcon: LucideIcons.lockKeyhole,
                  label: 'One-time code',
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
