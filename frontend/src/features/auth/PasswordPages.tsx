import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { ArrowLeft, Check, Eye, EyeOff, KeyRound, Mail } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { z } from 'zod';

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { authApi } from '@/features/auth/api';
import { AuthLayout } from '@/features/auth/AuthLayout';
import {
  passwordPlaceholder,
  summarisePolicy,
  usePasswordPolicy,
} from '@/features/auth/passwordPolicy';
import { ApiError } from '@/lib/api';

function reportableFailure(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;

  if (error.status === 429) {
    const raw = Number(error.details['retry_after_seconds']);
    if (!Number.isFinite(raw) || raw <= 0) {
      return 'Too many attempts. Please wait a moment and try again.';
    }
    const seconds = Math.ceil(raw);
    if (seconds < 60) {
      return `Too many attempts. Please try again in ${seconds} second${seconds === 1 ? '' : 's'}.`;
    }
    const minutes = Math.ceil(seconds / 60);
    return `Too many attempts. Please try again in ${minutes} minute${minutes === 1 ? '' : 's'}.`;
  }

  if (error.status === 0 || error.status >= 500) {
    return 'We could not reach the server just now. Please try again in a moment.';
  }
  return null;
}

// =============================================================================
// Forgot password
// =============================================================================
const forgotSchema = z.object({
  email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
});

export function ForgotPasswordPage() {
  const navigate = useNavigate();
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof forgotSchema>>({ defaultValues: { email: '' } });

  async function onSubmit(values: z.infer<typeof forgotSchema>) {
    const parsed = forgotSchema.safeParse(values);
    if (!parsed.success) {
      setError('email', { message: parsed.error.issues[0]?.message });
      return;
    }

    try {
      await authApi.forgotPassword(parsed.data.email);
    } catch (error) {
      const message = reportableFailure(error);
      if (message !== null) {
        setError('email', { message });
        toast.error(message);
        // Deliberately no navigation: the code was not sent, so the code-entry screen
        // has nothing to offer, and landing there is what made this failure invisible.
        return;
      }
      // Anything else falls through to the navigation below, so an unexpected status
      // cannot become an enumeration oracle by accident.
    }
    // Straight to code entry, always - carrying the address so it does not have
    // to be retyped. Advancing unconditionally is what keeps the flow silent
    // about whether that address has an account.
    void navigate({ to: '/reset-password', search: { email: parsed.data.email }, replace: true });
  }

  return (
    <AuthLayout
      title="Reset your password"
      subtitle="Enter your email and we will send you a 6-digit reset code."
      footer={
        <Link
          to="/login"
          className="text-content-muted hover:text-primary inline-flex items-center gap-1"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          Back to sign in
        </Link>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <Input
          label="Email"
          type="email"
          autoComplete="email"
          autoFocus
          placeholder="you@company.com"
          leftIcon={<Mail />}
          error={errors.email?.message}
          {...register('email')}
        />
        <Button type="submit" fullWidth size="lg" loading={isSubmitting}>
          Send reset code
        </Button>
      </form>
    </AuthLayout>
  );
}

// =============================================================================
// Reset password
// =============================================================================
const resetSchema = z
  .object({
    email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
    code: z.string().regex(/^\d{6}$/, 'Enter the 6-digit code from your email'),
    new_password: z.string().min(1, 'Password is required'),
    confirm_password: z.string().min(1, 'Confirm your password'),
  })
  .refine((data) => data.new_password === data.confirm_password, {
    message: 'Passwords do not match',
    path: ['confirm_password'],
  });

type ResetField = 'email' | 'code' | 'new_password' | 'confirm_password';

export function ResetPasswordPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const [showPassword, setShowPassword] = useState(false);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof resetSchema>>({
    // The address arrives from the request page so it does not have to be
    // retyped, but it stays editable: someone who opens this page from a
    // bookmark, or on a different device from the one that asked, has no other
    // way to supply it.
    defaultValues: {
      email: typeof search.email === 'string' ? search.email : '',
      code: '',
      new_password: '',
      confirm_password: '',
    },
  });

  const { data: policy } = usePasswordPolicy();

  async function onSubmit(values: z.infer<typeof resetSchema>) {
    const parsed = resetSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        setError(issue.path[0] as ResetField, { message: issue.message });
      }
      return;
    }

    try {
      await authApi.resetPassword({
        email: parsed.data.email,
        code: parsed.data.code,
        new_password: parsed.data.new_password,
      });
      toast.success('Password updated', {
        description: 'All other sessions were signed out.',
      });
      void navigate({ to: '/login', replace: true });
    } catch (error) {
      const transportProblem = reportableFailure(error);
      if (transportProblem !== null) {
        toast.error(transportProblem, { description: 'Your reset code is still valid.' });
        return;
      }

      if (error instanceof ApiError) {
        const fieldErrors = error.fieldErrors;
        if (fieldErrors['password']) {
          setError('new_password', { message: fieldErrors['password'] });
          return;
        }
        // A rejected code is the common failure, and attaching the message to
        // the password field would send the user to re-read the wrong input.
        if (error.code === 'invalid_token' || fieldErrors['code']) {
          setError('code', { message: fieldErrors['code'] ?? error.message });
          return;
        }
        setError('new_password', { message: error.message });
        return;
      }
      toast.error('Could not reset your password. Please try again.');
    }
  }

  return (
    <AuthLayout
      title="Choose a new password"
      subtitle="Enter the code we emailed you, then pick a new password."
      footer={
        <Link to="/forgot-password" className="text-primary font-medium hover:underline">
          Send a new code
        </Link>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <Input
          label="Email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          leftIcon={<Mail />}
          error={errors.email?.message}
          {...register('email')}
        />

        <Input
          label="Reset code"
          inputMode="numeric"
          autoComplete="one-time-code"
          autoFocus
          maxLength={6}
          placeholder="000000"
          className="text-center font-mono text-lg tracking-[0.3em]"
          leftIcon={<KeyRound />}
          hint={errors.code ? undefined : 'Expires 30 minutes after it was sent'}
          error={errors.code?.message}
          {...register('code')}
        />

        <Input
          label="New password"
          type={showPassword ? 'text' : 'password'}
          autoComplete="new-password"
          placeholder={passwordPlaceholder(policy)}
          hint={errors.new_password ? undefined : summarisePolicy(policy)}
          error={errors.new_password?.message}
          rightSlot={
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => setShowPassword((value) => !value)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              tabIndex={-1}
            >
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </Button>
          }
          {...register('new_password')}
        />

        <Input
          label="Confirm new password"
          type={showPassword ? 'text' : 'password'}
          autoComplete="new-password"
          placeholder="Re-enter your password"
          error={errors.confirm_password?.message}
          {...register('confirm_password')}
        />

        <Button type="submit" fullWidth size="lg" loading={isSubmitting}>
          Update password
        </Button>
      </form>
    </AuthLayout>
  );
}

// =============================================================================
// Verify email
// =============================================================================
export function VerifyEmailPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const [state, setState] = useState<'idle' | 'verifying' | 'done' | 'failed'>('idle');
  const [message, setMessage] = useState('');

  // Verification is confirmed by an explicit click, not automatically on mount.
  //
  // The token is single-use, and mail clients and link scanners routinely
  // prefetch URLs - an auto-verify would be consumed before the user ever sees
  // the page, leaving them with a dead link.
  async function verify() {
    if (!search.token) return;
    setState('verifying');
    try {
      await authApi.verifyEmail(search.token);
      setState('done');
      toast.success('Email verified');
      setTimeout(() => void navigate({ to: '/login', replace: true }), 1500);
    } catch (error) {
      setState('failed');
      setMessage(
        error instanceof ApiError ? error.message : 'This link is invalid or has expired.',
      );
    }
  }

  if (!search.token) {
    return (
      <AuthLayout
        title="Invalid verification link"
        subtitle="This link is missing its token."
        footer={
          <Link to="/login" className="text-primary font-medium hover:underline">
            Back to sign in
          </Link>
        }
      >
        <p className="text-content-muted text-center text-[13px]">
          Sign in and request a new verification email.
        </p>
      </AuthLayout>
    );
  }

  if (state === 'done') {
    return (
      <AuthLayout title="Email verified" subtitle="Taking you to sign in…">
        <div
          className="bg-success-bg text-success mx-auto flex h-12 w-12 items-center justify-center rounded-xl"
          aria-hidden
        >
          <Check className="h-6 w-6" />
        </div>
      </AuthLayout>
    );
  }

  if (state === 'failed') {
    return (
      <AuthLayout
        title="Verification failed"
        subtitle={message}
        footer={
          <Link to="/login" className="text-primary font-medium hover:underline">
            Back to sign in
          </Link>
        }
      >
        <p className="text-content-muted text-center text-[13px]">
          Verification links expire after 24 hours. Sign in to request a new one.
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Verify your email" subtitle="Confirm this address to activate your account.">
      <Button fullWidth size="lg" loading={state === 'verifying'} onClick={() => void verify()}>
        Verify my email address
      </Button>
    </AuthLayout>
  );
}
