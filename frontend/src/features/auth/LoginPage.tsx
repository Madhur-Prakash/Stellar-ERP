import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { Eye, EyeOff, KeyRound, LockKeyholeIcon, Mail } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { z } from 'zod';

import { Button, buttonClasses } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { authApi } from '@/features/auth/api';
import { AuthLayout } from '@/features/auth/AuthLayout';
import { useAuth } from '@/features/auth/AuthProvider';
import { ApiError } from '@/lib/api';
import { isTwoFactorChallenge } from '@/types/api';

const schema = z.object({
  email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
  // Length is *not* validated here. The server owns the policy, and telling
  // someone their existing password is "too short" at the login screen is both
  // wrong and alarming.
  password: z.string().min(1, 'Password is required'),
  remember_me: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

export function LoginPage() {
  const navigate = useNavigate();
  const { applySession } = useAuth();
  const search = useSearch({ strict: false });

  const [showPassword, setShowPassword] = useState(false);
  const [twoFactor, setTwoFactor] = useState<{ challengeId: string; rememberMe: boolean } | null>(
    null,
  );
  const [code, setCode] = useState('');
  const [codeError, setCodeError] = useState<string>();
  const [verifying, setVerifying] = useState(false);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    defaultValues: { email: '', password: '', remember_me: false },
  });

  async function onSubmit(values: FormValues) {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        setError(issue.path[0] as keyof FormValues, { message: issue.message });
      }
      return;
    }

    try {
      const result = await authApi.login(parsed.data);

      if (isTwoFactorChallenge(result)) {
        setTwoFactor({ challengeId: result.challenge_id, rememberMe: parsed.data.remember_me });
        return;
      }

      applySession(result);
      toast.success(`Welcome back, ${result.user.full_name.split(' ')[0]}`);
      void navigate({ to: search.redirect ?? '/', replace: true });
    } catch (error) {
      handleLoginError(error);
    }
  }

  function handleLoginError(error: unknown) {
    if (!(error instanceof ApiError)) {
      toast.error('Something went wrong. Please try again.');
      return;
    }

    // An unverified account is a recoverable state, not a credential failure -
    // route the user to the fix rather than showing a dead end.
    if (error.code === 'email_not_verified') {
      toast.error('Verify your email to continue', {
        description: 'We can send you another verification link.',
        action: {
          label: 'Resend',
          onClick: () => {
            void authApi
              .resendVerification(
                (document.getElementById('login-email') as HTMLInputElement)?.value,
              )
              .then(() => toast.success('Verification email sent'));
          },
        },
      });
      return;
    }

    if (error.code === 'account_locked') {
      toast.error('Account temporarily locked', { description: error.message });
      return;
    }

    if (error.isValidation) {
      for (const [field, message] of Object.entries(error.fieldErrors)) {
        setError(field as keyof FormValues, { message });
      }
      return;
    }

    // Credential failures are attached to the form rather than shown as a toast:
    // the error belongs next to the fields the user has to correct.
    setError('password', { message: error.message });
  }

  async function submitTwoFactor() {
    if (!twoFactor) return;
    setCodeError(undefined);

    if (code.trim().length < 6) {
      setCodeError('Enter the 6-digit code');
      return;
    }

    setVerifying(true);
    try {
      const tokens = await authApi.loginTwoFactor({
        challenge_id: twoFactor.challengeId,
        code: code.trim(),
        remember_me: twoFactor.rememberMe,
      });
      applySession(tokens);
      toast.success('Signed in');
      void navigate({ to: search.redirect ?? '/', replace: true });
    } catch (error) {
      setCodeError(
        error instanceof ApiError ? error.message : 'That code did not work. Try again.',
      );
    } finally {
      setVerifying(false);
    }
  }

  // ---- Second factor step -------------------------------------------------
  if (twoFactor) {
    return (
      <AuthLayout
        title="Two-factor authentication"
        subtitle="Enter the 6-digit code from your authenticator app."
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submitTwoFactor();
          }}
          className="space-y-4"
        >
          <Input
            label="Authentication code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            error={codeError}
            hint="You can also use one of your recovery codes."
            inputMode="numeric"
            autoComplete="one-time-code"
            // Focused on mount so the code can be typed immediately.
            autoFocus
            maxLength={12}
            placeholder="000000"
            className="text-center font-mono text-lg tracking-[0.3em]"
            leftIcon={<KeyRound />}
          />

          <Button type="submit" fullWidth size="lg" loading={verifying}>
            Verify and sign in
          </Button>

          <Button
            type="button"
            variant="ghost"
            fullWidth
            onClick={() => {
              setTwoFactor(null);
              setCode('');
              setCodeError(undefined);
            }}
          >
            Back to sign in
          </Button>
        </form>
      </AuthLayout>
    );
  }

  // ---- Password step ------------------------------------------------------
  return (
    <AuthLayout
      title="Sign in to Personal ERP"
      subtitle="Welcome back. Enter your details to continue."
      footer={
        <span className="text-content-muted">
          New to Personal ERP?{' '}
          <Link to="/register" className="text-primary font-medium hover:underline">
            Create an account
          </Link>
        </span>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <Input
          id="login-email"
          label="Email"
          type="email"
          autoComplete="email"
          autoFocus
          placeholder="you@company.com"
          leftIcon={<Mail />}
          error={errors.email?.message}
          {...register('email')}
        />

        <div>
          <Input
            label="Password"
            type={showPassword ? 'text' : 'password'}
            autoComplete="current-password"
            placeholder="Enter your password"
            error={errors.password?.message}
            rightSlot={
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={() => setShowPassword((value) => !value)}
                // Password visibility is a genuine accessibility aid, and the
                // control must say which state it will move to.
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                tabIndex={-1}
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            }
            {...register('password')}
          />

          <div className="mt-2 flex items-center justify-between">
            <label className="text-content-secondary flex cursor-pointer items-center gap-2 text-[13px] select-none">
              <input
                type="checkbox"
                className="border-border text-primary focus:ring-ring/30 h-3.5 w-3.5 rounded"
                {...register('remember_me')}
              />
              Keep me signed in
            </label>
            <Link
              to="/forgot-password"
              className="text-content-muted hover:text-primary text-[13px]"
            >
              Forgot password?
            </Link>
          </div>
        </div>

        <Button type="submit" fullWidth size="lg" loading={isSubmitting}>
          Sign in
        </Button>
      </form>

      <div className="my-5 flex items-center gap-3">
        <span className="bg-border h-px flex-1" />
        <span className="text-content-muted text-[11px] font-medium tracking-wide uppercase">
          or
        </span>
        <span className="bg-border h-px flex-1" />
      </div>

      {/* Passwordless is offered as an equal path, not a fallback: for an
          accountant who signs in monthly, a magic link is often the faster route.
          So the two share one variant and one row. A `secondary` next to a `ghost`
          reads as "this one, or that lesser thing", which is not the offer.

          Rendered as links, not buttons - they navigate, so they must be `<a>`.
          Labels are short because both now share a 400px card and `BASE` sets
          `whitespace-nowrap`: a full sentence would overflow rather than wrap. Each
          destination explains itself on arrival.

          No `mr-2` on the icons - the `md` size already contributes `gap-2`, and
          doubling it wastes width the two-up row does not have. */}
      <div className="grid grid-cols-2 gap-2">
        <Link to="/magic-link" className={buttonClasses('secondary', 'md', 'w-full')}>
          <Mail className="h-4 w-4" aria-hidden />
          Sign-in link
        </Link>
        <Link to="/otp" className={buttonClasses('secondary', 'md', 'w-full')}>
          {/* `Key`, not `KeyRound` - that one is the 2FA code field's icon above, and
              these are different flows. */}
          <LockKeyholeIcon className="h-4 w-4" aria-hidden />
          One-time code
        </Link>
      </div>
    </AuthLayout>
  );
}
