/// The single client-side source of truth for password rules.
///
/// The rules themselves live on the server (`auth/password_policy.py`) and are fetched
/// from `GET /auth/password-policy`. Nothing here restates them.
///
/// This exists because the password field appears in three places - registration, reset,
/// and settings - and each one would otherwise hard-code its own hint text. Three copies
/// of a rule is three chances to contradict the server.
library;

import 'package:flutter/material.dart';

import '../../models/auth.dart';
import '../../theme/tokens.dart';

/// Used before the policy request resolves.
const int _fallbackMinLength = 6;

/// Placeholder text for a password input.
String passwordPlaceholder(PasswordPolicy? policy) =>
    'At least ${policy?.minLength ?? _fallbackMinLength} characters';

/// One-line summary of the policy, assembled from what the server reported.
///
/// Returns null while the policy is loading, so callers render nothing rather than a
/// guess that might be wrong.
String? summarisePolicy(PasswordPolicy? policy) {
  if (policy == null) return null;

  final List<String> needs = <String>[
    if (policy.requiresUppercase) 'an uppercase letter',
    if (policy.requiresLowercase) 'a lowercase letter',
    if (policy.requiresSpecial) 'a special character',
    if (policy.requiresDigit) 'a digit',
  ];

  final String base = '${policy.minLength}+ characters';
  return needs.isEmpty ? '$base.' : '$base, including ${needs.join(', ')}.';
}

class PasswordStrength {
  const PasswordStrength({required this.score, required this.label});

  final int score;
  final String label;
}

/// Lightweight strength meter for live feedback while typing.
///
/// Advisory only - the server is the authority, and it also applies checks this cannot (a
/// weak-password blocklist, and the user's own name and email). So a password showing
/// "Strong" here can still be rejected on submit; that is correct, and the server's
/// message is what the user sees.
///
/// Thresholds derive from the fetched policy rather than being hard-coded, so a policy
/// change cannot leave the meter disagreeing with what will be accepted.
///
/// Unicode property escapes rather than `[A-Z]`, to match the backend's Unicode-aware
/// `str.isupper()` / `str.islower()`.
PasswordStrength strengthOf(String password, PasswordPolicy? policy) {
  if (password.isEmpty) return const PasswordStrength(score: 0, label: '');

  final int minLength = policy?.minLength ?? _fallbackMinLength;
  final List<bool> checks = <bool>[
    password.length >= minLength,
    RegExp(r'\p{Lu}', unicode: true).hasMatch(password) &&
        RegExp(r'\p{Ll}', unicode: true).hasMatch(password),
    RegExp(r'[^\p{L}\p{N}]', unicode: true).hasMatch(password),
    // Length past the floor is where real resistance comes from, so the last bar rewards
    // going well beyond the minimum.
    password.length >= (minLength * 2 > 12 ? minLength * 2 : 12),
  ];

  final int score = checks.where((bool passed) => passed).length;
  if (score <= 1) return PasswordStrength(score: score, label: 'Weak');
  if (score == 2) return PasswordStrength(score: score, label: 'Fair');
  if (score == 3) return PasswordStrength(score: score, label: 'Good');
  return PasswordStrength(score: score, label: 'Strong');
}

/// Four bars and a word, shown while a new password is being typed.
class PasswordStrengthMeter extends StatelessWidget {
  const PasswordStrengthMeter({
    super.key,
    required this.password,
    required this.policy,
  });

  final String password;
  final PasswordPolicy? policy;

  @override
  Widget build(BuildContext context) {
    if (password.isEmpty) return const SizedBox.shrink();

    final AppTokens t = context.tokens;
    final PasswordStrength strength = strengthOf(password, policy);
    final Color tone = switch (strength.label) {
      'Weak' => t.danger,
      'Fair' => t.warning,
      'Good' => t.info,
      'Strong' => t.success,
      _ => t.border,
    };
    final String? summary = summarisePolicy(policy);

    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            spacing: 8,
            children: <Widget>[
              Expanded(
                child: Row(
                  spacing: 4,
                  children: <Widget>[
                    for (int index = 0; index < 4; index++)
                      Expanded(
                        child: AnimatedContainer(
                          duration: Motion.base,
                          height: 4,
                          decoration: BoxDecoration(
                            color: index < strength.score
                                ? tone
                                : t.surfaceSunken,
                            borderRadius: BorderRadius.circular(Radii.full),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
              SizedBox(
                width: 48,
                child: Text(
                  strength.label,
                  textAlign: TextAlign.right,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w500,
                    color: t.contentMuted,
                  ),
                ),
              ),
            ],
          ),
          if (summary != null) ...<Widget>[
            const SizedBox(height: 6),
            Text(
              summary,
              style: TextStyle(fontSize: 12, color: t.contentMuted),
            ),
          ],
        ],
      ),
    );
  }
}
