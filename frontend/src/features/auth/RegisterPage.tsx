import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { Building2, Check, Eye, EyeOff, Mail, User } from 'lucide-react';
import { useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { toast } from 'sonner';
import { z } from 'zod';

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { authApi } from '@/features/auth/api';
import { AuthLayout } from '@/features/auth/AuthLayout';
import {
  passwordPlaceholder,
  strengthOf,
  summarisePolicy,
  usePasswordPolicy,
} from '@/features/auth/passwordPolicy';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';

const schema = z.object({
  full_name: z.string().trim().min(2, 'Enter your full name').max(200),
  email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
  organization_name: z.string().trim().max(200).optional(),
});

type FormValues = z.infer<typeof schema>;

export function RegisterPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const [showPassword, setShowPassword] = useState(false);
  const [done, setDone] = useState<{ email: string } | null>(null);
  const [resending, setResending] = useState(false);

  // Fetched rather than hard-coded so the displayed rules always match what the
  // server will actually accept.
  const { data: policy } = usePasswordPolicy();

  const {
    register,
    handleSubmit,
    control,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    defaultValues: { full_name: '', email: '', password: '', organization_name: '' },
  });

  // `useWatch` rather than `watch()`: the latter returns a fresh function on
  // every render and cannot be safely memoized, which the React Compiler and
  // the react-hooks lint rule both flag.
  const password = useWatch({ control, name: 'password' }) ?? '';
  const strength = strengthOf(password, policy);

  async function onSubmit(values: FormValues) {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        setError(issue.path[0] as keyof FormValues, { message: issue.message });
      }
      return;
    }

    try {
      const result = await authApi.register({
        email: parsed.data.email,
        password: parsed.data.password,
        full_name: parsed.data.full_name,
        // Send only one of these - the server rejects both together.
        ...(search.invitation
          ? { invitation_token: search.invitation }
          : parsed.data.organization_name
            ? { organization_name: parsed.data.organization_name }
            : {}),
      });

      if (result.email_verification_required) {
        setDone({ email: result.email });
      } else {
        toast.success('Account created. You can sign in now.');
        void navigate({ to: '/login', replace: true });
      }
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.code === 'email_taken') {
          setError('email', { message: 'An account with this email already exists' });
          return;
        }
        const fieldErrors = error.fieldErrors;
        if (Object.keys(fieldErrors).length > 0) {
          for (const [field, message] of Object.entries(fieldErrors)) {
            setError(field as keyof FormValues, { message });
          }
          return;
        }
        toast.error(error.message);
        return;
      }
      toast.error('Could not create your account. Please try again.');
    }
  }

  if (done) {
    return (
      <AuthLayout
        title="Check your email"
        subtitle={
          <>
            We sent a verification link to <strong className="text-content">{done.email}</strong>.
            Open it to activate your account.
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
            className="bg-success-bg text-success mx-auto flex h-12 w-12 items-center justify-center rounded-xl"
            aria-hidden
          >
            <Check className="h-6 w-6" />
          </div>
          <p className="text-content-muted text-[13px] leading-relaxed">
            The link expires in 24 hours. Check your spam folder if it has not arrived in a few
            minutes.
          </p>
          {/* Sending mail is the slowest thing on this screen and the least visible - it
              is an API call whose whole effect happens in someone else's inbox. Without a
              spinner the button looks inert, so it gets pressed again, and the second
              press spends one of the three sends a minute the server allows.

              The failure was worse than invisible before this: a bare `.then()` meant a
              rejected promise had no handler at all, so a refused resend - the rate limit
              being the likely one - reported nothing whatsoever. */}
          <Button
            variant="secondary"
            fullWidth
            loading={resending}
            onClick={() => {
              setResending(true);
              void authApi
                .resendVerification(done.email)
                .then(() =>
                  toast.success('Verification email sent again', {
                    description: `Check ${done.email}, including the spam folder.`,
                  }),
                )
                .catch((error: unknown) => {
                  if (error instanceof ApiError) {
                    // The mail-sending budget is the tightest in the application, so a
                    // refusal here is far more often "too soon" than "broken" - and the
                    // one thing that answer needs is *how long*.
                    toast.error(error.isRateLimited ? error.rateLimitMessage : error.message);
                    return;
                  }
                  toast.error('Could not resend the email. Please try again.');
                })
                .finally(() => setResending(false));
            }}
          >
            Resend verification email
          </Button>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title={search.invitation ? 'Accept your invitation' : 'Create your account'}
      subtitle={
        search.invitation
          ? 'Set up your account to join the organization.'
          : 'Start running your business on Personal ERP.'
      }
      footer={
        <span className="text-content-muted">
          Already have an account?{' '}
          <Link to="/login" className="text-primary font-medium hover:underline">
            Sign in
          </Link>
        </span>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <Input
          label="Full name"
          autoComplete="name"
          autoFocus
          placeholder="Jhon Doe"
          leftIcon={<User />}
          error={errors.full_name?.message}
          {...register('full_name')}
        />

        <Input
          label="Work email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          leftIcon={<Mail />}
          error={errors.email?.message}
          {...register('email')}
        />

        <div>
          <Input
            label="Password"
            type={showPassword ? 'text' : 'password'}
            autoComplete="new-password"
            placeholder={passwordPlaceholder(policy)}
            error={errors.password?.message}
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
            {...register('password')}
          />

          {password.length > 0 && (
            <div className="mt-2">
              <div className="flex items-center gap-2">
                <div className="bg-surface-sunken flex h-1 flex-1 gap-1 overflow-hidden rounded-full">
                  {[0, 1, 2, 3].map((index) => (
                    <span
                      key={index}
                      className={cn(
                        'h-full flex-1 rounded-full transition-colors duration-[var(--duration-base)]',
                        index < strength.score ? strength.tone : 'bg-transparent',
                      )}
                    />
                  ))}
                </div>
                <span className="text-content-muted w-12 text-right text-[11px] font-medium">
                  {strength.label}
                </span>
              </div>
              {summarisePolicy(policy) && (
                <p className="text-content-muted mt-1.5 text-[12px]">{summarisePolicy(policy)}</p>
              )}
            </div>
          )}
        </div>

        {/* Hidden when arriving from an invitation: the organization is already
            decided, and offering to create another would be confusing. */}
        {!search.invitation && (
          <Input
            label="Company name"
            placeholder="Acme Trading Co"
            leftIcon={<Building2 />}
            hint="Optional. You can create or join an organization later."
            error={errors.organization_name?.message}
            {...register('organization_name')}
          />
        )}

        <Button type="submit" fullWidth size="lg" loading={isSubmitting}>
          Create account
        </Button>

        <p className="text-content-muted text-center text-[12px] leading-relaxed">
          By creating an account you agree to our Terms of Service and Privacy Policy.
        </p>
      </form>
    </AuthLayout>
  );
}
