import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { FileText, Filter } from 'lucide-react';
import { useState } from 'react';

import { PageHeader } from '@/components/layout/AppShell';
import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';
import { organizationsApi } from '@/features/organizations/api';
import { formatDateTime, formatRelative } from '@/lib/format';
import type { AuditSeverity } from '@/types/api';

/**
 * Render an audit diff value for display.
 *
 * The values are `unknown` - a diff can hold a string, number, boolean, null, or
 * a nested JSONB object. Passing an object to `String()` yields
 * "[object Object]", which is worse than useless in an audit trail, so objects
 * are serialised instead.
 */
function renderDiffValue(value: unknown): string {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return '-';
}

/**
 * A before/after pair, or `null` if this entry is not one.
 *
 * **`changes` is not uniformly shaped, and assuming it was crashed this page.** Writers
 * that go through the audit service's `diff()` produce `{ field: { before, after } }`.
 * Document upload, re-extract, and confirm-into-bill instead use the column as a flat
 * snapshot - `{ status: 'uploaded', duplicate_of: null }` - so reading `.before` off the
 * value threw `Cannot read properties of null` and took the entire page down with it. One
 * uploaded document was enough.
 *
 * Both shapes are handled rather than one being migrated, because audit rows are immutable
 * by design: the mixed-shape entries already in the table cannot be rewritten, so any
 * reader has to cope with them for as long as the log is kept.
 *
 * The `in` checks matter - a snapshot value could legitimately be an object (a nested
 * payload), and that must render as a value rather than as an empty "- → -" diff.
 */
function asFieldDiff(value: unknown): { before: unknown; after: unknown } | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  if (!('before' in value) && !('after' in value)) return null;
  const pair = value as { before?: unknown; after?: unknown };
  return { before: pair.before ?? null, after: pair.after ?? null };
}

const SEVERITY_TONE: Record<AuditSeverity, BadgeTone> = {
  info: 'neutral',
  warning: 'warning',
  critical: 'danger',
};

export function AuditPage() {
  const [action, setAction] = useState('');
  const [severity, setSeverity] = useState('');

  const { data: actions } = useQuery({
    queryKey: ['audit-actions'],
    queryFn: organizationsApi.auditActions,
    staleTime: 60 * 60 * 1000,
  });

  /**
   * Cursor pagination via `useInfiniteQuery`.
   *
   * The trail is append-heavy: offsets both degrade with depth and shift rows
   * under the reader as new events land, so the API is cursor-based and the
   * client follows `next_cursor`.
   */
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ['audit', { action, severity }],
    queryFn: ({ pageParam }) =>
      organizationsApi.listAudit({
        limit: 25,
        ...(pageParam ? { cursor: pageParam } : {}),
        ...(action ? { action } : {}),
        ...(severity ? { severity } : {}),
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const entries = data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div>
      <PageHeader
        title="Audit log"
        description="An append-only record of every action taken in this organization."
      />

      <Card className="mb-4">
        <CardBody className="flex flex-wrap items-center gap-3 py-4">
          <Filter className="text-content-muted hidden h-4 w-4 sm:block" aria-hidden />

          {/* A native select is as wide as its widest option, and an action name like
              `organization.member.invitation.resend` is wider than a phone. Full width
              on its own row below `sm`, capped above it. */}
          <select
            value={action}
            onChange={(event) => setAction(event.target.value)}
            aria-label="Filter by action"
            className="border-border bg-surface text-content h-8 w-full min-w-0 rounded-md border px-2.5 text-[13px] sm:w-auto sm:max-w-64"
          >
            <option value="">All actions</option>
            {(actions ?? []).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>

          <select
            value={severity}
            onChange={(event) => setSeverity(event.target.value)}
            aria-label="Filter by severity"
            className="border-border bg-surface text-content h-8 w-full min-w-0 rounded-md border px-2.5 text-[13px] sm:w-auto"
          >
            <option value="">All severities</option>
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="critical">Critical</option>
          </select>

          {(action || severity) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setAction('');
                setSeverity('');
              }}
            >
              Clear filters
            </Button>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardBody className="p-0">
          {isLoading ? (
            <div className="space-y-3 p-5">
              {Array.from({ length: 8 }).map((_, index) => (
                <Skeleton key={index} className="h-10 rounded-md" />
              ))}
            </div>
          ) : entries.length === 0 ? (
            <EmptyState
              icon={FileText}
              title="No matching events"
              description={
                action || severity
                  ? 'Try widening your filters.'
                  : 'Actions across your organization will appear here as they happen.'
              }
            />
          ) : (
            <ul className="divide-border divide-y">
              {entries.map((entry) => (
                <li key={entry.id} className="hover:bg-surface-hover/40 px-5 py-3.5">
                  <div className="flex items-start gap-3">
                    <Badge tone={SEVERITY_TONE[entry.severity]} className="mt-0.5 shrink-0">
                      {entry.severity}
                    </Badge>

                    <div className="min-w-0 flex-1">
                      <p className="text-content text-[13px] leading-snug">
                        {entry.summary ?? entry.action}
                      </p>

                      <div className="text-content-muted mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px]">
                        <code className="bg-surface-sunken rounded px-1.5 py-0.5">
                          {entry.action}
                        </code>
                        <span>{entry.actor.name ?? entry.actor.email ?? 'System'}</span>
                        {entry.ip_address && <span>{entry.ip_address}</span>}
                        <span title={formatDateTime(entry.created_at)}>
                          {formatRelative(entry.created_at)}
                        </span>
                      </div>

                      {/* The field-level diff. Shown inline because "what
                          changed" is usually the reason someone opened this. */}
                      {Object.keys(entry.changes).length > 0 && (
                        <dl className="border-border mt-2 space-y-1 border-l-2 pl-3">
                          {Object.entries(entry.changes).map(([field, value]) => {
                            const diff = asFieldDiff(value);
                            return (
                              <div key={field} className="flex flex-wrap gap-1.5 text-[11px]">
                                <dt className="text-content-secondary font-medium">{field}:</dt>
                                <dd className="text-content-muted">
                                  {diff ? (
                                    <>
                                      <span className="line-through">
                                        {renderDiffValue(diff.before)}
                                      </span>
                                      {' → '}
                                      <span className="text-content">
                                        {renderDiffValue(diff.after)}
                                      </span>
                                    </>
                                  ) : (
                                    /* A snapshot rather than a change: showing it as
                                       "- → value" would invent a previous value that was
                                       never recorded. */
                                    <span className="text-content">{renderDiffValue(value)}</span>
                                  )}
                                </dd>
                              </div>
                            );
                          })}
                        </dl>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      {hasNextPage && (
        <div className="mt-4 flex justify-center">
          <Button
            variant="secondary"
            loading={isFetchingNextPage}
            onClick={() => void fetchNextPage()}
          >
            Load more
          </Button>
        </div>
      )}
    </div>
  );
}
