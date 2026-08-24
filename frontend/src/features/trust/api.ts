/**
 * Proof ledger API client.
 *
 * Money and large integers are strings throughout - `debit_minor` is a count of
 * paise that can exceed 2^53 over a business's lifetime, and JSON's only numeric
 * type is a double. Same rule as every other module here.
 *
 * Note which calls go through this file and which do not. Status, history,
 * sealing and configuring are ordinary authenticated API calls. **Verification is
 * not here**: it lives in `verify.ts` and talks to the chain directly, because a
 * verdict that came from our server would be worth exactly as much as our server.
 */
import { api } from '@/lib/api';

export type SealStatus = 'pending' | 'submitted' | 'confirmed' | 'failed';
export type SealTrigger = 'period_close' | 'schedule' | 'manual' | 'backfill';
export type SealCadence = 'on_period_close' | 'daily' | 'manual';

export interface Seal {
  id: string;
  seq: number;
  status: SealStatus;
  trigger: SealTrigger;

  merkle_root: string;
  prev_root: string;
  entry_count: number;
  /** Total debits in minor units, as a string. */
  debit_minor: string;

  /** The posting-time window that went on chain. Tiles forward by construction. */
  covered_from: string;
  covered_to: string;
  /** The accounting dates touched. Local only - these do not tile. */
  entry_date_from: string;
  entry_date_to: string;

  network: string | null;
  contract_id: string | null;
  tx_hash: string | null;
  ledger_sequence: string | null;

  /**
   * The network's timestamp. Null while a seal awaits confirmation, and the UI
   * must say so rather than invent one - this field is the entire basis of the
   * claim that a business cannot back-date its books.
   */
  sealed_at: string | null;
  submitted_at: string | null;
  confirmed_at: string | null;

  attempts: number;
  last_error: string | null;
  explorer_url: string | null;
  /** Sibling hashes needed to prove one entry from this seal. */
  tree_depth: number;
}

export interface ChainHealth {
  reachable: boolean;
  head: number | null;
  root: string | null;
  entries: number | null;
  sealed_at: string | null;
  admin: string | null;
  /** `false` is the condition worth shouting about: the chain and we disagree. */
  agrees_with_local: boolean | null;
  error: string | null;
}

export interface AttestationStatus {
  enabled: boolean;
  configured: boolean;
  ready: boolean;

  network: string | null;
  contract_id: string | null;
  contract_url: string | null;
  org_namespace: string | null;
  cadence: SealCadence;

  signer_public_key: string | null;
  /** True when the key is held outside this server - a stronger posture. */
  external_signer: boolean;
  registered_at: string | null;

  seals_confirmed: number;
  entries_sealed: number;
  unsealed_entries: number;
  oldest_unsealed_at: string | null;
  /** Age of the oldest unsealed entry. A growing figure is the failure mode. */
  days_unsealed: number | null;

  last_seal: Seal | null;
  open_seal: Seal | null;
  chain: ChainHealth;

  /** Already written for a human, worst first. Render in order. */
  warnings: string[];
}

export interface SealPage {
  items: Seal[];
  next_cursor: string | null;
  has_more: boolean;
  /** Computed server-side: a break in the chain is what this list is for. */
  continuous: boolean;
}

export interface NetworkInfo {
  enabled: boolean;
  network: 'testnet' | 'public' | null;
  contract_id: string | null;
  rpc_url: string;
  explorer_base: string;
  spec_version: number;
}

export interface CanonicalSpec {
  spec: {
    version: number;
    kind: string;
    money_scale: number;
    int_width: number;
    leaf_prefix: string;
    node_prefix: string;
    hash: string;
    merkle: string;
    fields: { name: string; type: string }[];
    line_fields: { name: string; type: string }[];
  };
}

export interface SealNowResult {
  seal: Seal | null;
  message: string;
}

export interface ReconcileResult {
  reconciled: boolean;
  reason: string | null;
  chain_head: number | null;
  chain_root: string | null;
  local_head: number | null;
  adjusted: number | null;
  agrees: boolean | null;
}

/** A proof bundle. Opaque on purpose - its shape is defined by its `format`. */
export interface ProofBundle {
  format: string;
  generated_at: string;
  chain: {
    network: string;
    contract_id: string;
    org_namespace: string;
    rpc_hint: string | null;
    explorer_tx: string | null;
  };
  seal: {
    seq: number;
    merkle_root: string;
    prev_root: string;
    entry_count: number;
    debit_minor: string;
    covered_from: string;
    covered_to: string;
    sealed_at: string | null;
    tx_hash: string | null;
    ledger: number | null;
    tree_depth: number;
  };
  leaf: { index: number; hash: string; canonical_version: number };
  path: { side: 'left' | 'right'; hash: string }[];
  entry: Record<string, unknown>;
  display: { _note: string; accounts: Record<string, { code: string; name: string }> };
  spec: CanonicalSpec['spec'];
  how_to_verify: string[];
}

export interface PublicSeal {
  seq: number;
  root: string;
  prev: string;
  entry_count: number;
  debit_minor: string;
  covered_from: string;
  covered_to: string;
  sealed_at: string;
}

export interface PublicChain {
  namespace: string;
  network: string;
  contract_id: string;
  head: number;
  root: string | null;
  entries: number | null;
  sealed_at: string | null;
  continuous: boolean;
  seals: PublicSeal[];
}

export const trustApi = {
  status: () => api.get<AttestationStatus>('/attestation/status'),
  network: () => api.get<NetworkInfo>('/attestation/network'),
  spec: () => api.get<CanonicalSpec>('/attestation/spec'),

  seals: (cursor?: string, limit = 20) =>
    api.get<SealPage>('/attestation/seals', {
      params: cursor ? { cursor, limit } : { limit },
    }),

  enable: (body: { cadence?: SealCadence; secret_key?: string; fund_on_testnet?: boolean }) =>
    api.post<AttestationStatus>('/attestation/enable', body),

  disable: () => api.post<AttestationStatus>('/attestation/disable', {}),

  setCadence: (cadence: SealCadence) =>
    api.patch<AttestationStatus>('/attestation/cadence', { cadence }),

  rotateSigner: (newAdmin: string) =>
    api.post<AttestationStatus>('/attestation/signer/rotate', { new_admin: newAdmin }),

  sealNow: () => api.post<SealNowResult>('/attestation/seals', {}),
  reconcile: () => api.post<ReconcileResult>('/attestation/reconcile', {}),
  drain: () =>
    api.post<{ processed: number; confirmed: number; failed: number; waiting: number }>(
      '/attestation/drain',
      {},
    ),

  /** The bundle for one entry. Export, then hand it to a counterparty. */
  proof: (journalEntryId: string) =>
    api.get<{ bundle: ProofBundle }>(`/attestation/proof/${journalEntryId}`),

  chainHealth: () =>
    api.get<{ enabled: boolean; reachable: boolean; network?: string; ledger?: number }>(
      '/attestation/chain/health',
    ),
};

/**
 * The public endpoints, for the unauthenticated verifier.
 *
 * Separate object rather than more methods on `trustApi`, so it is obvious at the
 * call site which surface a component is touching. The verifier page must never
 * reach for an authenticated route by accident.
 */
export const publicVerifyApi = {
  network: () => api.get<NetworkInfo>('/verify/network'),
  spec: () => api.get<CanonicalSpec>('/verify/spec'),
  chain: (namespace: string, limit = 12) =>
    api.get<PublicChain>(`/verify/chain/${namespace}`, { params: { limit } }),
};
