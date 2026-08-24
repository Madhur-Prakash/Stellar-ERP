import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { ArrowLeft, Check, KeyRound, Mail } from 'lucide-react';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { z } from 'zod';

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { authApi } from '@/features/auth/api';
import { AuthLayout } from '@/features/auth/AuthLayout';
import { useAuth } from '@/features/auth/AuthProvider';
import { ApiError } from '@/lib/api';
import { isDeviceApproved } from '@/types/api';

const emailSchema = z.string().min(1, 'Email is required').email('Enter a valid email address');

// =============================================================================
// Magic link - request
// =============================================================================
export function MagicLinkPage() {
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string>();
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);

  async function submit() {
    const parsed = emailSchema.safeParse(email);
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message);
      return;
    }
    setError(undefined);
    setSending(true);

    try {
      await authApi.requestMagicLink({ email: parsed.data });
    } catch {
      // Neutral outcome regardless - see ForgotPasswordPage.
    } finally {
      setSending(false);
      setSent(true);
    }
  }

  if (sent) {
    return (
      <AuthLayout
        title="Check your email"
        subtitle={
          <>
            If an account exists for <strong className="text-content">{email}</strong>, we have sent
            a sign-in link.
          </>
        }
        footer={
          <Link to="/login" className="text-primary font-medium hover:underline">
            Back to sign in
          </Link>
        }
      >
        <div className="space-y-4 text-center">
          <div
            className="bg-primary/10 text-primary mx-auto flex h-12 w-12 items-center justify-center rounded-xl"
            aria-hidden
          >
            <Mail className="h-6 w-6" />
          </div>
          <p className="text-content-muted text-[13px] leading-relaxed">
            The link expires in 15 minutes and can be used once.
          </p>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Sign in without a password"
      subtitle="We will email you a link that signs you in."
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
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        className="space-y-4"
        noValidate
      >
        <Input
          label="Email"
          type="email"
          autoComplete="email"
          autoFocus
          placeholder="you@company.com"
          leftIcon={<Mail />}
          error={error}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <Button type="submit" fullWidth size="lg" loading={sending}>
          Email me a sign-in link
        </Button>
      </form>
    </AuthLayout>
  );
}

// =============================================================================
// Magic link - consume
// =============================================================================
export function MagicLinkVerifyPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const { applySession } = useAuth();
  const [state, setState] = useState<'idle' | 'verifying' | 'approved' | 'failed'>('idle');
  const [message, setMessage] = useState('');
  const [userCode, setUserCode] = useState('');

  async function consume() {
    if (!search.token) return;
    setState('verifying');
    try {
      const result = await authApi.verifyMagicLink(search.token);

      // The link belonged to an app, so this browser gets nothing - by design. See
      // MagicLinkDeviceApproved: whoever asked for the link is who signs in.
      if (isDeviceApproved(result)) {
        setUserCode(result.user_code);
        setMessage(result.message);
        setState('approved');
        return;
      }

      applySession(result);
      toast.success(`Welcome back, ${result.user.full_name.split(' ')[0]}`);
      void navigate({ to: '/', replace: true });
    } catch (error) {
      setState('failed');
      if (error instanceof ApiError && error.code === 'two_factor_required') {
        setMessage('This account uses two-factor authentication. Sign in with your password.');
      } else {
        setMessage(
          error instanceof ApiError ? error.message : 'This link is invalid or has expired.',
        );
      }
    }
  }

  if (!search.token) {
    return (
      <AuthLayout
        title="Invalid sign-in link"
        subtitle="This link is missing its token."
        footer={
          <Link to="/magic-link" className="text-primary font-medium hover:underline">
            Request a new link
          </Link>
        }
      >
        <p className="text-content-muted text-center text-[13px]">
          Sign-in links expire after 15 minutes.
        </p>
      </AuthLayout>
    );
  }

  if (state === 'approved') {
    return (
      <AuthLayout
        title="Your app is signing in"
        subtitle={message}
        footer={
          <Link to="/login" className="text-primary font-medium hover:underline">
            Sign in here instead
          </Link>
        }
      >
        <div className="space-y-4 text-center">
          <div
            className="bg-success-bg text-success mx-auto flex h-12 w-12 items-center justify-center rounded-xl"
            aria-hidden
          >
            <Check className="h-6 w-6" />
          </div>
          <p className="text-content-muted text-[13px] leading-relaxed">
            The app should be showing this code. If it is not, close this tab and change your
            password - the link was not requested by you.
          </p>
          <div className="bg-primary/10 text-primary rounded-xl px-6 py-4 text-center font-mono text-3xl font-semibold tracking-[0.3em]">
            {userCode}
          </div>
        </div>
      </AuthLayout>
    );
  }

  if (state === 'failed') {
    return (
      <AuthLayout
        title="Could not sign you in"
        subtitle={message}
        footer={
          <Link to="/login" className="text-primary font-medium hover:underline">
            Back to sign in
          </Link>
        }
      >
        <Link to="/magic-link" className="block">
          <Button variant="secondary" fullWidth>
            Request a new link
          </Button>
        </Link>
      </AuthLayout>
    );
  }

  // Requires a click rather than firing on mount: the token is single-use, and
  // mail-client link prefetching would otherwise burn it before the user arrives.
  return (
    <AuthLayout
      title="Sign in to Stellar ERP"
      subtitle="Confirm to continue with your sign-in link."
    >
      <Button fullWidth size="lg" loading={state === 'verifying'} onClick={() => void consume()}>
        Continue to Stellar ERP
      </Button>
    </AuthLayout>
  );
}

// =============================================================================
// Email OTP
// =============================================================================
export function OtpPage() {
  const navigate = useNavigate();
  const { applySession } = useAuth();

  const [step, setStep] = useState<'email' | 'code'>('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  // Rate-limits the resend button client-side, so an impatient user does not
  // burn their server-side attempt budget by hammering it.
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((value) => value - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  async function requestCode() {
    const parsed = emailSchema.safeParse(email);
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message);
      return;
    }
    setError(undefined);
    setBusy(true);

    try {
      await authApi.requestOtp(parsed.data);
    } catch {
      // Neutral response regardless of whether the account exists.
    } finally {
      setBusy(false);
      setStep('code');
      setCooldown(30);
    }
  }

  async function verifyCode() {
    if (code.trim().length < 6) {
      setError('Enter the 6-digit code');
      return;
    }
    setError(undefined);
    setBusy(true);

    try {
      const tokens = await authApi.verifyOtp({ email, code: code.trim() });
      applySession(tokens);
      toast.success('Signed in');
      void navigate({ to: '/', replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === 'two_factor_required') {
        setError('This account uses two-factor authentication. Sign in with your password.');
      } else {
        setError(err instanceof ApiError ? err.message : 'That code did not work.');
      }
    } finally {
      setBusy(false);
    }
  }

  if (step === 'code') {
    return (
      <AuthLayout
        title="Enter your code"
        subtitle={
          <>
            We sent a 6-digit code to <strong className="text-content">{email}</strong>.
          </>
        }
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void verifyCode();
          }}
          className="space-y-4"
          noValidate
        >
          <Input
            label="Sign-in code"
            value={code}
            onChange={(event) => setCode(event.target.value.replace(/\D/g, ''))}
            error={error}
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            maxLength={6}
            placeholder="000000"
            className="text-center font-mono text-lg tracking-[0.3em]"
            leftIcon={<KeyRound />}
          />

          <Button type="submit" fullWidth size="lg" loading={busy}>
            Sign in
          </Button>

          <div className="flex items-center justify-between text-[13px]">
            <button
              type="button"
              className="text-content-muted hover:text-content"
              onClick={() => {
                setStep('email');
                setCode('');
                setError(undefined);
              }}
            >
              Use a different email
            </button>
            <button
              type="button"
              disabled={cooldown > 0}
              className="text-primary disabled:text-content-muted hover:underline disabled:no-underline"
              onClick={() => void requestCode()}
            >
              {cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend code'}
            </button>
          </div>
        </form>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Sign in with a code"
      subtitle="We will email you a 6-digit code."
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
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void requestCode();
        }}
        className="space-y-4"
        noValidate
      >
        <Input
          label="Email"
          type="email"
          autoComplete="email"
          autoFocus
          placeholder="you@company.com"
          leftIcon={<Mail />}
          error={error}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <Button type="submit" fullWidth size="lg" loading={busy}>
          Send me a code
        </Button>
      </form>
    </AuthLayout>
  );
}
