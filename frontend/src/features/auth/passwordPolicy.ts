/**
 * The single client-side source of truth for password rules.
 *
 * The rules themselves live on the server
 * (`backend/app/modules/auth/password_policy.py`) and are fetched from
 * `GET /auth/password-policy`. Nothing here restates them.
 *
 * This module exists because the password field appears in three places -
 * registration, reset, and settings - and each one previously hard-coded its own
 * hint text. Three copies of a rule is three chances to contradict the server.
 */
import { useQuery } from '@tanstack/react-query';

import { authApi } from '@/features/auth/api';
import type { PasswordPolicy } from '@/types/api';

/** Used when the policy request has not resolved yet. */
const FALLBACK_MIN_LENGTH = 6;

/**
 * Fetch the enforced policy.
 *
 * Cached for an hour: the policy changes at deploy time, not at runtime, so
 * re-requesting it per keystroke or per mount is waste.
 */
export function usePasswordPolicy() {
  return useQuery({
    queryKey: ['password-policy'],
    queryFn: authApi.passwordPolicy,
    staleTime: 60 * 60 * 1000,
  });
}

/** Placeholder text for a password input. */
export function passwordPlaceholder(policy: PasswordPolicy | undefined): string {
  return `At least ${policy?.min_length ?? FALLBACK_MIN_LENGTH} characters`;
}

/**
 * One-line summary of the policy, assembled from what the server reported.
 *
 * Returns `undefined` while the policy is loading, so callers render nothing
 * rather than a guess that might be wrong.
 */
export function summarisePolicy(policy: PasswordPolicy | undefined): string | undefined {
  if (!policy) return undefined;

  const needs: string[] = [];
  if (policy.requires_uppercase) needs.push('an uppercase letter');
  if (policy.requires_lowercase) needs.push('a lowercase letter');
  if (policy.requires_special) needs.push('a special character');
  if (policy.requires_digit) needs.push('a digit');

  const base = `${policy.min_length}+ characters`;
  return needs.length > 0 ? `${base}, including ${needs.join(', ')}.` : `${base}.`;
}

export interface PasswordStrength {
  score: number;
  label: string;
  tone: string;
}

/**
 * Lightweight strength meter for live feedback while typing.
 *
 * Advisory only - the server is the authority, and it also applies checks this
 * cannot (a weak-password blocklist, and the user's own name and email). So a
 * password showing "Strong" here can still be rejected on submit; that is
 * correct, and the server's message is what the user sees.
 *
 * Thresholds derive from the fetched policy rather than being hard-coded, so a
 * policy change cannot leave the meter disagreeing with what will be accepted.
 *
 * Unicode property escapes (`\p{Lu}`) rather than `[A-Z]`, to match the
 * backend's Unicode-aware `str.isupper()` / `str.islower()`.
 */
export function strengthOf(password: string, policy: PasswordPolicy | undefined): PasswordStrength {
  const minLength = policy?.min_length ?? FALLBACK_MIN_LENGTH;

  const checks = [
    password.length >= minLength,
    /\p{Lu}/u.test(password) && /\p{Ll}/u.test(password),
    /[^\p{L}\p{N}]/u.test(password),
    // Length past the floor is where real resistance comes from, so the last
    // bar rewards going well beyond the minimum.
    password.length >= Math.max(minLength * 2, 12),
  ];
  const score = checks.filter(Boolean).length;

  if (password.length === 0) return { score: 0, label: '', tone: 'bg-border' };
  if (score <= 1) return { score, label: 'Weak', tone: 'bg-danger' };
  if (score === 2) return { score, label: 'Fair', tone: 'bg-warning' };
  if (score === 3) return { score, label: 'Good', tone: 'bg-info' };
  return { score, label: 'Strong', tone: 'bg-success' };
}
