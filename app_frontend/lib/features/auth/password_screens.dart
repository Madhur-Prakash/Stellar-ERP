import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/api_error.dart';
import '../../models/auth.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_input.dart';
import '../../widgets/toast.dart';
import 'auth_layout.dart';
import 'password_policy.dart';

// =============================================================================
// Forgot password
// =============================================================================
class ForgotPasswordScreen extends ConsumerStatefulWidget {
  const ForgotPasswordScreen({super.key});

  @override
  ConsumerState<ForgotPasswordScreen> createState() =>
      _ForgotPasswordScreenState();
}

class _ForgotPasswordScreenState extends ConsumerState<ForgotPasswordScreen> {
  final TextEditingController _email = TextEditingController();
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final String email = _email.text.trim();
    if (email.isEmpty || !email.contains('@')) {
      setState(() => _error = 'Enter a valid email address');
      return;
    }

    setState(() {
      _error = null;
      _submitting = true;
    });

    try {
      await ref.read(authApiProvider).forgotPassword(email);
    } catch (error) {
      // Rate limits and server failures are reported; nothing else is.
      //
      // The distinction is what keeps the enumeration guarantee intact. The API answers
      // 200 identically whether or not the address has an account, so a 429 or a 5xx
      // cannot be *about* the address - a 429 is about how often this client has asked,
      // and a 5xx is about the server. Neither reveals anything, so hiding them buys no
      // privacy and costs the user everything: this used to swallow all of it and advance
      // anyway, leaving someone waiting on the next screen for an email that was never
      // sent, with a rate-limit warning visible only in the server log.
      //
      // Anything else still falls through to the navigation below, so an unexpected
      // status cannot become an oracle by accident.
      final ApiError apiError = ApiError.from(error);
      if (apiError.isRateLimited || apiError.isRetryable) {
        if (!mounted) return;
        final String message = apiError.isRateLimited
            ? apiError.rateLimitMessage
            : 'We could not send the code just now. Please try again in a moment.';
        setState(() {
          _error = message;
          _submitting = false;
        });
        // Deliberately no navigation: the code was not sent, so the code-entry screen
        // has nothing to offer, and landing there is what made this failure invisible.
        return;
      }
    } finally {
      if (mounted) setState(() => _submitting = false);
    }

    if (!mounted) return;
    // Straight to code entry, carrying the address so it does not have to be
    // retyped. Advancing unconditionally is what keeps the flow silent about
    // whether that address has an account.
    context.go('/reset-password?email=${Uri.encodeQueryComponent(email)}');
  }

  @override
  Widget build(BuildContext context) {
    return AuthLayout(
      title: 'Reset your password',
      subtitle: const Text(
        'Enter your email and we will send you a 6-digit reset code.',
      ),
      footer: const BackToSignIn(),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        spacing: 16,
        children: <Widget>[
          AppInput(
            label: 'Email',
            controller: _email,
            placeholder: 'you@company.com',
            leftIcon: LucideIcons.mail,
            error: _error,
            autofocus: true,
            keyboardType: TextInputType.emailAddress,
            onSubmitted: (_) => _submit(),
          ),
          AppButton(
            onPressed: _submit,
            loading: _submitting,
            fullWidth: true,
            size: AppButtonSize.lg,
            label: 'Send reset code',
          ),
        ],
      ),
    );
  }
}

// =============================================================================
// Reset password
// =============================================================================
class ResetPasswordScreen extends ConsumerStatefulWidget {
  const ResetPasswordScreen({super.key, this.email});

  /// Prefills the address the code was sent to. A convenience only - the code is
  /// what authorises the change, and it is never carried in the URL.
  final String? email;

  @override
  ConsumerState<ResetPasswordScreen> createState() =>
      _ResetPasswordScreenState();
}

class _ResetPasswordScreenState extends ConsumerState<ResetPasswordScreen> {
  late final TextEditingController _email = TextEditingController(
    text: widget.email ?? '',
  );
  final TextEditingController _code = TextEditingController();
  final TextEditingController _password = TextEditingController();
  final TextEditingController _confirm = TextEditingController();
  bool _showPassword = false;
  bool _submitting = false;
  String? _emailError;
  String? _codeError;
  String? _passwordError;
  String? _confirmError;

  @override
  void dispose() {
    _email.dispose();
    _code.dispose();
    _password.dispose();
    _confirm.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final String email = _email.text.trim();
    final String code = _code.text.trim();

    setState(() {
      _emailError = email.isEmpty || !email.contains('@')
          ? 'Enter a valid email address'
          : null;
      _codeError = RegExp(r'^\d{6}$').hasMatch(code)
          ? null
          : 'Enter the 6-digit code from your email';
      _passwordError = _password.text.isEmpty ? 'Password is required' : null;
      _confirmError = _confirm.text.isEmpty
          ? 'Confirm your password'
          : _confirm.text != _password.text
          ? 'Passwords do not match'
          : null;
    });
    if (_emailError != null ||
        _codeError != null ||
        _passwordError != null ||
        _confirmError != null) {
      return;
    }

    setState(() => _submitting = true);
    try {
      await ref
          .read(authApiProvider)
          .resetPassword(email: email, code: code, newPassword: _password.text);
      if (!mounted) return;
      context.toastSuccess(
        'Password updated',
        description: 'All other sessions were signed out.',
      );
      context.go('/login');
    } catch (error) {
      if (!mounted) return;
      final ApiError apiError = ApiError.from(error);

      // Rate limiting first, and as a toast rather than a field error. It is not a
      // complaint about any input - the code and the password may both be perfect - so
      // pinning it under one of them tells the user to go and fix something that is not
      // broken. This previously fell through to the catch-all below and appeared under
      // "New password" as "Too many requests. Slow down.", with no wait time.
      if (apiError.isRateLimited) {
        context.toastError(
          'Too many attempts',
          description: apiError.rateLimitMessage,
        );
        return;
      }
      if (apiError.isRetryable) {
        context.toastError(
          'Could not reach the server',
          description:
              'Your code is still valid. Please try again in a moment.',
        );
        return;
      }

      final String? passwordProblem = apiError.fieldErrors['password'];
      setState(() {
        if (passwordProblem != null) {
          _passwordError = passwordProblem;
        } else if (apiError.code == 'invalid_token' ||
            apiError.fieldErrors.containsKey('code')) {
          // A rejected code is the common failure; attaching it to the password
          // field would send the user to re-read the wrong input.
          _codeError = apiError.fieldErrors['code'] ?? apiError.message;
        } else {
          _passwordError = apiError.message;
        }
      });
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final PasswordPolicy? policy = ref
        .watch(passwordPolicyProvider)
        .valueOrNull;

    return AuthLayout(
      title: 'Choose a new password',
      subtitle: const Text(
        'Enter the code we emailed you, then pick a new password.',
      ),
      footer: AuthFooterPrompt(
        prompt: '',
        actionLabel: 'Send a new code',
        onAction: () => context.go('/forgot-password'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        spacing: 16,
        children: <Widget>[
          AppInput(
            label: 'Email',
            controller: _email,
            placeholder: 'you@company.com',
            leftIcon: LucideIcons.mail,
            error: _emailError,
            keyboardType: TextInputType.emailAddress,
          ),
          AppInput(
            label: 'Reset code',
            controller: _code,
            placeholder: '000000',
            leftIcon: LucideIcons.keyRound,
            error: _codeError,
            hint: _codeError == null
                ? 'Expires 30 minutes after it was sent'
                : null,
            autofocus: true,
            keyboardType: TextInputType.number,
            maxLength: 6,
          ),
          AppInput(
            label: 'New password',
            controller: _password,
            placeholder: passwordPlaceholder(policy),
            obscureText: !_showPassword,
            error: _passwordError,
            hint: _passwordError == null ? summarisePolicy(policy) : null,
            onChanged: (_) => setState(() {}),
            rightSlot: Padding(
              padding: const EdgeInsets.only(right: 4),
              child: AppIconButton(
                icon: _showPassword ? LucideIcons.eyeOff : LucideIcons.eye,
                tooltip: _showPassword ? 'Hide password' : 'Show password',
                size: 15,
                onPressed: () => setState(() => _showPassword = !_showPassword),
              ),
            ),
          ),
          AppInput(
            label: 'Confirm new password',
            controller: _confirm,
            placeholder: 'Re-enter your password',
            obscureText: !_showPassword,
            error: _confirmError,
            onSubmitted: (_) => _submit(),
          ),
          AppButton(
            onPressed: _submit,
            loading: _submitting,
            fullWidth: true,
            size: AppButtonSize.lg,
            label: 'Update password',
          ),
        ],
      ),
    );
  }
}

// =============================================================================
// Verify email
// =============================================================================
class VerifyEmailScreen extends ConsumerStatefulWidget {
  const VerifyEmailScreen({super.key, this.token});

  final String? token;

  @override
  ConsumerState<VerifyEmailScreen> createState() => _VerifyEmailScreenState();
}

class _VerifyEmailScreenState extends ConsumerState<VerifyEmailScreen> {
  _VerifyState _state = _VerifyState.idle;
  String _message = '';

  /// Verification is confirmed by an explicit press, not automatically on mount.
  ///
  /// The token is single-use, and mail clients and link scanners routinely prefetch URLs -
  /// an auto-verify would be consumed before the user ever saw the screen, leaving them
  /// with a dead link.
  Future<void> _verify() async {
    setState(() => _state = _VerifyState.verifying);
    try {
      await ref.read(authApiProvider).verifyEmail(widget.token!);
      if (!mounted) return;
      setState(() => _state = _VerifyState.done);
      context.toastSuccess('Email verified');
      await Future<void>.delayed(const Duration(milliseconds: 1500));
      if (mounted) context.go('/login');
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _state = _VerifyState.failed;
        _message = ApiError.from(error).message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    if (widget.token == null || widget.token!.isEmpty) {
      return AuthLayout(
        title: 'Invalid verification link',
        subtitle: const Text('This link is missing its token.'),
        footer: const BackToSignIn(),
        child: Text(
          'Sign in and request a new verification email.',
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 13, color: t.contentMuted),
        ),
      );
    }

    if (_state == _VerifyState.done) {
      return AuthLayout(
        title: 'Email verified',
        subtitle: const Text('Taking you to sign in…'),
        child: Container(
          width: 48,
          height: 48,
          decoration: BoxDecoration(
            color: t.successBg,
            borderRadius: BorderRadius.circular(Radii.xl),
          ),
          alignment: Alignment.center,
          child: Icon(LucideIcons.check, size: 24, color: t.success),
        ),
      );
    }

    if (_state == _VerifyState.failed) {
      return AuthLayout(
        title: 'Verification failed',
        subtitle: Text(_message),
        footer: const BackToSignIn(),
        child: Text(
          'Verification links expire after 24 hours. Sign in to request a new one.',
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 13, color: t.contentMuted),
        ),
      );
    }

    return AuthLayout(
      title: 'Verify your email',
      subtitle: const Text('Confirm this address to activate your account.'),
      child: AppButton(
        onPressed: _verify,
        loading: _state == _VerifyState.verifying,
        fullWidth: true,
        size: AppButtonSize.lg,
        label: 'Verify my email address',
      ),
    );
  }
}

enum _VerifyState { idle, verifying, done, failed }
