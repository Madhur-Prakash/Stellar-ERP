import 'dart:async';

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

// =============================================================================
// Magic link - request
// =============================================================================
class MagicLinkScreen extends ConsumerStatefulWidget {
  const MagicLinkScreen({super.key});

  @override
  ConsumerState<MagicLinkScreen> createState() => _MagicLinkScreenState();
}

class _MagicLinkScreenState extends ConsumerState<MagicLinkScreen> {
  final TextEditingController _email = TextEditingController();
  final TextEditingController _twoFactorCode = TextEditingController();

  bool _sending = false;
  String? _error;

  /// Non-null once the link has been sent: what this app polls with, plus the code
  /// it shows so the email can be matched to this screen.
  DeviceSignInStarted? _started;
  Timer? _poll;
  bool _expired = false;

  /// Set when the account has 2FA. The link proved the mailbox; the code is owed
  /// here, in the app that is about to get the session.
  String? _challengeId;
  bool _completingTwoFactor = false;
  String? _twoFactorError;

  @override
  void dispose() {
    _poll?.cancel();
    _email.dispose();
    _twoFactorCode.dispose();
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
      _sending = true;
    });

    try {
      // Not `requestMagicLink`: that one sends a link this app never hears about
      // again, which is why sending from here used to sign in only the browser.
      final DeviceSignInStarted started = await ref
          .read(authApiProvider)
          .startDeviceSignIn(email);
      if (!mounted) return;
      setState(() => _started = started);
      _startPolling(started);
    } catch (_) {
      // Neutral outcome regardless - see ForgotPasswordScreen. There is nothing to
      // poll, so the screen stays on the form rather than lying about a sent link.
      if (mounted) {
        setState(() => _error = 'Could not send the link. Try again.');
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  void _startPolling(DeviceSignInStarted started) {
    _poll?.cancel();
    _poll = Timer.periodic(
      Duration(seconds: started.pollIntervalSeconds),
      (_) => _tick(started),
    );
  }

  Future<void> _tick(DeviceSignInStarted started) async {
    try {
      final DeviceSignInPoll result = await ref
          .read(authApiProvider)
          .pollDeviceSignIn(started.deviceHandle);
      if (!mounted) return;

      switch (result) {
        case DeviceSignInPending():
          return; // Nobody has opened the link yet.
        case DeviceSignInTokens(tokens: final TokenResponse tokens):
          _poll?.cancel();
          ref.read(authControllerProvider.notifier).applySession(tokens);
          context.toastSuccess('Welcome back, ${tokens.user.firstName}');
          context.go('/');
        case DeviceSignInChallenge(
          challenge: final TwoFactorChallenge challenge,
        ):
          _poll?.cancel();
          setState(() => _challengeId = challenge.challengeId);
      }
    } catch (error) {
      if (!mounted) return;
      // A 401 means the handle is spent or the window closed - the signal to stop,
      // not an error to keep retrying against.
      _poll?.cancel();
      setState(() => _expired = true);
    }
  }

  Future<void> _completeTwoFactor() async {
    final String code = _twoFactorCode.text.trim();
    if (code.isEmpty) {
      setState(
        () => _twoFactorError = 'Enter the code from your authenticator app',
      );
      return;
    }

    setState(() {
      _twoFactorError = null;
      _completingTwoFactor = true;
    });

    try {
      final TokenResponse tokens = await ref
          .read(authApiProvider)
          .loginTwoFactor(challengeId: _challengeId!, code: code);
      if (!mounted) return;
      ref.read(authControllerProvider.notifier).applySession(tokens);
      context.toastSuccess('Welcome back, ${tokens.user.firstName}');
      context.go('/');
    } catch (error) {
      if (!mounted) return;
      setState(() => _twoFactorError = ApiError.from(error).message);
    } finally {
      if (mounted) setState(() => _completingTwoFactor = false);
    }
  }

  void _restart() {
    _poll?.cancel();
    setState(() {
      _started = null;
      _expired = false;
      _challengeId = null;
      _twoFactorError = null;
      _twoFactorCode.clear();
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_challengeId != null) return _buildTwoFactorStep();
    if (_started != null) return _buildWaitingStep(_started!);
    return _buildEmailStep();
  }

  // ---------------------------------------------------------------------------
  Widget _buildEmailStep() {
    return AuthLayout(
      title: 'Sign in without a password',
      subtitle: const Text(
        'We will email you a link. Opening it signs in this app.',
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
            loading: _sending,
            fullWidth: true,
            size: AppButtonSize.lg,
            label: 'Email me a sign-in link',
          ),
        ],
      ),
    );
  }

  Widget _buildWaitingStep(DeviceSignInStarted started) {
    final AppTokens t = context.tokens;

    if (_expired) {
      return AuthLayout(
        title: 'That link has expired',
        subtitle: const Text('Sign-in links last 15 minutes and work once.'),
        footer: const BackToSignIn(),
        child: AppButton(
          onPressed: _restart,
          variant: AppButtonVariant.secondary,
          fullWidth: true,
          label: 'Send a new link',
        ),
      );
    }

    return AuthLayout(
      title: 'Check your email',
      subtitle: Text.rich(
        TextSpan(
          children: <InlineSpan>[
            const TextSpan(text: 'If an account exists for '),
            TextSpan(
              text: _email.text.trim(),
              style: TextStyle(color: t.content, fontWeight: FontWeight.w600),
            ),
            const TextSpan(
              text:
                  ', we have sent a sign-in link. Open it and this app signs in '
                  'by itself - you can leave this window as it is.',
            ),
          ],
        ),
      ),
      footer: AuthFooterPrompt(
        prompt: '',
        actionLabel: 'Back to sign in',
        onAction: () {
          _poll?.cancel();
          context.go('/login');
        },
      ),
      child: Column(
        spacing: 16,
        children: <Widget>[
          Text(
            'The email shows this code. Only open the link if it matches.',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 13, color: t.contentMuted, height: 1.6),
          ),
          Container(
            padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 24),
            decoration: BoxDecoration(
              color: t.primary.withValues(alpha: 0.10),
              borderRadius: BorderRadius.circular(Radii.xl),
            ),
            child: Text(
              started.userCode,
              style: TextStyle(
                fontSize: 30,
                fontWeight: FontWeight.w600,
                letterSpacing: 8,
                color: t.primary,
                fontFeatures: const <FontFeature>[FontFeature.tabularFigures()],
              ),
            ),
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            spacing: 10,
            children: <Widget>[
              SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: t.contentMuted,
                ),
              ),
              Text(
                'Waiting for you to open it',
                style: TextStyle(fontSize: 13, color: t.contentMuted),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildTwoFactorStep() {
    return AuthLayout(
      title: 'One more step',
      subtitle: const Text(
        'The link checked out. Enter the code from your authenticator app to '
        'finish signing in here.',
      ),
      footer: AuthFooterPrompt(
        prompt: '',
        actionLabel: 'Start again',
        onAction: _restart,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        spacing: 16,
        children: <Widget>[
          AppInput(
            label: 'Authentication code',
            controller: _twoFactorCode,
            placeholder: '000000',
            leftIcon: LucideIcons.keyRound,
            error: _twoFactorError,
            autofocus: true,
            keyboardType: TextInputType.number,
            onSubmitted: (_) => _completeTwoFactor(),
          ),
          AppButton(
            onPressed: _completeTwoFactor,
            loading: _completingTwoFactor,
            fullWidth: true,
            size: AppButtonSize.lg,
            label: 'Sign in',
          ),
        ],
      ),
    );
  }
}

// =============================================================================
// Magic link - consume
// =============================================================================
class MagicLinkVerifyScreen extends ConsumerStatefulWidget {
  const MagicLinkVerifyScreen({super.key, this.token});

  final String? token;

  @override
  ConsumerState<MagicLinkVerifyScreen> createState() =>
      _MagicLinkVerifyScreenState();
}

class _MagicLinkVerifyScreenState extends ConsumerState<MagicLinkVerifyScreen> {
  bool _verifying = false;
  bool _failed = false;
  String _message = '';

  /// Set when the link turned out to belong to a *different* client, which had
  /// already been waiting on it. Nothing is signed in here.
  String? _approvedUserCode;

  Future<void> _consume() async {
    setState(() => _verifying = true);
    try {
      final MagicLinkVerifyResult result = await ref
          .read(authApiProvider)
          .verifyMagicLink(widget.token!);
      if (!mounted) return;

      switch (result) {
        case MagicLinkSignedIn(tokens: final TokenResponse tokens):
          ref.read(authControllerProvider.notifier).applySession(tokens);
          context.toastSuccess('Welcome back, ${tokens.user.firstName}');
          context.go('/');
        case MagicLinkDeviceApproved(
          userCode: final String code,
          message: final String message,
        ):
          setState(() {
            _verifying = false;
            _approvedUserCode = code;
            _message = message;
          });
      }
    } catch (error) {
      if (!mounted) return;
      final ApiError apiError = ApiError.from(error);
      setState(() {
        _verifying = false;
        _failed = true;
        _message = apiError.code == 'two_factor_required'
            ? 'This account uses two-factor authentication. Sign in with your password.'
            : apiError.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    if (widget.token == null || widget.token!.isEmpty) {
      return AuthLayout(
        title: 'Invalid sign-in link',
        subtitle: const Text('This link is missing its token.'),
        footer: AuthFooterPrompt(
          prompt: '',
          actionLabel: 'Request a new link',
          onAction: () => context.go('/magic-link'),
        ),
        child: Text(
          'Sign-in links expire after 15 minutes.',
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 13, color: t.contentMuted),
        ),
      );
    }

    if (_failed) {
      return AuthLayout(
        title: 'Could not sign you in',
        subtitle: Text(_message),
        footer: const BackToSignIn(),
        child: AppButton(
          onPressed: () => context.go('/magic-link'),
          variant: AppButtonVariant.secondary,
          fullWidth: true,
          label: 'Request a new link',
        ),
      );
    }

    // The link was another client's. It has been approved and that client is signing
    // itself in - nothing happens here, deliberately.
    if (_approvedUserCode != null) {
      return AuthLayout(
        title: 'That app is signing in',
        subtitle: Text(_message),
        footer: AuthFooterPrompt(
          prompt: '',
          actionLabel: 'Sign in here instead',
          onAction: () => context.go('/login'),
        ),
        child: Column(
          spacing: 16,
          children: <Widget>[
            Text(
              'It should be showing this code. If it is not, change your password - '
              'the link was not requested by you.',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 13,
                color: t.contentMuted,
                height: 1.6,
              ),
            ),
            Container(
              padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 24),
              decoration: BoxDecoration(
                color: t.primary.withValues(alpha: 0.10),
                borderRadius: BorderRadius.circular(Radii.xl),
              ),
              child: Text(
                _approvedUserCode!,
                style: TextStyle(
                  fontSize: 30,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 8,
                  color: t.primary,
                ),
              ),
            ),
          ],
        ),
      );
    }

    // Requires a press rather than firing on mount: the token is single-use, and
    // mail-client link prefetching would otherwise burn it before the user arrives.
    return AuthLayout(
      title: 'Sign in to Personal ERP',
      subtitle: const Text('Confirm to continue with your sign-in link.'),
      child: AppButton(
        onPressed: _consume,
        loading: _verifying,
        fullWidth: true,
        size: AppButtonSize.lg,
        label: 'Continue to Personal ERP',
      ),
    );
  }
}

// =============================================================================
// Email OTP
// =============================================================================
class OtpScreen extends ConsumerStatefulWidget {
  const OtpScreen({super.key});

  @override
  ConsumerState<OtpScreen> createState() => _OtpScreenState();
}

class _OtpScreenState extends ConsumerState<OtpScreen> {
  final TextEditingController _email = TextEditingController();
  final TextEditingController _code = TextEditingController();

  bool _codeStep = false;
  bool _busy = false;
  String? _error;
  int _cooldown = 0;
  Timer? _timer;

  @override
  void dispose() {
    _timer?.cancel();
    _email.dispose();
    _code.dispose();
    super.dispose();
  }

  /// Rate-limits the resend button locally, so an impatient user does not burn their
  /// server-side attempt budget by hammering it.
  void _startCooldown() {
    _timer?.cancel();
    setState(() => _cooldown = 30);
    _timer = Timer.periodic(const Duration(seconds: 1), (Timer timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      setState(() => _cooldown--);
      if (_cooldown <= 0) timer.cancel();
    });
  }

  Future<void> _requestCode() async {
    final String email = _email.text.trim();
    if (email.isEmpty || !email.contains('@')) {
      setState(() => _error = 'Enter a valid email address');
      return;
    }

    setState(() {
      _error = null;
      _busy = true;
    });

    try {
      await ref.read(authApiProvider).requestOtp(email);
    } catch (_) {
      // Neutral response regardless of whether the account exists.
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _codeStep = true;
        });
        _startCooldown();
      }
    }
  }

  Future<void> _verifyCode() async {
    final String code = _code.text.trim();
    if (code.length < 6) {
      setState(() => _error = 'Enter the 6-digit code');
      return;
    }

    setState(() {
      _error = null;
      _busy = true;
    });

    try {
      final TokenResponse tokens = await ref
          .read(authApiProvider)
          .verifyOtp(email: _email.text.trim(), code: code);
      ref.read(authControllerProvider.notifier).applySession(tokens);
      if (!mounted) return;
      context.toastSuccess('Signed in');
      context.go('/');
    } catch (error) {
      if (!mounted) return;
      final ApiError apiError = ApiError.from(error);
      setState(() {
        _busy = false;
        _error = apiError.code == 'two_factor_required'
            ? 'This account uses two-factor authentication. Sign in with your password.'
            : apiError.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    if (_codeStep) {
      return AuthLayout(
        title: 'Enter your code',
        subtitle: Text.rich(
          TextSpan(
            children: <InlineSpan>[
              const TextSpan(text: 'We sent a 6-digit code to '),
              TextSpan(
                text: _email.text.trim(),
                style: TextStyle(color: t.content, fontWeight: FontWeight.w600),
              ),
              const TextSpan(text: '.'),
            ],
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          spacing: 16,
          children: <Widget>[
            AppInput(
              label: 'Sign-in code',
              controller: _code,
              placeholder: '000000',
              error: _error,
              autofocus: true,
              maxLength: 6,
              textAlign: TextAlign.center,
              leftIcon: LucideIcons.keyRound,
              keyboardType: TextInputType.number,
              textStyle: monoStyle(fontSize: 18).copyWith(letterSpacing: 5),
              onSubmitted: (_) => _verifyCode(),
            ),
            AppButton(
              onPressed: _verifyCode,
              loading: _busy,
              fullWidth: true,
              size: AppButtonSize.lg,
              label: 'Sign in',
            ),
            Row(
              children: <Widget>[
                AppTextLink(
                  label: 'Use a different email',
                  fontWeight: FontWeight.w400,
                  colour: t.contentMuted,
                  // `text-content-muted hover:text-content` on the web.
                  hoverColour: t.content,
                  onTap: () => setState(() {
                    _codeStep = false;
                    _code.clear();
                    _error = null;
                  }),
                ),
                const Spacer(),
                if (_cooldown > 0)
                  Text(
                    'Resend in ${_cooldown}s',
                    style: TextStyle(fontSize: 13, color: t.contentMuted),
                  )
                else
                  AppTextLink(label: 'Resend code', onTap: _requestCode),
              ],
            ),
          ],
        ),
      );
    }

    return AuthLayout(
      title: 'Sign in with a code',
      subtitle: const Text('We will email you a 6-digit code.'),
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
            onSubmitted: (_) => _requestCode(),
          ),
          AppButton(
            onPressed: _requestCode,
            loading: _busy,
            fullWidth: true,
            size: AppButtonSize.lg,
            label: 'Send me a code',
          ),
        ],
      ),
    );
  }
}
