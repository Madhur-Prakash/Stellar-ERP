/**
 * The cross-implementation equivalence test.
 *
 * **This is the most important test in the frontend, and it is the one that makes
 * the verifier trustworthy.**
 *
 * The canonical encoding exists twice on purpose - once in Python, once here -
 * because a verifier who called our server for a verdict would have gained
 * nothing. The cost of that decision is that the two can silently drift, and the
 * failure mode is catastrophic and invisible: every proof would fail with "the
 * figures do not match", accusing honest businesses of tampering.
 *
 * So the golden vector below is copied from
 * `backend/tests/test_attestation_canonical.py`, byte for byte, and this file
 * asserts that this implementation reaches the same 32 bytes. If it fails, the two
 * halves have diverged - and the fix is to find out which one moved, never to
 * update the expected value.
 *
 * The vector deliberately exercises everything awkward at once: a null field, an
 * empty-string field (which must *not* hash the same as null), a value with
 * trailing zeroes, a non-ASCII narration with a rupee sign, a fractional amount,
 * three lines so the count matters, and a timezone-aware timestamp.
 */
import { describe, expect, it } from 'vitest';

import {
  CANONICAL_VERSION,
  CanonicalError,
  LOCAL_SPEC,
  encodeEntry,
  foldPath,
  fromHex,
  leafHashHex,
  merkleRoot,
  moneyMinor,
  nodeHash,
  toHex,
  treeDepth,
  verifyInclusion,
  type EntryPayload,
  type ProofStep,
} from '@/features/trust/canonical';

/**
 * The same entry as `GOLDEN_ENTRY` in the Python suite, rendered the way a proof
 * bundle carries it: money as strings, uuids as strings, the timestamp with a `Z`.
 *
 * That rendering is not incidental. It is exactly what `payload_to_json` produces
 * and exactly what crosses the wire, so this test covers the real path rather than
 * a convenient approximation of it.
 */
const GOLDEN_ENTRY: EntryPayload = {
  organization_id: '0192f3a4-5b6c-7d8e-9f01-234567890abc',
  entry_id: '0192f3a4-5b6c-7d8e-9f01-234567890def',
  entry_number: 'JV-2026-27-0001',
  entry_date: '2026-03-31',
  currency: 'INR',
  total_debit: '100.0000',
  total_credit: '100.0000',
  narration: 'Sale to Sharma & Sons - ₹100',
  reference: '',
  counterparty: null,
  source_type: 'invoice',
  source_id: '0192f3a4-5b6c-7d8e-9f01-2345678901ab',
  reverses_id: null,
  posted_at: '2026-03-31T09:15:30Z',
  lines: [
    {
      line_number: 1,
      account_id: '0192f3a4-5b6c-7d8e-9f01-100000000001',
      debit: '100.0000',
      credit: '0.0000',
      description: 'Receivable',
    },
    {
      line_number: 2,
      account_id: '0192f3a4-5b6c-7d8e-9f01-200000000002',
      debit: '0.0000',
      credit: '84.7500',
      description: null,
    },
    {
      line_number: 3,
      account_id: '0192f3a4-5b6c-7d8e-9f01-300000000003',
      debit: '0.0000',
      credit: '15.2500',
      description: 'IGST 18%',
    },
  ],
};

/**
 * ############################################################################
 * Copied from `GOLDEN_LEAF` in backend/tests/test_attestation_canonical.py.
 *
 * DO NOT UPDATE THIS TO MAKE A FAILING TEST PASS. If it fails, this
 * implementation and the Python one disagree, and every proof issued against
 * every seal on chain is affected. Find out which side moved.
 * ############################################################################
 */
const GOLDEN_LEAF = '6d86fbb3e6bbd2357897b39bf872145d2abf2c1c3e27448a4cf2f4d80600b605';

/** Also pinned in the Python suite. A second, coarser tripwire. */
const GOLDEN_LENGTH = 412;

describe('agreement with the Python implementation', () => {
  it('produces the golden leaf hash', async () => {
    expect(await leafHashHex(GOLDEN_ENTRY)).toBe(GOLDEN_LEAF);
  });

  it('produces the golden encoded length', () => {
    expect(encodeEntry(GOLDEN_ENTRY)).toHaveLength(GOLDEN_LENGTH);
  });

  it('leads the encoding with the version byte', () => {
    expect(encodeEntry(GOLDEN_ENTRY)[0]).toBe(CANONICAL_VERSION);
  });

  it('declares a spec matching the field order it encodes', () => {
    // The runtime check the verifier performs against `/verify/spec`. If this
    // drifts, the page would refuse valid proofs with a version-mismatch message.
    expect(LOCAL_SPEC.version).toBe(CANONICAL_VERSION);
    expect(LOCAL_SPEC.leaf_prefix).toBe('00');
    expect(LOCAL_SPEC.node_prefix).toBe('01');
    expect(LOCAL_SPEC.merkle).toBe('rfc6962');
    expect(LOCAL_SPEC.fields.map((field) => field.name)).toEqual([
      'organization_id',
      'entry_id',
      'entry_number',
      'entry_date',
      'currency',
      'total_debit',
      'total_credit',
      'narration',
      'reference',
      'counterparty',
      'source_type',
      'source_id',
      'reverses_id',
      'posted_at',
    ]);
  });

  it('does not hash the entry status', async () => {
    // The bug this guards against: hashing `status` meant that reversing an entry
    // - the normal correction path in this ledger - invalidated the entry's own
    // proof. A stray `status` key must make no difference.
    const withStatus = { ...GOLDEN_ENTRY, status: 'reversed' };
    expect(await leafHashHex(withStatus)).toBe(GOLDEN_LEAF);
  });
});

describe('sensitivity', () => {
  const cases: [string, unknown][] = [
    ['organization_id', '0192f3a4-5b6c-7d8e-9f01-2345678900ff'],
    ['entry_number', 'JV-2026-27-0002'],
    ['entry_date', '2026-04-01'],
    ['currency', 'USD'],
    ['total_debit', '100.0001'],
    ['narration', 'Sale to Sharma & Sons - ₹101'],
    ['reference', 'CHQ-1'],
    ['counterparty', 'Sharma & Sons'],
    ['source_type', 'billing'],
    ['posted_at', '2026-03-31T09:15:31Z'],
  ];

  it.each(cases)('changing %s changes the hash', async (fieldName, value) => {
    const tampered = { ...GOLDEN_ENTRY, [fieldName]: value };
    expect(await leafHashHex(tampered)).not.toBe(GOLDEN_LEAF);
  });

  it('distinguishes null from an empty string', async () => {
    // The single most likely encoding bug, and the reason for the ABSENT sentinel.
    const empty = await leafHashHex({ ...GOLDEN_ENTRY, reference: '' });
    const absent = await leafHashHex({ ...GOLDEN_ENTRY, reference: null });
    expect(empty).not.toBe(absent);
    expect(empty).toBe(GOLDEN_LEAF);
  });

  it('is insensitive to line order', async () => {
    const shuffled = { ...GOLDEN_ENTRY, lines: [...(GOLDEN_ENTRY.lines ?? [])].reverse() };
    expect(await leafHashHex(shuffled)).toBe(GOLDEN_LEAF);
  });

  it('changes when a line is dropped', async () => {
    const fewer = { ...GOLDEN_ENTRY, lines: (GOLDEN_ENTRY.lines ?? []).slice(0, 2) };
    expect(await leafHashHex(fewer)).not.toBe(GOLDEN_LEAF);
  });
});

describe('money', () => {
  it.each([
    ['0', 0n],
    ['0.0000', 0n],
    ['1', 10_000n],
    ['1.0000', 10_000n],
    ['100.00', 1_000_000n],
    ['100.0000', 1_000_000n],
    ['0.0001', 1n],
    ['-42.5000', -425_000n],
    ['99999999999999.9999', 999_999_999_999_999_999n],
  ])('scales %s exactly', (amount, minor) => {
    expect(moneyMinor(amount)).toBe(minor);
  });

  it('does not lose precision on a value a double would round', () => {
    // `Number('1234567.89') * 10000` is 12345678899.999998. This is why every
    // amount crosses the wire as a string and reaches a BigInt without passing
    // through Number.
    expect(moneyMinor('1234567.89')).toBe(12_345_678_900n);
  });

  it('refuses more precision than the column holds', () => {
    expect(() => moneyMinor('1.00001')).toThrow(CanonicalError);
  });

  it('accepts trailing zeroes beyond the scale', () => {
    // `100.00000` is the same number as `100.0000`; only real digits are a loss.
    expect(moneyMinor('100.00000')).toBe(1_000_000n);
  });

  it('treats null as zero', () => {
    expect(moneyMinor(null)).toBe(0n);
  });
});

describe('refusals', () => {
  it('refuses a naive timestamp', async () => {
    // The server refuses it too. Accepting it here would make the two disagree on
    // exactly the input where a 5.5-hour shift enters the hash.
    await expect(
      leafHashHex({ ...GOLDEN_ENTRY, posted_at: '2026-03-31T09:15:30' }),
    ).rejects.toThrow(CanonicalError);
  });

  it('refuses a missing field rather than defaulting it', async () => {
    const { currency: _dropped, ...incomplete } = GOLDEN_ENTRY;
    await expect(leafHashHex(incomplete)).rejects.toThrow(/currency/);
  });

  it('refuses an entry with no lines', async () => {
    await expect(leafHashHex({ ...GOLDEN_ENTRY, lines: [] })).rejects.toThrow(CanonicalError);
  });

  it('refuses an object where text is expected', async () => {
    // Without this, `String({})` is "[object Object]" - a valid string that hashes
    // to something the server never produced, and the reader would be told the
    // figures were altered when the file is simply malformed.
    await expect(leafHashHex({ ...GOLDEN_ENTRY, narration: { nested: true } })).rejects.toThrow(
      CanonicalError,
    );
  });
});

describe('the Merkle tree', () => {
  const leaf = async (index: number) => {
    const bytes = new Uint8Array(33);
    bytes[0] = 0x00;
    bytes[1] = index & 0xff;
    bytes[2] = (index >> 8) & 0xff;
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    return new Uint8Array(digest);
  };

  it('matches RFC 6962 for n = 3, not the Bitcoin construction', async () => {
    const leaves = [await leaf(0), await leaf(1), await leaf(2)];
    const rfc = await nodeHash(await nodeHash(leaves[0]!, leaves[1]!), leaves[2]!);
    const bitcoin = await nodeHash(
      await nodeHash(leaves[0]!, leaves[1]!),
      await nodeHash(leaves[2]!, leaves[2]!),
    );

    expect(toHex(await merkleRoot(leaves))).toBe(toHex(rfc));
    expect(toHex(await merkleRoot(leaves))).not.toBe(toHex(bitcoin));
  });

  it('verifies inclusion for every leaf across many tree sizes', async () => {
    for (const count of [1, 2, 3, 4, 5, 7, 8, 9, 16, 17, 33]) {
      const leaves = await Promise.all(
        Array.from({ length: count }, (_unused, index) => leaf(index)),
      );
      const root = toHex(await merkleRoot(leaves));

      for (let index = 0; index < count; index += 1) {
        // Build the path the way the server does, then fold it the way the page
        // does - the two halves of the same claim.
        const path = await referencePath(index, leaves);
        expect(await verifyInclusion(leaves[index]!, path, root), `leaf ${index} of ${count}`).toBe(
          true,
        );
      }
    }
  });

  it('rejects a forged leaf', async () => {
    const leaves = await Promise.all(Array.from({ length: 8 }, (_u, i) => leaf(i)));
    const root = toHex(await merkleRoot(leaves));
    const path = await referencePath(0, leaves);
    expect(await verifyInclusion(await leaf(999), path, root)).toBe(false);
  });

  it('rejects a path with the sides flipped', async () => {
    const leaves = await Promise.all(Array.from({ length: 8 }, (_u, i) => leaf(i)));
    const root = toHex(await merkleRoot(leaves));
    const path = await referencePath(3, leaves);
    // Annotated rather than `as const` per element: the annotation is what makes
    // the literal narrow, and eslint rightly objects to an assertion that only
    // restates what a declared type already says.
    const flipped: ProofStep[] = path.map((s) => ({
      side: s.side === 'left' ? 'right' : 'left',
      hash: s.hash,
    }));
    expect(await verifyInclusion(leaves[3]!, flipped, root)).toBe(false);
  });

  it('bounds a path by the tree depth', async () => {
    const leaves = await Promise.all(Array.from({ length: 12 }, (_u, i) => leaf(i)));
    const path = await referencePath(5, leaves);
    expect(path.length).toBeLessThanOrEqual(treeDepth(12));
  });

  it('folds a single-leaf tree to the leaf itself', async () => {
    const only = await leaf(0);
    expect(toHex(await foldPath(only, []))).toBe(toHex(only));
    expect(toHex(await merkleRoot([only]))).toBe(toHex(only));
  });

  it('round-trips hex', () => {
    const bytes = new Uint8Array([0, 1, 15, 16, 255]);
    expect(toHex(bytes)).toBe('00010f10ff');
    expect(Array.from(fromHex('00010f10ff'))).toEqual([0, 1, 15, 16, 255]);
  });
});

/**
 * RFC 6962 `PATH`, written here so the test builds paths independently of the
 * server rather than trusting a fixture.
 *
 * Innermost sibling first, matching the Python `inclusion_proof`. The order is the
 * detail most likely to be got wrong, and getting it wrong produces a path of the
 * right length with the right hashes that fails only at the final comparison.
 */
async function referencePath(
  index: number,
  leaves: readonly Uint8Array[],
): Promise<{ side: 'left' | 'right'; hash: string }[]> {
  if (leaves.length === 1) return [];

  let split = 1;
  while (split * 2 < leaves.length) split *= 2;

  if (index < split) {
    return [
      ...(await referencePath(index, leaves.slice(0, split))),
      { side: 'right' as const, hash: toHex(await merkleRoot(leaves.slice(split))) },
    ];
  }
  return [
    ...(await referencePath(index - split, leaves.slice(split))),
    { side: 'left' as const, hash: toHex(await merkleRoot(leaves.slice(0, split))) },
  ];
}
