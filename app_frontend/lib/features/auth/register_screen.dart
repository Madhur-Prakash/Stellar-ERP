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

class RegisterScreen extends ConsumerStatefulWidget {
  const RegisterScreen({super.key, this.invitationToken});

  /// Present when arriving from an invitation link.
  final String? invitationToken;

  @override
  ConsumerState<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends ConsumerState<RegisterScreen> {
  final TextEditingController _fullName = TextEditingController();
  final TextEditingController _email = TextEditingController();
  final TextEditingController _password = TextEditingController();
  final TextEditingController _organization = TextEditingController();

  bool _showPassword = false;
  bool _submitting = false;
  bool _resending = false;
  String? _nameError;
  String? _emailError;
  String? _passwordError;

  /// Set once the account exists and verification is outstanding.
  String? _registeredEmail;

  @override
  void dispose() {
    _fullName.dispose();
    _email.dispose();
    _password.dispose();
    _organization.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final String name = _fullName.text.trim();
    final String email = _email.text.trim();
    final String password = _password.text;

    setState(() {
      _nameError = name.length < 2 ? 'Enter your full name' : null;
      _emailError = email.isEmpty
          ? 'Email is required'
          : !email.contains('@')
          ? 'Enter a valid email address'
          : null;
      _passwordError = password.isEmpty ? 'Password is required' : null;
    });
    if (_nameError != null || _emailError != null || _passwordError != null) {
      return;
    }

    setState(() => _submitting = true);
    try {
      final RegisterResponse result = await ref
          .read(authApiProvider)
          .register(
            email: email,
            password: password,
            fullName: name,
            organizationName: _organization.text.trim(),
            invitationToken: widget.invitationToken,
          );

      if (!mounted) return;
      if (result.emailVerificationRequired) {
        setState(() => _registeredEmail = result.email);
      } else {
        context.toastSuccess('Account created. You can sign in now.');
        context.go('/login');
      }
    } catch (error) {
      if (!mounted) return;
      final ApiError apiError = ApiError.from(error);

      if (apiError.code == 'email_taken') {
        setState(
          () => _emailError = 'An account with this email already exists',
        );
        return;
      }
      final Map<String, String> fields = apiError.fieldErrors;
      if (fields.isNotEmpty) {
        setState(() {
          _nameError = fields['full_name'];
          _emailError = fields['email'];
          _passwordError = fields['password'];
        });
        return;
      }
      context.toastError(apiError.message);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    // Fetched rather than hard-coded so the displayed rules always match what the server
    // will actually accept.
    final PasswordPolicy? policy = ref
        .watch(passwordPolicyProvider)
        .valueOrNull;

    if (_registeredEmail != null) {
      return AuthLayout(
        title: 'Check your email',
        subtitle: Text.rich(
          TextSpan(
            children: <InlineSpan>[
              const TextSpan(text: 'We sent a verification link to '),
              TextSpan(
                text: _registeredEmail,
                style: TextStyle(color: t.content, fontWeight: FontWeight.w600),
              ),
              const TextSpan(text: '. Open it to activate your account.'),
            ],
          ),
        ),
        footer: AuthFooterPrompt(
          prompt: '',
          actionLabel: 'Back to sign in',
          onAction: () => context.go('/login'),
        ),
        child: Column(
          spacing: 16,
          children: <Widget>[
            Container(
              width: 48,
              height: 48,
              decoration: BoxDecoration(
                color: t.successBg,
                borderRadius: BorderRadius.circular(Radii.xl),
              ),
              alignment: Alignment.center,
              child: Icon(LucideIcons.check, size: 24, color: t.success),
            ),
            Text(
              'The link expires in 24 hours. Check your spam folder if it has not '
              'arrived in a few minutes.',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 13,
                color: t.contentMuted,
                height: 1.6,
              ),
            ),
            // Sending mail is the slowest thing on this screen and the least visible - an
            // API call whose whole effect happens in someone else's inbox. Without the
            // spinner the button looks inert, so it gets pressed again, and the second
            // press spends one of the three sends a minute the server allows.
            //
            // The failure was worse than invisible before this: nothing caught the throw,
            // so a refused resend - the rate limit being the likely one - reported
            // nothing at all to the person waiting on the email.
            AppButton(
              onPressed: _resending
                  ? null
                  : () async {
                      setState(() => _resending = true);
                      try {
                        await ref
                            .read(authApiProvider)
                            .resendVerification(_registeredEmail!);
                        if (context.mounted) {
                          context.toastSuccess(
                            'Verification email sent again',
                            description:
                                'Check $_registeredEmail, including the spam folder.',
                          );
                        }
                      } catch (error) {
                        if (context.mounted) {
                          context.toastApiError(
                            error,
                            'Could not resend the email. Please try again.',
                          );
                        }
                      } finally {
                        if (mounted) setState(() => _resending = false);
                      }
                    },
              loading: _resending,
              variant: AppButtonVariant.secondary,
              fullWidth: true,
              label: 'Resend verification email',
            ),
          ],
        ),
      );
    }

    final bool invited = widget.invitationToken != null;

    return AuthLayout(
      title: invited ? 'Accept your invitation' : 'Create your account',
      subtitle: Text(
        invited
            ? 'Set up your account to join the organization.'
            : 'Start running your business on Stellar ERP.',
      ),
      footer: AuthFooterPrompt(
        prompt: 'Already have an account?',
        actionLabel: 'Sign in',
        onAction: () => context.go('/login'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          AppInput(
            label: 'Full name',
            controller: _fullName,
            placeholder: 'Jhon Doe',
            leftIcon: LucideIcons.user,
            error: _nameError,
            autofocus: true,
            autofillHints: const <String>[AutofillHints.name],
          ),
          const SizedBox(height: 16),
          AppInput(
            label: 'Work email',
            controller: _email,
            placeholder: 'you@company.com',
            leftIcon: LucideIcons.mail,
            error: _emailError,
            keyboardType: TextInputType.emailAddress,
            autofillHints: const <String>[AutofillHints.email],
          ),
          const SizedBox(height: 16),
          AppInput(
            label: 'Password',
            controller: _password,
            placeholder: passwordPlaceholder(policy),
            obscureText: !_showPassword,
            error: _passwordError,
            autofillHints: const <String>[AutofillHints.newPassword],
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
          PasswordStrengthMeter(password: _password.text, policy: policy),

          // Hidden when arriving from an invitation: the organization is already decided,
          // and offering to create another would be confusing.
          if (!invited) ...<Widget>[
            const SizedBox(height: 16),
            AppInput(
              label: 'Company name',
              controller: _organization,
              placeholder: 'Acme Trading Co',
              leftIcon: LucideIcons.building2,
              hint: 'Optional. You can create or join an organization later.',
            ),
          ],

          const SizedBox(height: 16),
          AppButton(
            onPressed: _submit,
            loading: _submitting,
            fullWidth: true,
            size: AppButtonSize.lg,
            label: 'Create account',
          ),
          const SizedBox(height: 16),
          Text(
            'By creating an account you agree to our Terms of Service and Privacy Policy.',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 12, color: t.contentMuted, height: 1.6),
          ),
        ],
      ),
    );
  }
}
