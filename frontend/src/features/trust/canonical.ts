/**
 * The canonical encoding and the Merkle tree, in TypeScript.
 *
 * **This is a deliberate second implementation of
 * `backend/app/modules/attestation/canonical.py` and `merkle.py`, and the
 * duplication is the entire point.**
 *
 * If the verifier called the server to check a proof, a verifier would have
 * gained nothing: a compromised backend would happily answer `valid: true` for
 * anything. The only way a bank's credit officer can get an answer that does not
 * depend on trusting us is to recompute the hash themselves, in their own
 * browser, and compare it against what the contract says - reading the contract
 * from an RPC endpoint they are free to change.
 *
 * So the encoding is written twice, and the two are proven equivalent against
 * shared golden vectors. That is a real maintenance cost, accepted on purpose,
 * because a single shared implementation would mean the verifier is running our
 * code.
 *
 * **Changing anything in this file changes what verifies.** It is append-only,
 * exactly like its Python twin: a new field or a new money scale means a new
 * `CANONICAL_VERSION` and a new branch here, never an edit in place. `spec.ts`
 * checks at runtime that this file and the server agree on the version, so a
 * half-deployed release says "the verifier is out of date" instead of failing
 * every proof for no visible reason.
 *
 * No dependencies. `BigInt` for the 128-bit integers, `TextEncoder` for UTF-8,
 * and Web Crypto for SHA-256 - all of which have been in every browser this
 * product targets for years, and none of which can be tampered with by a
 * bundler.
 */

/** Leading byte of every canonical encoding. */
export const CANONICAL_VERSION = 1;

/** Decimal places in the money column. Money is scaled by `10 ** MONEY_SCALE`. */
export const MONEY_SCALE = 4;

/** Width of an encoded integer: 16 bytes signed is `i128`. */
export const INT_WIDTH = 16;

/** Sentinel length marking an absent value, so `null` and `""` differ. */
const ABSENT = 0xffffffff;

/** Tag naming what kind of record this is, hashed into every leaf. */
const KIND_JOURNAL_ENTRY = 'journal_entry.v1';

/** Domain-separation prefixes. Leaves get `0x00`, interior nodes `0x01`. */
const LEAF_PREFIX = 0x00;
const NODE_PREFIX = 0x01;

const DIGEST_BYTES = 32;

/**
 * The field order, written out by hand.
 *
 * **Not derived from anything, and the order is part of the contract.** It must
 * match `FIELD_ORDER` in `canonical.py` exactly; `spec.ts` asserts that against
 * what the server publishes.
 */
const FIELD_ORDER: readonly [string, FieldKind][] = [
  ['organization_id', 'uuid'],
  ['entry_id', 'uuid'],
  ['entry_number', 'str'],
  ['entry_date', 'date'],
  ['currency', 'str'],
  // `status` is deliberately absent, matching `canonical.py`. A leaf commits to
  // what was recorded, not to what later happened to it: this ledger corrects by
  // reversal, so `posted` becoming `reversed` is the normal path for any entry,
  // and hashing the status would mean taking that path invalidated the entry's own
  // proof. The reversal is provable through its own leaf instead.
  ['total_debit', 'money'],
  ['total_credit', 'money'],
  ['narration', 'str'],
  ['reference', 'str'],
  ['counterparty', 'str'],
  ['source_type', 'str'],
  ['source_id', 'uuid'],
  ['reverses_id', 'uuid'],
  ['posted_at', 'instant'],
];

const LINE_FIELD_ORDER: readonly [string, FieldKind][] = [
  ['line_number', 'u32'],
  ['account_id', 'uuid'],
  ['debit', 'money'],
  ['credit', 'money'],
  ['description', 'str'],
];

type FieldKind = 'u32' | 'u64' | 'int' | 'str' | 'uuid' | 'date' | 'instant' | 'money';

/** A canonical payload, as it arrives inside a proof bundle. */
export interface EntryPayload {
  [field: string]: unknown;
  lines?: Array<Record<string, unknown>>;
}

/** Raised when a bundle cannot be encoded. Carries a message worth showing. */
export class CanonicalError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CanonicalError';
  }
}

// ---------------------------------------------------------------------------
// Primitive encoders
// ---------------------------------------------------------------------------
const utf8 = new TextEncoder();

function encU32(value: number): Uint8Array {
  if (!Number.isInteger(value) || value < 0 || value > 0xffffffff) {
    throw new CanonicalError(`${value} does not fit a u32`);
  }
  const out = new Uint8Array(4);
  new DataView(out.buffer).setUint32(0, value, false);
  return out;
}

function encU64(value: bigint): Uint8Array {
  if (value < 0n || value > 0xffffffffffffffffn) {
    throw new CanonicalError(`${value} does not fit a u64`);
  }
  const out = new Uint8Array(8);
  new DataView(out.buffer).setBigUint64(0, value, false);
  return out;
}

/**
 * A 16-byte signed big-endian integer - `i128`, two's complement.
 *
 * Fixed width rather than length-prefixed: a variable-width integer would let
 * `1` be encoded as one byte or as sixteen, and two encoders that disagreed
 * about which would produce different hashes for the same number while both
 * looking correct in isolation.
 */
function encInt(value: bigint): Uint8Array {
  const bits = BigInt(INT_WIDTH * 8);
  const limit = 1n << (bits - 1n);
  if (value >= limit || value < -limit) {
    throw new CanonicalError(`${value} does not fit an i128`);
  }
  let unsigned = value < 0n ? value + (1n << bits) : value;
  const out = new Uint8Array(INT_WIDTH);
  for (let index = INT_WIDTH - 1; index >= 0; index -= 1) {
    out[index] = Number(unsigned & 0xffn);
    unsigned >>= 8n;
  }
  return out;
}

function encBytes(value: Uint8Array | null): Uint8Array {
  if (value === null) return encU32(ABSENT);
  return concat([encU32(value.length), value]);
}

/**
 * Length-prefixed UTF-8.
 *
 * **No normalisation, no trimming, no case folding.** The bytes the server
 * hashed are the bytes hashed here. Normalising would make the hash depend on a
 * Unicode table version.
 */
function encStr(value: string | null | undefined): Uint8Array {
  if (value === null || value === undefined) return encU32(ABSENT);
  return encBytes(utf8.encode(value));
}

/** 16 raw bytes, or the absent sentinel. The byte form has one spelling. */
function encUuid(value: string | null | undefined): Uint8Array {
  if (value === null || value === undefined || value === '') return encU32(ABSENT);
  const hex = value.replace(/-/g, '').toLowerCase();
  if (!/^[0-9a-f]{32}$/.test(hex)) {
    throw new CanonicalError(`${value} is not a UUID`);
  }
  const raw = new Uint8Array(16);
  for (let index = 0; index < 16; index += 1) {
    raw[index] = Number.parseInt(hex.slice(index * 2, index * 2 + 2), 16);
  }
  return encBytes(raw);
}

/** A date as `YYYYMMDD` in a u32 - legible in a hex dump and in a failure. */
function encDate(value: string | null | undefined): Uint8Array {
  if (value === null || value === undefined || value === '') return encU32(ABSENT);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.slice(0, 10));
  if (!match) throw new CanonicalError(`${value} is not an ISO date`);
  const [, year, month, day] = match;
  return encU32(Number(year) * 10000 + Number(month) * 100 + Number(day));
}

/**
 * A timestamp as milliseconds since the Unix epoch.
 *
 * A value with no timezone is refused rather than assumed to be UTC. Guessing is
 * how a 5.5-hour shift enters a hash - and the server refuses the same thing, so
 * accepting it here would make the two implementations disagree on exactly the
 * inputs where it matters.
 */
function encInstant(value: string | null | undefined): Uint8Array {
  if (value === null || value === undefined || value === '') return encU32(ABSENT);
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(value)) {
    throw new CanonicalError(`${value} carries no timezone, so its instant is a guess`);
  }
  const millis = Date.parse(value);
  if (Number.isNaN(millis)) throw new CanonicalError(`${value} is not a timestamp`);
  return encU64(BigInt(millis));
}

/**
 * Money to whole minor units, exactly - by string arithmetic, never by `Number`.
 *
 * `Number('1234567.89') * 10000` is `12345678899.999998` on a double. Every
 * amount in a bundle is a string for this reason, and it has to stay a string all
 * the way into a `BigInt`.
 */
export function moneyMinor(value: string | number | null | undefined): bigint {
  if (value === null || value === undefined || value === '') return 0n;
  const text = String(value).trim();
  const match = /^(-?)(\d+)(?:\.(\d*))?$/.exec(text);
  if (!match) throw new CanonicalError(`${text} is not a decimal amount`);

  const [, sign, whole, fraction = ''] = match;
  if (fraction.length > MONEY_SCALE) {
    // Trailing zeroes beyond the scale are harmless; real digits are not, because
    // rounding here would put a figure on chain that is not the figure in the books.
    const excess = fraction.slice(MONEY_SCALE);
    if (/[^0]/.test(excess)) {
      throw new CanonicalError(
        `${text} carries more than ${MONEY_SCALE} decimal places and cannot be encoded without loss`,
      );
    }
  }
  const padded = (fraction + '0'.repeat(MONEY_SCALE)).slice(0, MONEY_SCALE);
  return BigInt(`${sign}${whole}${padded}`);
}

function encMoney(value: string | number | null | undefined): Uint8Array {
  return encInt(moneyMinor(value));
}

function concat(parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

function encodeField(kind: FieldKind, value: unknown): Uint8Array {
  switch (kind) {
    case 'u32':
      return encU32(asNumber(value, 'u32'));
    case 'u64':
      return encU64(BigInt(asScalar(value, 'u64')));
    case 'int':
      return encInt(BigInt(asScalar(value, 'int')));
    case 'str':
      return encStr(asText(value));
    case 'uuid':
      return encUuid(asNullableText(value));
    case 'date':
      return encDate(asNullableText(value));
    case 'instant':
      return encInstant(asNullableText(value));
    case 'money':
      return encMoney(asNullableText(value));
    default: {
      const exhaustive: never = kind;
      throw new CanonicalError(`unknown field kind ${String(exhaustive)}`);
    }
  }
}

/**
 * Narrowing helpers.
 *
 * These exist because `String(someObject)` is `"[object Object]"` - a perfectly
 * valid string that hashes to something the server never produced. A bundle whose
 * `narration` arrived as a nested object would then fail verification with "the
 * figures do not match", pointing the reader at fraud when the real problem is a
 * malformed file. Refusing loudly is the only honest option.
 */
function asText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  throw new CanonicalError(
    'A text field in this proof is not text, so the document cannot be hashed.',
  );
}

function asNullableText(value: unknown): string | null {
  return asText(value);
}

function asScalar(value: unknown, kind: string): string {
  if (typeof value === 'bigint') return value.toString();
  const text = asText(value);
  if (text === null || text === '') {
    throw new CanonicalError(`A ${kind} field in this proof is empty.`);
  }
  return text;
}

function asNumber(value: unknown, kind: string): number {
  if (typeof value === 'number') return value;
  const text = asScalar(value, kind);
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) {
    throw new CanonicalError(`A ${kind} field in this proof is not a number.`);
  }
  return parsed;
}

// ---------------------------------------------------------------------------
// The entry encoding
// ---------------------------------------------------------------------------
/**
 * The canonical bytes of one journal entry.
 *
 * Structure, in order:
 *
 *     version    u8      = CANONICAL_VERSION
 *     kind       str     = "journal_entry.v1"
 *     <fields>           in FIELD_ORDER
 *     line_count u32
 *     <lines>            each in LINE_FIELD_ORDER, ordered by line_number
 */
export function encodeEntry(payload: EntryPayload): Uint8Array {
  const parts: Uint8Array[] = [new Uint8Array([CANONICAL_VERSION]), encStr(KIND_JOURNAL_ENTRY)];

  for (const [name, kind] of FIELD_ORDER) {
    if (!(name in payload)) {
      throw new CanonicalError(`the entry is missing "${name}"`);
    }
    parts.push(encodeField(kind, payload[name]));
  }

  const lines = [...(payload.lines ?? [])].sort(
    (a, b) => Number(a['line_number']) - Number(b['line_number']),
  );
  if (lines.length === 0) {
    throw new CanonicalError('an entry with no lines cannot be sealed');
  }

  parts.push(encU32(lines.length));
  for (const line of lines) {
    for (const [name, kind] of LINE_FIELD_ORDER) {
      if (!(name in line)) {
        throw new CanonicalError(`a line is missing "${name}"`);
      }
      parts.push(encodeField(kind, line[name]));
    }
  }

  return concat(parts);
}

// ---------------------------------------------------------------------------
// Hashing
// ---------------------------------------------------------------------------
async function sha256(data: Uint8Array): Promise<Uint8Array> {
  if (!globalThis.crypto?.subtle) {
    // Web Crypto is unavailable over plain HTTP on a non-localhost origin. Worth
    // naming, because the symptom is otherwise "verification does not work" on a
    // deployment that is simply missing TLS.
    throw new CanonicalError(
      'This browser has no Web Crypto. Verification needs a secure context (https).',
    );
  }
  const digest = await globalThis.crypto.subtle.digest('SHA-256', data as BufferSource);
  return new Uint8Array(digest);
}

/** `SHA-256(0x00 || canonical(entry))` - the Merkle leaf for one entry. */
export async function leafHash(payload: EntryPayload): Promise<Uint8Array> {
  const encoded = encodeEntry(payload);
  return sha256(concat([new Uint8Array([LEAF_PREFIX]), encoded]));
}

export async function leafHashHex(payload: EntryPayload): Promise<string> {
  return toHex(await leafHash(payload));
}

/** `SHA-256(0x01 || left || right)` - an interior node. */
export async function nodeHash(left: Uint8Array, right: Uint8Array): Promise<Uint8Array> {
  return sha256(concat([new Uint8Array([NODE_PREFIX]), left, right]));
}

// ---------------------------------------------------------------------------
// Inclusion proofs
// ---------------------------------------------------------------------------
/** One sibling on the path from a leaf to the root. */
export interface ProofStep {
  side: 'left' | 'right';
  hash: string;
}

/**
 * Fold a leaf through its path and return the root it produces.
 *
 * **The path is consumed innermost-first**, matching RFC 6962's `PATH`. A path
 * folded in the wrong order still has the right length and the right hashes and
 * fails only at the final comparison - which reads as "the books were altered".
 * Getting this backwards is the single easiest way to accuse an honest business
 * of fraud, which is why the order is stated here and asserted in the tests.
 */
export async function foldPath(leaf: Uint8Array, path: readonly ProofStep[]): Promise<Uint8Array> {
  let accumulator = leaf;
  for (const step of path) {
    const sibling = fromHex(step.hash);
    if (sibling.length !== DIGEST_BYTES) {
      throw new CanonicalError(`a proof step is ${sibling.length} bytes, not ${DIGEST_BYTES}`);
    }
    if (step.side === 'left') {
      accumulator = await nodeHash(sibling, accumulator);
    } else if (step.side === 'right') {
      accumulator = await nodeHash(accumulator, sibling);
    } else {
      throw new CanonicalError(`a proof step has an unknown side "${String(step.side)}"`);
    }
  }
  return accumulator;
}

/** Whether folding `leaf` through `path` reproduces `root`. */
export async function verifyInclusion(
  leaf: Uint8Array,
  path: readonly ProofStep[],
  root: string,
): Promise<boolean> {
  try {
    return toHex(await foldPath(leaf, path)) === root.toLowerCase();
  } catch {
    // A malformed path is "not verified", not a crash. A hand-edited bundle
    // should show a red tick and an explanation.
    return false;
  }
}

/** RFC 6962 root over already-hashed leaves. Used by the tests, not the page. */
export async function merkleRoot(leaves: readonly Uint8Array[]): Promise<Uint8Array> {
  if (leaves.length === 0) throw new CanonicalError('a Merkle tree needs at least one leaf');
  if (leaves.length === 1) return leaves[0]!;

  let split = 1;
  while (split * 2 < leaves.length) split *= 2;
  return nodeHash(await perfectRoot(leaves.slice(0, split)), await merkleRoot(leaves.slice(split)));
}

async function perfectRoot(leaves: readonly Uint8Array[]): Promise<Uint8Array> {
  let level = [...leaves];
  while (level.length > 1) {
    const next: Uint8Array[] = [];
    for (let index = 0; index < level.length; index += 2) {
      next.push(await nodeHash(level[index]!, level[index + 1]!));
    }
    level = next;
  }
  return level[0]!;
}

// ---------------------------------------------------------------------------
// Hex
// ---------------------------------------------------------------------------
export function toHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function fromHex(hex: string): Uint8Array {
  const clean = hex.trim().toLowerCase();
  if (clean.length % 2 !== 0 || /[^0-9a-f]/.test(clean)) {
    throw new CanonicalError(`"${hex}" is not hex`);
  }
  const out = new Uint8Array(clean.length / 2);
  for (let index = 0; index < out.length; index += 1) {
    out[index] = Number.parseInt(clean.slice(index * 2, index * 2 + 2), 16);
  }
  return out;
}

/** Number of sibling hashes on the longest path, for display. */
export function treeDepth(count: number): number {
  if (count <= 1) return 0;
  return 32 - Math.clz32(count - 1);
}

/** The local half of the spec, for comparison against what the server publishes. */
export const LOCAL_SPEC = {
  version: CANONICAL_VERSION,
  kind: KIND_JOURNAL_ENTRY,
  money_scale: MONEY_SCALE,
  int_width: INT_WIDTH,
  absent: ABSENT,
  leaf_prefix: '00',
  node_prefix: '01',
  hash: 'sha256',
  merkle: 'rfc6962',
  fields: FIELD_ORDER.map(([name, type]) => ({ name, type })),
  line_fields: LINE_FIELD_ORDER.map(([name, type]) => ({ name, type })),
} as const;
