import type { ErrorComponentProps } from '@tanstack/react-router';
import { RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { ApiError } from '@/lib/api';
import { env } from '@/lib/env';

/**
 * Route-level error boundary.
 *
 * Shows the request id when the failure came from the API, because that is what
 * makes a user report actionable - it maps directly to the backend log lines for
 * that exact request. The stack trace is shown in development only; in
 * production it would leak internals to no benefit.
 */
export function RouteErrorPage({ error, reset }: ErrorComponentProps) {
  const apiError = error instanceof ApiError ? error : null;

  return (
    <div className="bg-canvas flex min-h-dvh flex-col items-center justify-center px-6 text-center">
      <div
        className="bg-danger-bg text-danger mb-4 flex h-12 w-12 items-center justify-center rounded-xl"
        aria-hidden
      >
        <RefreshCw className="h-5 w-5" />
      </div>

      <h1 className="text-content text-2xl font-semibold tracking-[-0.03em]">
        Something went wrong
      </h1>
      <p className="text-content-muted mt-2 max-w-md text-[13px] leading-relaxed">
        {apiError?.message ?? 'An unexpected error occurred while loading this page.'}
      </p>

      {apiError?.requestId && (
        <p className="text-content-muted mt-3 font-mono text-[11px]">
          Request ID: {apiError.requestId}
        </p>
      )}

      <div className="mt-6 flex gap-2">
        <Button onClick={reset} leftIcon={<RefreshCw className="h-4 w-4" />}>
          Try again
        </Button>
        <Button variant="secondary" onClick={() => window.location.reload()}>
          Reload page
        </Button>
      </div>

      {env.isDev && error instanceof Error && error.stack && (
        <pre className="bg-surface-sunken text-content-muted mt-8 max-w-2xl overflow-auto rounded-lg p-4 text-left font-mono text-[11px]">
          {error.stack}
        </pre>
      )}
    </div>
  );
}
