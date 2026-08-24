import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { Building2, Check } from 'lucide-react';
import { toast } from 'sonner';

import { Button, buttonClasses } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { AuthLayout } from '@/features/auth/AuthLayout';
import { useAuth } from '@/features/auth/AuthProvider';
import { organizationsApi } from '@/features/organizations/api';
import { ApiError } from '@/lib/api';
import { formatDate } from '@/lib/format';

/**
 * Invitation acceptance.
 *
 * Handles both recipients: someone who already has an account (accept directly)
 * and someone who does not (redirected to registration with the token attached).
 * The preview endpoint reports which case applies, so the page never asks the
 * user to work it out.
 */
export function AcceptInvitePage() {
  const search = useSearch({ strict: false });
  const navigate = useNavigate();
  const { isAuthenticated, isLoading: authLoading, refresh } = useAuth();

  const token = search.token;

  const {
    data: preview,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useQuery({
    queryKey: ['invitation', token],
    queryFn: () => organizationsApi.previewInvitation(token!),
    enabled: Boolean(token),
    // An invalid or expired token will not become valid on retry, so those must
    // not be retried. A 5xx or a dropped connection is a different thing
    // entirely and may well succeed a second later.
    retry: (failureCount, err) => err instanceof ApiError && err.isRetryable && failureCount < 2,
  });

  const accept = useMutation({
    mutationFn: () => organizationsApi.acceptInvitation(token!),
    onSuccess: async (result) => {
      toast.success(result.message, {
        ...(result.detail ? { description: result.detail } : {}),
      });
      // The membership changed, so permissions did too.
      await refresh();
      void navigate({ to: '/', replace: true });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : 'Could not accept the invitation'),
  });

  if (!token) {
    return (
      <AuthLayout
        title="Invalid invitation link"
        subtitle="This link is missing its token."
        footer={
          <Link to="/login" className="text-primary font-medium hover:underline">
            Go to sign in
          </Link>
        }
      >
        <p className="text-content-muted text-center text-[13px]">
          Ask whoever invited you to send a new invitation.
        </p>
      </AuthLayout>
    );
  }

  if (isLoading || authLoading) {
    return (
      <AuthLayout title="Checking your invitation…">
        <div className="space-y-3">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-9 w-full" />
        </div>
      </AuthLayout>
    );
  }

  if (error || !preview) {
    // A server fault is not an invalid invitation, and reporting one as the other
    // is actively harmful: it tells the recipient to ask for a new link, and the
    // new link fails in exactly the same way. This screen once rendered "This
    // invitation is no longer valid / A database error occurred" - two claims that
    // contradict each other, one of them wrong.
    const serverFault = !(error instanceof ApiError) || error.isRetryable;

    if (serverFault) {
      return (
        <AuthLayout
          title="We could not check your invitation"
          subtitle="Something went wrong on our side. Your invitation is most likely fine."
          footer={
            <Link to="/login" className="text-primary font-medium hover:underline">
              Go to sign in
            </Link>
          }
        >
          <div className="space-y-4">
            <p className="text-content-muted text-center text-[13px] leading-relaxed">
              This is a problem with the server, not with your link - asking for a new invitation
              will not help. Try again in a moment.
            </p>
            <Button fullWidth size="lg" loading={isFetching} onClick={() => void refetch()}>
              Try again
            </Button>
            {error instanceof ApiError && error.requestId !== undefined && (
              <p className="text-content-muted text-center font-mono text-[11px]">
                Reference: {error.requestId}
              </p>
            )}
          </div>
        </AuthLayout>
      );
    }

    return (
      <AuthLayout
        title="This invitation is no longer valid"
        subtitle={
          error instanceof ApiError ? error.message : 'It may have expired or already been used.'
        }
        footer={
          <Link to="/login" className="text-primary font-medium hover:underline">
            Go to sign in
          </Link>
        }
      >
        <p className="text-content-muted text-center text-[13px]">
          Invitations expire after 7 days. Ask for a new one.
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title={`Join ${preview.organization_name}`}
      subtitle={
        <>
          {preview.invited_by_name ?? 'Someone'} invited{' '}
          <strong className="text-content">{preview.email}</strong> to join as{' '}
          <strong className="text-content">{preview.role_name}</strong>.
        </>
      }
    >
      <div className="space-y-5">
        <div className="border-border bg-surface-sunken/50 flex items-center gap-3 rounded-lg border p-4">
          <span
            className="bg-primary/12 text-primary flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-sm font-bold"
            aria-hidden
          >
            {preview.organization_name.slice(0, 2).toUpperCase()}
          </span>
          <div className="min-w-0">
            <p className="text-content truncate text-[14px] font-semibold">
              {preview.organization_name}
            </p>
            <p className="text-content-muted text-[12px]">
              Role: {preview.role_name} · expires {formatDate(preview.expires_at)}
            </p>
          </div>
        </div>

        {preview.requires_registration ? (
          <>
            <p className="text-content-muted text-[13px] leading-relaxed">
              You will need an account first. Registering through this link joins the organization
              automatically and verifies your email.
            </p>
            <Link
              to="/register"
              search={{ invitation: token }}
              className={buttonClasses('primary', 'lg', 'w-full')}
            >
              Create your account
            </Link>
          </>
        ) : isAuthenticated ? (
          <Button
            fullWidth
            size="lg"
            loading={accept.isPending}
            leftIcon={<Check className="h-4 w-4" />}
            onClick={() => accept.mutate()}
          >
            Accept invitation
          </Button>
        ) : (
          <>
            <p className="text-content-muted text-[13px] leading-relaxed">
              You already have an account. Sign in to accept this invitation.
            </p>
            <Link
              to="/login"
              search={{ redirect: `/accept-invite?token=${encodeURIComponent(token)}` }}
              className={buttonClasses('primary', 'lg', 'w-full')}
            >
              <Building2 className="mr-2 h-4 w-4" aria-hidden />
              Sign in to continue
            </Link>
          </>
        )}
      </div>
    </AuthLayout>
  );
}
