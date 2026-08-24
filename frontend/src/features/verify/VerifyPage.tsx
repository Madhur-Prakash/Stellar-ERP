/**
 * The public verifier - the only screen in this application designed for somebody
 * who does not trust the server that served it.
 *
 * Everything about it follows from one sentence: **a bank's credit officer must be
 * able to reach a verdict with no account, no wallet, no seed phrase, and no idea
 * that a blockchain is involved.**
 *
 * So:
 *
 * - **No authentication, and no app shell.** It does not import the sidebar, the
 *   session, or anything that could develop a dependency on being signed in.
 * - **The verdict is computed here, in the reader's browser.** The hash is
 *   recomputed from the file they hold, and the root is read straight from the
 *   Stellar contract. Our API is not consulted for the answer at any point - which
 *   is the whole reason the feature is worth anything.
 * - **The RPC endpoint is editable, on screen.** A scheme whose pitch is "you need
 *   not trust us" cannot quietly require trusting one hosted RPC. Most people will
 *   never touch it; the ones who would notice its absence are exactly the ones
 *   whose opinion matters.
 * - **Every step is shown, passing or failing.** A single green tick is a thing to
 *   be believed. Five named checks are a thing to be read.
 * - **Failures say what is wrong in a sentence, not a stack trace.** "The figures
 *   in this document are not the figures that were sealed" is actionable;
 *   `CanonicalError: leaf mismatch` is not.
 */
import { useMutation } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock,
  ExternalLink,
  FileJson,
  Loader2,
  ShieldCheck,
  Upload,
  XCircle,
} from 'lucide-react';
import { useRef, useState } from 'react';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody } from '@/components/ui/Card';
import { track } from '@/features/feedback/api';
import type { ProofBundle } from '@/features/trust/api';
import { explorerTxUrl } from '@/features/trust/chain';
import {
  type StepResult,
  type VerifyVerdict,
  defaultRpc,
  parseBundle,
  verifyBundle,
} from '@/features/trust/verify';
import { cn } from '@/lib/cn';
import { env } from '@/lib/env';

export function VerifyPage() {
  const [text, setText] = useState('');
  const [rpcUrl, setRpcUrl] = useState('');
  const [showRpc, setShowRpc] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const check = useMutation<VerifyVerdict, Error, { bundle: ProofBundle; rpcUrl?: string }>({
    mutationFn: ({ bundle, rpcUrl: rpc }) => verifyBundle(bundle, rpc ? { rpcUrl: rpc } : {}),
    onSuccess: (verdict) => {
      // Counted anonymously - the caller has no account here and never will. This
      // is the one metric that says whether the feature is used *by the people it
      // was built for*, so requiring a login to record it would guarantee the
      // answer was always zero. See `PUBLIC_ACTIONS` on the server.
      track(verdict.verified ? 'proof.verified' : 'proof.rejected', {
        verified: verdict.verified,
        ...(verdict.network ? { network: verdict.network } : {}),
      });
    },
  });

  const run = (raw: string) => {
    setParseError(null);
    check.reset();
    let bundle: ProofBundle;
    try {
      bundle = parseBundle(raw);
    } catch (cause) {
      setParseError(cause instanceof Error ? cause.message : 'That file could not be read.');
      return;
    }
    check.mutate(rpcUrl.trim() ? { bundle, rpcUrl: rpcUrl.trim() } : { bundle });
  };

  const readFile = async (file: File) => {
    const contents = await file.text();
    setText(contents);
    run(contents);
  };

  return (
    <div className="bg-surface-sunken min-h-screen">
      <header className="border-border bg-surface border-b">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
          <div className="flex items-center gap-2.5">
            <div className="bg-primary/10 text-primary grid size-8 place-items-center rounded-lg">
              <ShieldCheck className="size-4.5" />
            </div>
            <div>
              <p className="text-content text-sm font-semibold">Verify a document</p>
              <p className="text-content-muted text-[12px]">
                Checked in your browser, against the Stellar network
              </p>
            </div>
          </div>
          <Badge tone="neutral">{env.appName}</Badge>
        </div>
      </header>

      <main className="mx-auto max-w-3xl space-y-4 px-4 py-6 sm:px-6">
        <Card>
          <CardBody className="pt-5">
            <h1 className="text-content text-[17px] font-semibold tracking-tight">
              Was this document altered?
            </h1>
            <p className="text-content-secondary mt-1.5 text-[13px] leading-relaxed">
              Paste the proof file you were sent, or drop it below. This page recomputes the
              document&apos;s fingerprint and compares it with what is recorded on the Stellar
              network. <strong>Nothing is uploaded</strong> - the check happens on your device, and
              the answer does not depend on trusting whoever sent you the file.
            </p>

            <div
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                const file = event.dataTransfer.files[0];
                if (file) void readFile(file);
              }}
              className={cn(
                'mt-4 rounded-lg border-2 border-dashed p-4 transition-colors',
                dragging ? 'border-primary bg-primary/5' : 'border-border bg-surface-sunken',
              )}
            >
              <label htmlFor="proof" className="sr-only">
                Proof file contents
              </label>
              <textarea
                id="proof"
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder='Paste the proof file here - it starts with {"format": "stellar-erp.proof.v1", …'
                spellCheck={false}
                rows={6}
                className="border-border bg-surface text-content placeholder:text-content-muted focus:border-primary focus:ring-primary/20 w-full resize-y rounded-md border p-3 font-mono text-[12px] focus:ring-2 focus:outline-none"
              />

              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button
                  onClick={() => run(text)}
                  disabled={!text.trim() || check.isPending}
                  loading={check.isPending}
                  leftIcon={<ShieldCheck className="size-4" />}
                >
                  Verify
                </Button>
                <Button
                  variant="outline"
                  onClick={() => fileInput.current?.click()}
                  leftIcon={<Upload className="size-4" />}
                >
                  Choose a file
                </Button>
                <input
                  ref={fileInput}
                  type="file"
                  accept=".json,application/json,text/plain"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void readFile(file);
                  }}
                />
                {text && (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setText('');
                      setParseError(null);
                      check.reset();
                    }}
                  >
                    Clear
                  </Button>
                )}
              </div>
            </div>

            {parseError && (
              <p className="text-danger mt-3 flex items-start gap-2 text-[13px]">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                {parseError}
              </p>
            )}

            <div className="mt-4">
              <button
                type="button"
                onClick={() => setShowRpc((open) => !open)}
                className="text-content-muted hover:text-content-secondary inline-flex items-center gap-1 text-[12px]"
              >
                <ChevronDown
                  className={cn('size-3.5 transition-transform', showRpc && 'rotate-180')}
                />
                Use a different Stellar endpoint
              </button>
              {showRpc && (
                <div className="mt-2">
                  <label htmlFor="rpc" className="sr-only">
                    Soroban RPC URL
                  </label>
                  <input
                    id="rpc"
                    value={rpcUrl}
                    onChange={(event) => setRpcUrl(event.target.value)}
                    placeholder={defaultRpc('testnet')}
                    spellCheck={false}
                    className="border-border bg-surface text-content placeholder:text-content-muted focus:border-primary focus:ring-primary/20 w-full rounded-md border px-3 py-2 font-mono text-[12px] focus:ring-2 focus:outline-none"
                  />
                  <p className="text-content-muted mt-1 text-[12px]">
                    Leave this blank to use the public endpoint for the network named in the file.
                    Point it anywhere you like - including a node you run - so the answer never
                    depends on an endpoint somebody else chose.
                  </p>
                </div>
              )}
            </div>
          </CardBody>
        </Card>

        {check.isPending && (
          <Card>
            <CardBody className="flex items-center gap-3 pt-5">
              <Loader2 className="text-primary size-4 animate-spin" />
              <p className="text-content-secondary text-[13px]">
                Hashing the document and reading the Stellar contract…
              </p>
            </CardBody>
          </Card>
        )}

        {check.isError && !check.data && (
          <Card>
            <CardBody className="pt-5">
              <p className="text-danger flex items-start gap-2 text-[13px]">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                {check.error.message}
              </p>
            </CardBody>
          </Card>
        )}

        {check.data && <Verdict verdict={check.data} />}

        <Card>
          <CardBody className="pt-5">
            <h2 className="text-content text-sm font-semibold">What this tells you</h2>
            <dl className="mt-3 space-y-3">
              <div>
                <dt className="text-content flex items-center gap-1.5 text-[13px] font-medium">
                  <CheckCircle2 className="text-success size-3.5" />A green result means
                </dt>
                <dd className="text-content-secondary mt-0.5 text-[13px] leading-relaxed">
                  This document is byte-for-byte what it was when the business sealed its books, and
                  the seal was recorded on a public network at a date the business could not have
                  back-dated.
                </dd>
              </div>
              <div>
                <dt className="text-content flex items-center gap-1.5 text-[13px] font-medium">
                  <AlertTriangle className="text-warning size-3.5" />
                  It does not mean
                </dt>
                <dd className="text-content-secondary mt-0.5 text-[13px] leading-relaxed">
                  That the figures were correct when they were entered. No cryptographic check can
                  establish that. What it rules out is the document having been changed afterwards
                  to fit a different story - which is how accounts are usually falsified.
                </dd>
              </div>
              <div>
                <dt className="text-content flex items-center gap-1.5 text-[13px] font-medium">
                  <FileJson className="text-content-muted size-3.5" />
                  What you are not being shown
                </dt>
                <dd className="text-content-secondary mt-0.5 text-[13px] leading-relaxed">
                  Anything else in the business&apos;s books. The proof carries this one entry and a
                  short list of opaque fingerprints; the rest of the ledger stays private and cannot
                  be reconstructed from it.
                </dd>
              </div>
            </dl>
          </CardBody>
        </Card>
      </main>

      <footer className="text-content-muted mx-auto max-w-3xl px-4 pb-8 text-center text-[12px] sm:px-6">
        The check runs entirely in this page. You can read its source, and point it at any Stellar
        endpoint you trust.
      </footer>
    </div>
  );
}

/* -------------------------------------------------------------------------- */

function Verdict({ verdict }: { verdict: VerifyVerdict }) {
  const good = verdict.verified;
  return (
    <Card className={cn('border-2', good ? 'border-success/40' : 'border-danger/40')}>
      <CardBody className="pt-5">
        <div className="flex items-start gap-3">
          <div
            className={cn(
              'grid size-10 shrink-0 place-items-center rounded-full',
              good ? 'bg-success-bg text-success' : 'bg-danger-bg text-danger',
            )}
          >
            {good ? <CheckCircle2 className="size-5" /> : <XCircle className="size-5" />}
          </div>
          <div className="min-w-0">
            <p className={cn('text-[15px] font-semibold', good ? 'text-success' : 'text-danger')}>
              {verdict.headline}
            </p>
            <p className="text-content-secondary mt-1 text-[13px] leading-relaxed">
              {verdict.detail}
            </p>
          </div>
        </div>

        <ol className="border-border mt-4 space-y-2 border-t pt-4">
          {verdict.steps.map((step) => (
            <Step key={step.stage} step={step} />
          ))}
        </ol>

        {(verdict.sealedAt || verdict.sealSeq !== undefined) && (
          <dl className="border-border mt-4 grid gap-3 border-t pt-4 sm:grid-cols-2">
            {verdict.sealedAt && (
              <Fact
                label="Sealed on"
                value={verdict.sealedAt.toLocaleString()}
                note="recorded by the Stellar network, not by the sender"
                icon={<Clock className="size-3.5" />}
              />
            )}
            {verdict.entryCount !== undefined && (
              <Fact
                label="Sealed alongside"
                value={`${verdict.entryCount} entries`}
                note="you were shown only this one"
              />
            )}
            {verdict.network && (
              <Fact label="Network" value={verdict.network} note={verdict.contractId ?? ''} />
            )}
            {verdict.txHash && verdict.network && (
              <div className="min-w-0">
                <dt className="text-content-muted text-[12px] font-medium">Transaction</dt>
                <dd className="mt-0.5">
                  <a
                    href={explorerTxUrl(verdict.network, verdict.txHash)}
                    target="_blank"
                    rel="noreferrer"
                    className="text-primary inline-flex items-center gap-1 font-mono text-[12px] break-all hover:underline"
                  >
                    {verdict.txHash.slice(0, 24)}…
                    <ExternalLink className="size-3 shrink-0" />
                  </a>
                </dd>
              </div>
            )}
          </dl>
        )}

        {verdict.leafHash && (
          <details className="border-border mt-4 border-t pt-4">
            <summary className="text-content-muted hover:text-content-secondary cursor-pointer text-[12px]">
              Show the fingerprints
            </summary>
            <dl className="mt-3 space-y-2">
              <Hash label="This document" value={verdict.leafHash} />
              {verdict.computedRoot && (
                <Hash label="Computed batch root" value={verdict.computedRoot} />
              )}
              {verdict.onChainRoot && (
                <Hash label="Root on the network" value={verdict.onChainRoot} />
              )}
            </dl>
          </details>
        )}
      </CardBody>
    </Card>
  );
}

function Step({ step }: { step: StepResult }) {
  return (
    <li className="flex items-start gap-2.5 text-[13px]">
      {step.ok ? (
        <CheckCircle2 className="text-success mt-0.5 size-4 shrink-0" />
      ) : (
        <XCircle className="text-danger mt-0.5 size-4 shrink-0" />
      )}
      <span className={cn('min-w-0', step.ok ? 'text-content-secondary' : 'text-danger')}>
        {step.label}
        {step.detail && (
          <span className="text-content-muted ml-1.5 font-mono text-[11px] break-all">
            {step.detail}
          </span>
        )}
      </span>
    </li>
  );
}

function Fact({
  label,
  value,
  note,
  icon,
}: {
  label: string;
  value: string;
  note?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-content-muted flex items-center gap-1 text-[12px] font-medium">
        {icon}
        {label}
      </dt>
      <dd className="text-content mt-0.5 text-[13px] wrap-break-word">{value}</dd>
      {note && <dd className="text-content-muted text-[11px] break-all">{note}</dd>}
    </div>
  );
}

function Hash({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-content-muted text-[11px] font-medium">{label}</dt>
      <dd className="text-content-secondary font-mono text-[11px] break-all">{value}</dd>
    </div>
  );
}
