/**
 * Client-side verification: the pipeline that makes a verdict independent of us.
 *
 * Five steps, and the fifth is the one that matters:
 *
 *   1. Read the bundle's `format` and refuse what we do not understand.
 *   2. Check the bundle's declared spec against our own, so a half-deployed
 *      release says "the verifier is out of date" instead of failing every proof.
 *   3. Re-encode the entry and hash it. Must equal the bundle's leaf.
 *   4. Fold the Merkle path. Must equal the bundle's root.
 *   5. **Ask the contract.** The root must be what it holds at that sequence.
 *
 * Steps 1-4 can be done offline and prove only that the bundle is internally
 * consistent - a forger can produce one of those trivially. Step 5 is what makes
 * the answer worth anything, and it goes to a Soroban RPC endpoint the reader
 * chooses, never through our API.
 *
 * Every failure returns a verdict with a sentence a non-technical reader can act
 * on. A verifier who sees a stack trace learns nothing; one who sees "the amounts
 * in this document do not match what was sealed" learns everything.
 */
import {
  CanonicalError,
  LOCAL_SPEC,
  fromHex,
  leafHashHex,
  toHex,
  foldPath,
} from '@/features/trust/canonical';
import { ChainError, readSeal, verifyRootOnChain, type ChainTarget } from '@/features/trust/chain';
import type { ProofBundle } from '@/features/trust/api';

export const BUNDLE_FORMAT = 'stellar-erp.proof.v1';

/** How far verification got, so the UI can show progress rather than a spinner. */
export type VerifyStage = 'format' | 'spec' | 'leaf' | 'path' | 'chain' | 'done';

export interface StepResult {
  stage: VerifyStage;
  label: string;
  ok: boolean;
  detail?: string;
}

export interface VerifyVerdict {
  /** True only when the chain itself confirmed the root. */
  verified: boolean;
  /** A sentence for a non-technical reader. */
  headline: string;
  detail: string;
  steps: StepResult[];

  /** Populated when the chain was reached. */
  sealedAt?: Date;
  sealSeq?: number;
  entryCount?: number;
  onChainRoot?: string;
  computedRoot?: string;
  leafHash?: string;
  network?: string;
  contractId?: string;
  txHash?: string | null;
}

function step(stage: VerifyStage, label: string, ok: boolean, detail?: string): StepResult {
  return detail === undefined ? { stage, label, ok } : { stage, label, ok, detail };
}

/**
 * Verify a proof bundle end to end.
 *
 * `rpcOverride` lets the reader point at their own Soroban RPC. That is not a
 * developer convenience - it is the difference between "trust this page" and
 * "trust the network", and it is the reason the field is on the screen.
 */
export async function verifyBundle(
  bundle: ProofBundle,
  options: { rpcUrl?: string } = {},
): Promise<VerifyVerdict> {
  const steps: StepResult[] = [];

  // ---- 1. Format ---------------------------------------------------------
  if (bundle?.format !== BUNDLE_FORMAT) {
    steps.push(step('format', 'Recognise the proof format', false, String(bundle?.format ?? '-')));
    return {
      verified: false,
      headline: 'This file is not a proof bundle this page understands.',
      detail: `Expected format "${BUNDLE_FORMAT}". Ask the sender to export it again.`,
      steps,
    };
  }
  steps.push(step('format', 'Recognise the proof format', true, bundle.format));

  // ---- 2. Spec agreement -------------------------------------------------
  const declared = bundle.spec?.version;
  if (declared !== LOCAL_SPEC.version) {
    steps.push(
      step(
        'spec',
        'Agree on the hashing rules',
        false,
        `bundle v${String(declared)} vs this page v${LOCAL_SPEC.version}`,
      ),
    );
    return {
      verified: false,
      headline: 'This proof was written with a different version of the hashing rules.',
      detail:
        `The bundle declares encoding version ${String(declared)} and this page implements ` +
        `version ${LOCAL_SPEC.version}. That is a deployment mismatch, not a sign the books ` +
        'were altered. Reload the page, or ask the sender which version of Stellar ERP ' +
        'produced the file.',
      steps,
    };
  }
  steps.push(step('spec', 'Agree on the hashing rules', true, `encoding v${LOCAL_SPEC.version}`));

  // ---- 3. The leaf -------------------------------------------------------
  let computedLeaf: string;
  try {
    computedLeaf = await leafHashHex(bundle.entry);
  } catch (cause) {
    const reason = cause instanceof CanonicalError ? cause.message : String(cause);
    steps.push(step('leaf', 'Hash the document', false, reason));
    return {
      verified: false,
      headline: 'This document could not be hashed, so it cannot be checked.',
      detail: reason,
      steps,
    };
  }

  if (computedLeaf !== bundle.leaf?.hash?.toLowerCase()) {
    steps.push(step('leaf', 'Hash the document', false, computedLeaf));
    return {
      verified: false,
      headline: 'The figures in this document are not the figures that were sealed.',
      detail:
        'Re-hashing the entry produced a different value from the one the bundle claims. ' +
        'Something in it has been changed since it was sealed - an amount, a date, an ' +
        'account, or the entry number.',
      steps,
      leafHash: computedLeaf,
    };
  }
  steps.push(step('leaf', 'Hash the document', true, `${computedLeaf.slice(0, 16)}…`));

  // ---- 4. The path -------------------------------------------------------
  let computedRoot: string;
  try {
    computedRoot = toHex(await foldPath(fromHex(computedLeaf), bundle.path));
  } catch (cause) {
    const reason = cause instanceof CanonicalError ? cause.message : String(cause);
    steps.push(step('path', 'Walk the proof path', false, reason));
    return {
      verified: false,
      headline: 'The proof path in this file is malformed.',
      detail: reason,
      steps,
      leafHash: computedLeaf,
    };
  }

  const claimedRoot = bundle.seal?.merkle_root?.toLowerCase() ?? '';
  if (computedRoot !== claimedRoot) {
    steps.push(step('path', 'Walk the proof path', false, computedRoot));
    return {
      verified: false,
      headline: 'This document does not belong to the batch the file says it does.',
      detail:
        'The proof path does not lead from this entry to the sealed root. The bundle has ' +
        'been assembled incorrectly or edited.',
      steps,
      leafHash: computedLeaf,
      computedRoot,
    };
  }
  steps.push(
    step(
      'path',
      'Walk the proof path',
      true,
      `${bundle.path.length} sibling hash${bundle.path.length === 1 ? '' : 'es'}`,
    ),
  );

  // ---- 5. The chain ------------------------------------------------------
  // Everything above is offline and proves only internal consistency. This is the
  // step that makes the verdict independent of whoever sent the file.
  const target: ChainTarget = {
    rpcUrl: options.rpcUrl || bundle.chain.rpc_hint || defaultRpc(bundle.chain.network),
    network: bundle.chain.network,
    contractId: bundle.chain.contract_id,
    namespace: bundle.chain.org_namespace,
  };

  let onChain;
  try {
    const confirmed = await verifyRootOnChain(target, bundle.seal.seq, claimedRoot);
    if (!confirmed) {
      steps.push(step('chain', 'Confirm against the Stellar network', false));
      return {
        verified: false,
        headline: 'The Stellar network does not hold this root.',
        detail:
          `The contract has no seal at sequence ${bundle.seal.seq} with this root. The books ` +
          'presented here are not the books that were sealed.',
        steps,
        leafHash: computedLeaf,
        computedRoot,
        network: target.network,
        contractId: target.contractId,
      };
    }
    onChain = await readSeal(target, bundle.seal.seq);
  } catch (cause) {
    const reason = cause instanceof ChainError ? cause.message : String(cause);
    steps.push(step('chain', 'Confirm against the Stellar network', false, reason));
    return {
      verified: false,
      headline: 'The Stellar network could not be reached, so this is unconfirmed.',
      detail:
        `${reason} Everything checkable offline passed: the document hashes correctly and ` +
        'its proof path is intact. Only the final confirmation against the network is ' +
        'missing, so try again or use a different RPC endpoint.',
      steps,
      leafHash: computedLeaf,
      computedRoot,
    };
  }

  steps.push(
    step(
      'chain',
      'Confirm against the Stellar network',
      true,
      `seal #${bundle.seal.seq} on ${target.network}`,
    ),
  );
  steps.push(step('done', 'Verified', true));

  const sealedAt = onChain?.sealedAt ?? undefined;
  const when = sealedAt ? sealedAt.toLocaleString() : 'a time recorded on the network';

  return {
    verified: true,
    headline: 'Verified. This document is exactly as it was when the books were sealed.',
    detail:
      `It was part of a batch of ${onChain?.count ?? bundle.seal.entry_count} entries sealed ` +
      `to the Stellar ${target.network} on ${when}, and has not changed since. ` +
      'The seal is recorded on a public network, so the date cannot have been back-dated.',
    steps,
    ...(sealedAt ? { sealedAt } : {}),
    sealSeq: bundle.seal.seq,
    entryCount: onChain?.count ?? bundle.seal.entry_count,
    onChainRoot: onChain?.root ?? claimedRoot,
    computedRoot,
    leafHash: computedLeaf,
    network: target.network,
    contractId: target.contractId,
    txHash: bundle.seal.tx_hash,
  };
}

/** Public RPC for a network, when the bundle names none. */
export function defaultRpc(network: string): string {
  return network === 'public'
    ? 'https://mainnet.sorobanrpc.com'
    : 'https://soroban-testnet.stellar.org';
}

/**
 * Parse a pasted or uploaded bundle.
 *
 * Accepts both the bare bundle and the `{ bundle: … }` envelope the export
 * endpoint returns, because a person who copies a response body out of a browser
 * will paste whichever they happened to see - and refusing one of them would be a
 * gratuitous way to fail.
 */
export function parseBundle(text: string): ProofBundle {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error('That is not valid JSON. Paste the whole proof file, including the braces.');
  }
  if (parsed && typeof parsed === 'object' && 'bundle' in parsed) {
    return (parsed as { bundle: ProofBundle }).bundle;
  }
  return parsed as ProofBundle;
}
