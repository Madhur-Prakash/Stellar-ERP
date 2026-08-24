/**
 * Trust - the third ledger, from the business's side.
 *
 * Three presentation decisions, each of which is really an honesty decision:
 *
 * - **The backlog's age is the headline, not the seal count.** "412 entries
 *   sealed" is reassuring and says nothing about now. "9 entries unsealed for 6
 *   days" is the only figure that distinguishes sealing working from sealing
 *   having silently stopped, which look identical from every other angle.
 * - **What the seal does *not* prove is on the screen.** While the signing key
 *   lives on this server, a seal proves the books have not changed *since* it was
 *   written - not that they were right when it was. Burying that in documentation
 *   would be the single most dishonest thing this product could do.
 * - **A pending seal shows no timestamp.** `sealed_at` comes from the network, and
 *   until the network has answered there is no time to show. Rendering our own
 *   clock there would undermine the exact claim the page exists to make.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  ExternalLink,
  KeyRound,
  Link2,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
} from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { PageHeader } from '@/components/ui/DataTable';
import { EmptyState } from '@/components/ui/EmptyState';
import { Select } from '@/components/ui/Select';
import { Skeleton } from '@/components/ui/Skeleton';
import {
  type AttestationStatus,
  type Seal,
  type SealCadence,
  trustApi,
} from '@/features/trust/api';
import { track } from '@/features/feedback/api';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatDateTime, formatNumber } from '@/lib/format';

const CADENCE_OPTIONS: { value: SealCadence; label: string }[] = [
  { value: 'daily', label: 'Every day, and when a period closes' },
  { value: 'on_period_close', label: 'Only when a period closes' },
  { value: 'manual', label: 'Only when I press the button' },
];

export function TrustPage() {
  const queryClient = useQueryClient();
  const [showAdvanced, setShowAdvanced] = useState(false);

  const {
    data: status,
    isLoading,
    error,
  } = useQuery({
    queryKey: ['attestation-status'],
    queryFn: () => trustApi.status(),
    // The chain is read on every call, so this is the one screen where a stale
    // answer is actively misleading: a business checking whether its books are
    // sealed needs now, not a minute ago.
    refetchInterval: 30_000,
  });

  const { data: history } = useQuery({
    queryKey: ['attestation-seals'],
    queryFn: () => trustApi.seals(undefined, 20),
    enabled: Boolean(status?.enabled),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['attestation-status'] });
    void queryClient.invalidateQueries({ queryKey: ['attestation-seals'] });
  };

  const failed = (fallback: string) => (cause: unknown) =>
    toast.error(cause instanceof ApiError ? cause.message : fallback);

  const enable = useMutation({
    mutationFn: (cadence: SealCadence) => trustApi.enable({ cadence, fund_on_testnet: true }),
    onSuccess: (next) => {
      toast.success('Sealing is on. Your books are now committed to Stellar.');
      // The conversion that matters: how many businesses reach this screen versus
      // how many switch sealing on.
      track('attestation.enabled', {
        ...(next.network ? { network: next.network } : {}),
        cadence: next.cadence,
      });
      invalidate();
    },
    onError: failed('Could not switch sealing on'),
  });

  const disable = useMutation({
    mutationFn: () => trustApi.disable(),
    onSuccess: () => {
      toast.success('Sealing is off. Everything already sealed stays verifiable.');
      // Worth counting as much as switching it on: a business that stops sealing
      // stops being checkable, and the rate is a product signal not a vanity one.
      track('attestation.disabled');
      invalidate();
    },
    onError: failed('Could not switch sealing off'),
  });

  const sealNow = useMutation({
    mutationFn: () => trustApi.sealNow(),
    onSuccess: (result) => {
      toast.success(result.message);
      track('seal.now', {
        outcome:
          result.seal?.status === 'confirmed'
            ? 'ok'
            : result.seal?.status === 'failed'
              ? 'failed'
              : 'unknown',
        ...(result.seal ? { count: result.seal.entry_count } : {}),
      });
      invalidate();
    },
    onError: failed('Could not seal'),
  });

  const reconcile = useMutation({
    mutationFn: () => trustApi.reconcile(),
    onSuccess: (result) => {
      toast.success(
        result.agrees
          ? 'The chain and your database agree.'
          : `Reconciled against the chain (head #${String(result.chain_head ?? 0)}).`,
      );
      invalidate();
    },
    onError: failed('Could not reconcile'),
  });

  const setCadence = useMutation({
    mutationFn: (cadence: SealCadence) => trustApi.setCadence(cadence),
    onSuccess: () => {
      toast.success('Sealing schedule updated.');
      invalidate();
    },
    onError: failed('Could not change the schedule'),
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <PageHeader
          title="Trust"
          description="The third ledger - proof your books are unchanged."
        />
        <Skeleton className="h-32" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (error || !status) {
    return (
      <div>
        <PageHeader title="Trust" />
        <EmptyState
          icon={AlertTriangle}
          title="Could not load sealing status"
          description={error instanceof ApiError ? error.message : 'Try again in a moment.'}
        />
      </div>
    );
  }

  // Nothing configured on the server at all - a deployment question, not a user one.
  if (!status.configured && !status.enabled) {
    return (
      <div>
        <PageHeader
          title="Trust"
          description="The third ledger - proof your books are unchanged."
        />
        <Card>
          <CardBody>
            <Explainer />
            <div className="border-border mt-5 border-t pt-5">
              <h3 className="text-content text-sm font-semibold">Turn it on</h3>
              <p className="text-content-muted mt-1 max-w-2xl text-[13px]">
                A Stellar account is created for this organization, funded on the test network, and
                registered on the proof-ledger contract. Nothing about your accounting changes, and
                no business data leaves this server.
              </p>
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <Button
                  onClick={() => enable.mutate('daily')}
                  loading={enable.isPending}
                  leftIcon={<ShieldCheck className="size-4" />}
                >
                  Enable sealing
                </Button>
                <span className="text-content-muted text-[12px]">Recommended: seal every day.</span>
              </div>
            </div>
          </CardBody>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Trust"
        description="The third ledger - proof your books are unchanged."
        action={
          status.enabled ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => reconcile.mutate()}
                loading={reconcile.isPending}
                leftIcon={<RefreshCw className="size-3.5" />}
              >
                Check the chain
              </Button>
              <Button
                size="sm"
                onClick={() => sealNow.mutate()}
                loading={sealNow.isPending}
                disabled={!status.ready}
                leftIcon={<ShieldCheck className="size-3.5" />}
              >
                Seal now
              </Button>
            </div>
          ) : undefined
        }
      />

      {status.warnings.length > 0 && <Warnings warnings={status.warnings} />}

      <SealingState status={status} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Metric
          label="Entries sealed"
          value={formatNumber(status.entries_sealed)}
          sub={`across ${formatNumber(status.seals_confirmed)} seal${
            status.seals_confirmed === 1 ? '' : 's'
          }`}
          tone="neutral"
        />
        <Metric
          label="Waiting to be sealed"
          value={formatNumber(status.unsealed_entries)}
          sub={
            status.days_unsealed === null
              ? 'nothing outstanding'
              : `oldest is ${status.days_unsealed} day${status.days_unsealed === 1 ? '' : 's'} old`
          }
          tone={(status.days_unsealed ?? 0) >= 2 ? 'bad' : 'neutral'}
        />
        <Metric
          label="Chain"
          value={
            !status.chain.reachable
              ? 'unreachable'
              : status.chain.head === null
                ? '—'
                : `#${String(status.chain.head)}`
          }
          sub={
            !status.chain.reachable
              ? (status.chain.error ?? 'could not be read')
              : status.chain.agrees_with_local === false
                ? 'disagrees with this database'
                : `${status.network ?? ''} · ${formatNumber(status.chain.entries ?? 0)} entries`
          }
          tone={status.chain.agrees_with_local === false ? 'bad' : 'neutral'}
        />
      </div>

      <Card>
        <CardHeader
          title="Seal history"
          description="Each seal commits a batch of entries. Every one links to the one before it."
          action={
            history && history.items.length > 0 ? (
              history.continuous ? (
                <Badge tone="success">Unbroken chain</Badge>
              ) : (
                <Badge tone="danger">Chain broken</Badge>
              )
            ) : undefined
          }
        />
        <CardBody className="p-0">
          {!history ? (
            <div className="px-5 pb-5">
              <Skeleton className="h-24" />
            </div>
          ) : history.items.length === 0 ? (
            <div className="px-5 pb-5">
              <EmptyState
                icon={Clock}
                title="Nothing sealed yet"
                description="Post an entry and press Seal now, or wait for tonight's scheduled seal."
              />
            </div>
          ) : (
            <SealList seals={history.items} />
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Settings"
          action={
            <Button variant="ghost" size="sm" onClick={() => setShowAdvanced((open) => !open)}>
              {showAdvanced ? 'Hide details' : 'Show details'}
            </Button>
          }
        />
        <CardBody className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Select
              label="How often to seal"
              value={status.cadence}
              options={CADENCE_OPTIONS}
              disabled={!status.enabled || setCadence.isPending}
              onChange={(event) => setCadence.mutate(event.target.value as SealCadence)}
              hint="Sealing more often narrows the window in which history could be rewritten. On Stellar it costs a fraction of a cent, so daily is the default."
            />

            <div>
              <span className="text-content-secondary mb-1.5 block text-[13px] font-medium">
                Sealing
              </span>
              {status.enabled ? (
                <Button
                  variant="outline"
                  onClick={() => disable.mutate()}
                  loading={disable.isPending}
                  leftIcon={<ShieldOff className="size-4" />}
                >
                  Turn sealing off
                </Button>
              ) : (
                <Button
                  onClick={() => enable.mutate('daily')}
                  loading={enable.isPending}
                  leftIcon={<ShieldCheck className="size-4" />}
                >
                  Turn sealing on
                </Button>
              )}
              <span className="text-content-muted mt-1.5 block text-[12px]">
                Turning it off stops new seals. Everything already sealed stays verifiable forever.
              </span>
            </div>
          </div>

          {showAdvanced && <Advanced status={status} />}
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <Explainer />
        </CardBody>
      </Card>
    </div>
  );
}

/* -------------------------------------------------------------------------- */

function Warnings({ warnings }: { warnings: string[] }) {
  // Server-ordered, worst first, and rendered in that order. Sorting them here
  // would put a configuration note above a chain divergence.
  return (
    <div className="space-y-2">
      {warnings.map((warning, index) => (
        <div
          key={warning}
          className={cn(
            'flex items-start gap-2.5 rounded-lg border p-3 text-[13px]',
            index === 0
              ? 'border-warning/30 bg-warning-bg text-warning'
              : 'border-border bg-surface-sunken text-content-secondary',
          )}
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <p>{warning}</p>
        </div>
      ))}
    </div>
  );
}

function SealingState({ status }: { status: AttestationStatus }) {
  const on = status.enabled && status.ready;
  return (
    <Card>
      <CardBody className="flex flex-wrap items-center justify-between gap-4 pt-5">
        <div className="flex items-center gap-3">
          <div
            className={cn(
              'grid size-10 shrink-0 place-items-center rounded-full',
              on ? 'bg-success-bg text-success' : 'bg-surface-sunken text-content-muted',
            )}
          >
            {on ? <ShieldCheck className="size-5" /> : <ShieldOff className="size-5" />}
          </div>
          <div>
            <p className="text-content text-sm font-semibold">
              {on ? 'Your books are being sealed' : 'Sealing is off'}
            </p>
            <p className="text-content-muted text-[13px]">
              {status.last_seal?.sealed_at
                ? `Last sealed ${formatDateTime(status.last_seal.sealed_at)}`
                : status.open_seal
                  ? `Seal #${String(status.open_seal.seq)} is awaiting confirmation`
                  : 'Nothing sealed yet'}
            </p>
          </div>
        </div>

        {status.contract_url && (
          <a
            href={status.contract_url}
            target="_blank"
            rel="noreferrer"
            className="text-primary focus-visible:ring-primary inline-flex items-center gap-1.5 rounded text-[13px] hover:underline focus-visible:ring-2 focus-visible:outline-none"
          >
            <Link2 className="size-3.5" />
            View the contract
            <ExternalLink className="size-3" />
          </a>
        )}
      </CardBody>
    </Card>
  );
}

function Metric({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub: string;
  tone: 'bad' | 'neutral';
}) {
  return (
    <Card>
      <CardBody className="pt-5">
        <p className="text-content-muted text-[12px] font-medium tracking-wide uppercase">
          {label}
        </p>
        <p
          className={cn(
            'mt-1 text-2xl font-semibold tabular-nums',
            tone === 'bad' ? 'text-danger' : 'text-content',
          )}
        >
          {value}
        </p>
        <p className="text-content-muted mt-0.5 text-[12px]">{sub}</p>
      </CardBody>
    </Card>
  );
}

function statusBadge(seal: Seal) {
  switch (seal.status) {
    case 'confirmed':
      return <Badge tone="success">On chain</Badge>;
    case 'submitted':
      return <Badge tone="info">Awaiting confirmation</Badge>;
    case 'pending':
      return <Badge tone="warning">Queued</Badge>;
    case 'failed':
      return <Badge tone="danger">Failed</Badge>;
  }
}

function SealList({ seals }: { seals: Seal[] }) {
  return (
    <ul className="divide-border divide-y">
      {seals.map((seal) => (
        <li key={seal.id} className="px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-content text-sm font-semibold tabular-nums">
                  Seal #{seal.seq}
                </span>
                {statusBadge(seal)}
                {seal.status === 'confirmed' && seal.explorer_url && (
                  <a
                    href={seal.explorer_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-primary inline-flex items-center gap-1 text-[12px] hover:underline"
                  >
                    transaction
                    <ExternalLink className="size-3" />
                  </a>
                )}
              </div>
              <p className="text-content-secondary mt-1 text-[13px]">
                {formatNumber(seal.entry_count)} entr{seal.entry_count === 1 ? 'y' : 'ies'} ·{' '}
                {seal.entry_date_from === seal.entry_date_to
                  ? seal.entry_date_from
                  : `${seal.entry_date_from} to ${seal.entry_date_to}`}
              </p>
              <p className="text-content-muted mt-1 font-mono text-[11px] break-all">
                {seal.merkle_root}
              </p>
              {seal.last_error && seal.status !== 'confirmed' && (
                <p className="text-danger mt-1 text-[12px]">{seal.last_error}</p>
              )}
            </div>

            <div className="shrink-0 text-right">
              {/* No timestamp until the network gives one. */}
              <p className="text-content-secondary text-[13px]">
                {seal.sealed_at ? formatDateTime(seal.sealed_at) : '—'}
              </p>
              <p className="text-content-muted text-[12px]">
                {seal.sealed_at
                  ? 'network time'
                  : seal.status === 'failed'
                    ? 'never sealed'
                    : 'not yet confirmed'}
              </p>
              <p className="text-content-muted mt-1 text-[12px]">
                proves 1 entry with {seal.tree_depth} hash
                {seal.tree_depth === 1 ? '' : 'es'}
              </p>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

function Advanced({ status }: { status: AttestationStatus }) {
  const rows: [string, string | null][] = [
    ['Network', status.network],
    ['Contract', status.contract_id],
    ['Your namespace on chain', status.org_namespace],
    ['Signing account', status.signer_public_key],
    ['Registered', status.registered_at ? formatDateTime(status.registered_at) : null],
  ];

  return (
    <div className="border-border space-y-4 border-t pt-4">
      <dl className="grid gap-3 sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-content-muted text-[12px] font-medium">{label}</dt>
            <dd className="text-content mt-0.5 font-mono text-[12px] break-all">{value ?? '—'}</dd>
          </div>
        ))}
      </dl>

      <div
        className={cn(
          'flex items-start gap-2.5 rounded-lg border p-3 text-[13px]',
          status.external_signer
            ? 'border-success/30 bg-success-bg text-success'
            : 'border-border bg-surface-sunken text-content-secondary',
        )}
      >
        <KeyRound className="mt-0.5 size-4 shrink-0" />
        <div>
          <p className="font-medium">
            {status.external_signer
              ? 'The signing key is held outside this server'
              : 'The signing key is held on this server'}
          </p>
          <p className="mt-0.5">
            {status.external_signer
              ? 'Sealing needs a signature this server cannot produce alone, so a seal is evidence about the books rather than only about this machine.'
              : 'A seal therefore proves your books have not changed since it was written - not that they were correct when it was. Adding your accountant as a co-signer on the Stellar account closes that gap, and is the strongest statement available short of a full audit.'}
          </p>
        </div>
      </div>

      {status.org_namespace && (
        <p className="text-content-muted text-[12px]">
          Your namespace is a salted hash of this organization&apos;s internal id. Nothing on chain
          identifies you until you hand somebody a proof - which is what discloses it, deliberately,
          one counterparty at a time.
        </p>
      )}
    </div>
  );
}

function Explainer() {
  return (
    <div className="max-w-3xl pt-5">
      <h3 className="text-content text-sm font-semibold">What the third ledger is</h3>
      <p className="text-content-secondary mt-1.5 text-[13px] leading-relaxed">
        Your accounts already keep two ledgers: the <strong>journal</strong>, which records what
        happened to the money, and the <strong>audit trail</strong>, which records who did it. Both
        live in your own database - which means both are trusted by you and by nobody else. Anyone
        with your database password could rewrite either, and no bank, buyer, or auditor could tell.
      </p>
      <p className="text-content-secondary mt-2 text-[13px] leading-relaxed">
        The <strong>proof ledger</strong> is the third. Periodically it computes a single
        fingerprint of your journal and writes it to a public network. Later, anyone you choose can
        be handed one invoice and check it against that fingerprint - and see that it has not been
        altered since. They need no account, no wallet, and no access to anything else in your
        books.
      </p>
      <dl className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="border-border bg-surface-sunken rounded-lg border p-3">
          <dt className="text-content flex items-center gap-1.5 text-[13px] font-medium">
            <CheckCircle2 className="text-success size-3.5" />
            What a seal proves
          </dt>
          <dd className="text-content-secondary mt-1 text-[12px] leading-relaxed">
            That the books you show today are exactly the books that existed when the seal was
            written, at a time the network recorded and you cannot back-date.
          </dd>
        </div>
        <div className="border-border bg-surface-sunken rounded-lg border p-3">
          <dt className="text-content flex items-center gap-1.5 text-[13px] font-medium">
            <AlertTriangle className="text-warning size-3.5" />
            What it does not
          </dt>
          <dd className="text-content-secondary mt-1 text-[12px] leading-relaxed">
            That the entries were true when they were made. No cryptography can show that. What it
            removes is editing history afterwards - which is how accounts are actually cooked.
          </dd>
        </div>
      </dl>
      <p className="text-content-muted mt-3 text-[12px]">
        No amount, customer, product, or account number ever leaves this server. Only 32-byte
        fingerprints, a count, and a total.
      </p>
    </div>
  );
}
