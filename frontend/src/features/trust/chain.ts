/**
 * Reading the proof ledger contract straight from the browser.
 *
 * This module is what makes a verdict independent of our servers. The verifier
 * recomputes the hash locally (`canonical.ts`) and then asks the *contract*
 * whether that root is what was sealed - over an RPC endpoint the reader can
 * change. Our backend is not in the path, so it being compromised, offline, or
 * actively hostile does not change the answer.
 *
 * **The Stellar SDK is loaded dynamically, and that is a deliberate performance
 * decision.** It is a few hundred kilobytes of XDR machinery that the accounting,
 * sales, inventory and analytics screens have no use for. A static import would
 * put it in the main bundle and make every user of the ERP download a blockchain
 * library to open the billing screen. `await import(...)` keeps it on the two
 * routes that need it, and the module-level cache means a verifier checking six
 * invoices pays for it once.
 *
 * The import is typed as `typeof import('@stellar/stellar-sdk')`, so everything
 * below is fully checked despite being loaded at runtime. That matters more than
 * it sounds: an earlier draft reached into the module through `any` to tolerate
 * two possible SDK shapes, which bought nothing - the dependency is pinned - and
 * cost every type guarantee in the file.
 *
 * Reads are `simulateTransaction`: no fee, no signature, no wallet, nothing
 * submitted. That is what lets a bank's credit officer verify with no Stellar
 * account and no idea they are talking to a blockchain.
 */

/*
 * `Buffer` is a Node global, and nothing in a browser provides one.
 *
 * This import used to be absent, on the belief that Vite polyfills it. Vite does
 * not: there is no polyfill plugin and no `define` for it in `vite.config.ts`. It
 * appeared to work only because the dev server's dependency pre-bundling left the
 * SDK's own copy attached to `globalThis`, which the production build does not -
 * so `/verify` reached step 5 and died with `ReferenceError: Buffer is not defined`
 * on the deployed site while every local check passed.
 *
 * Importing it explicitly is the fix. The package is already here as a dependency
 * of `@stellar/stellar-base`, and is declared in our own `package.json` as well so
 * that a hoisting change in a future install cannot quietly remove it again.
 */
import { Buffer } from 'buffer';

/** A source account for simulation only. Never signs, never submits. */
const NULL_ACCOUNT = 'GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF';

const PASSPHRASES: Record<string, string> = {
  testnet: 'Test SDF Network ; September 2015',
  public: 'Public Global Stellar Network ; September 2015',
};

/** Contract error codes that mean "not there", which is an answer, not a fault. */
const NOT_FOUND_CODES = [2, 7] as const;

export interface ChainTarget {
  rpcUrl: string;
  network: string;
  contractId: string;
  namespace: string;
}

export interface OnChainSeal {
  seq: number;
  root: string;
  prev: string;
  count: number;
  debitMinor: string;
  coveredFrom: Date;
  coveredTo: Date;
  /** The network's own timestamp. The basis of the whole claim. */
  sealedAt: Date;
}

export interface OnChainBook {
  head: number;
  root: string;
  entries: number;
  sealedAt: Date | null;
  admin: string;
}

export class ChainError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ChainError';
  }
}

// `import type` emits no runtime import, so the SDK is still only fetched by the
// dynamic `import()` below - the types are free.
import type * as StellarSdkTypes from '@stellar/stellar-sdk';

type StellarSdk = typeof StellarSdkTypes;

let sdkPromise: Promise<StellarSdk> | null = null;

/**
 * Load the SDK once per page.
 *
 * Cached as the *promise*, not the module, so six concurrent verifications share
 * one download rather than starting six. A failure clears the cache so a later
 * attempt can retry instead of replaying the rejection forever.
 */
async function sdk(): Promise<StellarSdk> {
  sdkPromise ??= import('@stellar/stellar-sdk').catch((cause: unknown) => {
    sdkPromise = null;
    throw new ChainError(
      'The Stellar library could not be loaded, so the chain cannot be read directly. ' +
        `Check your connection and reload. (${describe(cause)})`,
    );
  });
  return sdkPromise;
}

function describe(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function passphraseFor(network: string): string {
  const passphrase = PASSPHRASES[network];
  if (!passphrase) throw new ChainError(`Unknown Stellar network "${network}".`);
  return passphrase;
}

function hexToBytes(hex: string): Buffer {
  const clean = hex.trim().toLowerCase();
  if (!/^[0-9a-f]+$/.test(clean) || clean.length % 2 !== 0) {
    throw new ChainError(`"${hex}" is not hex.`);
  }
  const out = new Uint8Array(clean.length / 2);
  for (let index = 0; index < out.length; index += 1) {
    out[index] = Number.parseInt(clean.slice(index * 2, index * 2 + 2), 16);
  }
  // The SDK's `nativeToScVal` wants a Buffer for `bytes` - a plain Uint8Array is
  // silently encoded as a vector of numbers instead, which the contract rejects.
  // `Buffer` is imported at the top of this module; it is not a browser global.
  return Buffer.from(out);
}

function bytesToHex(value: unknown): string {
  if (value instanceof Uint8Array) {
    return Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  if (typeof value === 'string') return value.toLowerCase();
  return '';
}

/**
 * Read one field from a decoded contract struct.
 *
 * `scValToNative` renders a Soroban struct as a plain object. Reaching into it
 * through `unknown` rather than `any` keeps the type checker involved: every value
 * that leaves this module goes through one of the narrowing helpers below, so a
 * field that turns out to be a different shape after an SDK upgrade is a type
 * error here rather than `NaN` on the screen.
 */
function field(raw: unknown, name: string): unknown {
  if (typeof raw !== 'object' || raw === null) return undefined;
  if (raw instanceof Map) return (raw as Map<string, unknown>).get(name);
  return (raw as Record<string, unknown>)[name];
}

function asNumber(value: unknown): number {
  if (typeof value === 'number') return value;
  if (typeof value === 'bigint') return Number(value);
  if (typeof value === 'string') return Number(value);
  return 0;
}

/**
 * A contract integer as a decimal string, without going through `Number`.
 *
 * `debits` is an `i128` of paise and can exceed 2^53, so stringifying it via a
 * double would silently round a control total. `bigint` and `string` are the two
 * shapes the SDK produces; anything else reads as zero rather than
 * `[object Object]`.
 */
function asIntegerString(value: unknown): string {
  if (typeof value === 'bigint') return value.toString();
  if (typeof value === 'number') return Math.trunc(value).toString();
  if (typeof value === 'string' && /^-?\d+$/.test(value.trim())) return value.trim();
  return '0';
}

/** A decoded contract `Address` as its `G…`/`C…` string. */
function asAddress(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'object' && value !== null) {
    const candidate = value as { address?: unknown; toString?: () => string };
    if (typeof candidate.address === 'string') return candidate.address;
  }
  return '';
}

function instant(value: unknown): Date {
  return new Date(asNumber(value) * 1000);
}

/**
 * Invoke a read-only contract function and return its decoded result.
 *
 * `null` for the two "not there" outcomes - an unregistered namespace and a
 * missing seal - because both are legitimate answers a verifier needs to see as
 * "not verified" rather than as a transport error.
 */
async function simulate(
  target: ChainTarget,
  functionName: string,
  args: (lib: StellarSdk) => ReturnType<StellarSdk['nativeToScVal']>[],
): Promise<unknown> {
  const lib = await sdk();

  const server = new lib.rpc.Server(target.rpcUrl, {
    // A self-hosted RPC on plain http is a choice the operator has already made.
    allowHttp: target.rpcUrl.startsWith('http://'),
  });

  const contract = new lib.Contract(target.contractId);
  const source = new lib.Account(NULL_ACCOUNT, '0');

  const transaction = new lib.TransactionBuilder(source, {
    fee: '1000',
    networkPassphrase: passphraseFor(target.network),
  })
    .addOperation(contract.call(functionName, ...args(lib)))
    .setTimeout(30)
    .build();

  let simulated: Awaited<ReturnType<typeof server.simulateTransaction>>;
  try {
    simulated = await server.simulateTransaction(transaction);
  } catch (cause) {
    throw new ChainError(
      `The Stellar RPC at ${target.rpcUrl} could not be reached. ` +
        `You can point the verifier at a different endpoint. (${describe(cause)})`,
    );
  }

  if (lib.rpc.Api.isSimulationError(simulated)) {
    const detail = simulated.error;
    if (NOT_FOUND_CODES.some((code) => detail.includes(`#${String(code)}`))) return null;
    throw new ChainError(`The contract refused the read: ${detail.slice(0, 200)}`);
  }

  const retval = simulated.result?.retval;
  if (!retval) return null;
  return lib.scValToNative(retval) as unknown;
}

/** Read one seal, or `null` if the contract holds none at that sequence. */
export async function readSeal(target: ChainTarget, seq: number): Promise<OnChainSeal | null> {
  const raw = await simulate(target, 'get', (lib) => [
    lib.nativeToScVal(hexToBytes(target.namespace), { type: 'bytes' }),
    lib.nativeToScVal(seq, { type: 'u32' }),
  ]);
  if (raw === null || raw === undefined) return null;

  return {
    seq: asNumber(field(raw, 'seq')),
    root: bytesToHex(field(raw, 'root')),
    prev: bytesToHex(field(raw, 'prev')),
    count: asNumber(field(raw, 'count')),
    debitMinor: asIntegerString(field(raw, 'debits')),
    coveredFrom: instant(field(raw, 'from')),
    coveredTo: instant(field(raw, 'to')),
    sealedAt: instant(field(raw, 'at')),
  };
}

/** Read the head of a namespace's chain. */
export async function readBook(target: ChainTarget): Promise<OnChainBook | null> {
  const raw = await simulate(target, 'latest', (lib) => [
    lib.nativeToScVal(hexToBytes(target.namespace), { type: 'bytes' }),
  ]);
  if (raw === null || raw === undefined) return null;

  const sealedAt = asNumber(field(raw, 'sealed_at'));
  return {
    head: asNumber(field(raw, 'head')),
    root: bytesToHex(field(raw, 'root')),
    entries: asNumber(field(raw, 'entries')),
    sealedAt: sealedAt ? new Date(sealedAt * 1000) : null,
    admin: asAddress(field(raw, 'admin')),
  };
}

/**
 * Ask the contract directly whether a root is what it holds at a sequence.
 *
 * One call, one boolean - the cheapest form of the only question that matters.
 * Used alongside {@link readSeal} rather than instead of it: `verify` settles it,
 * and the full seal supplies the timestamp and control totals a person wants to
 * read next to the green tick.
 */
export async function verifyRootOnChain(
  target: ChainTarget,
  seq: number,
  root: string,
): Promise<boolean> {
  const raw = await simulate(target, 'verify', (lib) => [
    lib.nativeToScVal(hexToBytes(target.namespace), { type: 'bytes' }),
    lib.nativeToScVal(seq, { type: 'u32' }),
    lib.nativeToScVal(hexToBytes(root), { type: 'bytes' }),
  ]);
  return raw === true;
}

/** Explorer link for a transaction, built from the network a seal recorded. */
export function explorerTxUrl(network: string, txHash: string): string {
  const segment = network === 'public' ? 'public' : 'testnet';
  return `https://stellar.expert/explorer/${segment}/tx/${txHash}`;
}

/** Explorer link for the contract itself. */
export function explorerContractUrl(network: string, contractId: string): string {
  const segment = network === 'public' ? 'public' : 'testnet';
  return `https://stellar.expert/explorer/${segment}/contract/${contractId}`;
}
