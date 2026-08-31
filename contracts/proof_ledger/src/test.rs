//! Contract tests.
//!
//! Written adversarially rather than as a happy-path demo. The contract's whole
//! value is what it *refuses*, so most of these assert a panic: an out-of-order
//! sequence, a broken chain, a re-seal, an empty seal, a stranger signing. A test
//! suite for this contract that only proved sealing works would prove nothing
//! worth knowing.
//!
//! `#[should_panic(expected = "...")]` matches on the host's error text, which
//! includes the contract error code - `Error(Contract, #3)` for
//! [`Error::SequenceOutOfOrder`]. Matching the code rather than a message keeps
//! the assertions pinned to the enum discriminants, so renaming a variant cannot
//! silently make a test pass for the wrong reason.

use soroban_sdk::{
    testutils::{Address as _, Ledger as _},
    Address, BytesN, Env,
};

use crate::{Book, ProofLedger, ProofLedgerClient, Seal};

/// Deterministic 32-byte values, so a failing assertion names a recognisable
/// root instead of a wall of hex.
fn root(env: &Env, tag: u8) -> BytesN<32> {
    let mut bytes = [0u8; 32];
    // Fill every byte, not just the first: a root that is mostly zeroes is one
    // typo away from the genesis sentinel, and a test that accidentally built the
    // sentinel would fail for a reason unrelated to what it is checking.
    for (index, slot) in bytes.iter_mut().enumerate() {
        *slot = tag
            .wrapping_add(index as u8)
            .wrapping_mul(7)
            .wrapping_add(1);
    }
    BytesN::from_array(env, &bytes)
}

fn genesis(env: &Env) -> BytesN<32> {
    BytesN::from_array(env, &[0u8; 32])
}

/// One day in seconds, for building period boundaries that tile forwards.
const DAY: u64 = 86_400;

struct Fixture {
    env: Env,
    client: ProofLedgerClient<'static>,
    org: BytesN<32>,
    admin: Address,
}

fn setup() -> Fixture {
    let env = Env::default();
    // Every test signs as whoever it needs to; the authorisation-specific tests
    // turn this off and assert the real requirement.
    env.mock_all_auths();

    let contract_id = env.register(ProofLedger, ());
    let client = ProofLedgerClient::new(&env, &contract_id);
    let org = BytesN::from_array(&env, &[0xAB; 32]);
    let admin = Address::generate(&env);

    client.register(&org, &admin);

    Fixture {
        env,
        client,
        org,
        admin,
    }
}

/// Seal period `n` (1-indexed), chaining from `prev`.
fn seal_nth(f: &Fixture, n: u32, prev: &BytesN<32>, count: u32) -> Seal {
    let from = (n as u64 - 1) * 30 * DAY;
    let to = from + 29 * DAY;
    f.client.seal(
        &f.org,
        &n,
        &root(&f.env, n as u8),
        prev,
        &count,
        &(i128::from(count) * 10_000),
        &from,
        &to,
    )
}

// -----------------------------------------------------------------------------
// Registration
// -----------------------------------------------------------------------------
#[test]
fn register_opens_an_empty_book() {
    let f = setup();
    let book = f.client.latest(&f.org);

    assert_eq!(book.head, 0);
    assert_eq!(book.root, genesis(&f.env));
    assert_eq!(book.sealed_at, 0);
    assert_eq!(book.covered_to, 0);
    assert_eq!(book.entries, 0);
    assert_eq!(book.admin, f.admin);
    assert!(f.client.is_registered(&f.org));
}

#[test]
fn is_registered_is_false_for_an_unknown_namespace() {
    let f = setup();
    let other = BytesN::from_array(&f.env, &[0x11; 32]);
    assert!(!f.client.is_registered(&other));
}

#[test]
#[should_panic(expected = "Error(Contract, #1)")]
fn register_refuses_a_second_book_for_one_namespace() {
    // Re-registering would be a way to abandon an inconvenient chain and start a
    // fresh one under the same identity.
    let f = setup();
    let usurper = Address::generate(&f.env);
    f.client.register(&f.org, &usurper);
}

#[test]
#[should_panic(expected = "Error(Contract, #2)")]
fn sealing_an_unregistered_namespace_is_refused() {
    let f = setup();
    let stranger = BytesN::from_array(&f.env, &[0x22; 32]);
    f.client.seal(
        &stranger,
        &1,
        &root(&f.env, 1),
        &genesis(&f.env),
        &10,
        &100_000,
        &0,
        &DAY,
    );
}

// -----------------------------------------------------------------------------
// The happy path, and the chain it builds
// -----------------------------------------------------------------------------
#[test]
fn the_first_seal_chains_from_genesis() {
    let f = setup();
    let first = seal_nth(&f, 1, &genesis(&f.env), 12);

    assert_eq!(first.seq, 1);
    assert_eq!(first.prev, genesis(&f.env));
    assert_eq!(first.count, 12);

    let book = f.client.latest(&f.org);
    assert_eq!(book.head, 1);
    assert_eq!(book.root, first.root);
    assert_eq!(book.entries, 12);
    assert_eq!(book.covered_to, first.to);
}

#[test]
fn seals_form_an_unbroken_chain() {
    let f = setup();

    let mut prev = genesis(&f.env);
    for n in 1..=6u32 {
        let seal = seal_nth(&f, n, &prev, n * 3);
        assert_eq!(seal.prev, prev, "seal {n} must chain from its predecessor");
        prev = seal.root;
    }

    let book = f.client.latest(&f.org);
    assert_eq!(book.head, 6);
    // 3 + 6 + 9 + 12 + 15 + 18
    assert_eq!(book.entries, 63);

    // Walk it back independently of the book, which is what a verifier does.
    let mut expected_prev = genesis(&f.env);
    for n in 1..=6u32 {
        let seal = f.client.get(&f.org, &n);
        assert_eq!(seal.prev, expected_prev);
        expected_prev = seal.root;
    }
}

#[test]
fn the_network_sets_the_timestamp_not_the_caller() {
    // The single most important property in the contract. `seal` has no `at`
    // parameter at all, so this test's job is to prove the value comes from the
    // ledger and moves when the ledger does.
    let f = setup();

    f.env.ledger().with_mut(|l| l.timestamp = 1_700_000_000);
    let first = seal_nth(&f, 1, &genesis(&f.env), 5);
    assert_eq!(first.at, 1_700_000_000);

    f.env.ledger().with_mut(|l| l.timestamp = 1_700_086_400);
    let second = seal_nth(&f, 2, &first.root, 5);
    assert_eq!(second.at, 1_700_086_400);

    assert!(
        second.at > first.at,
        "a later seal must carry a later network timestamp, which is what makes \
         back-dating visible"
    );
}

// -----------------------------------------------------------------------------
// Sequencing - the idempotency guarantee
// -----------------------------------------------------------------------------
#[test]
#[should_panic(expected = "Error(Contract, #3)")]
fn a_duplicate_submission_is_rejected_by_the_contract() {
    // This is the ambiguous-failure case: the worker's submission timed out, it
    // does not know whether the seal landed, and it retries. Idempotency is
    // enforced here, by consensus, not by the caller's retry logic.
    let f = setup();
    let first = seal_nth(&f, 1, &genesis(&f.env), 8);
    let _ = first;
    seal_nth(&f, 1, &genesis(&f.env), 8);
}

#[test]
#[should_panic(expected = "Error(Contract, #3)")]
fn a_skipped_sequence_is_refused() {
    // A business cannot quietly omit a period: the gap leaves `head` behind and
    // the next seal is refused, so skipping is not a silent act.
    let f = setup();
    let first = seal_nth(&f, 1, &genesis(&f.env), 4);
    seal_nth(&f, 3, &first.root, 4);
}

#[test]
#[should_panic(expected = "Error(Contract, #3)")]
fn resealing_an_earlier_sequence_is_refused() {
    let f = setup();
    let first = seal_nth(&f, 1, &genesis(&f.env), 4);
    let second = seal_nth(&f, 2, &first.root, 4);
    let _ = second;
    // Attempt to overwrite period 1 with different content.
    f.client.seal(
        &f.org,
        &1,
        &root(&f.env, 99),
        &genesis(&f.env),
        &4,
        &40_000,
        &0,
        &(29 * DAY),
    );
}

#[test]
#[should_panic(expected = "Error(Contract, #3)")]
fn a_first_seal_numbered_zero_is_refused() {
    // Sequences start at 1 so that `head == 0` unambiguously means "empty book".
    let f = setup();
    f.client.seal(
        &f.org,
        &0,
        &root(&f.env, 1),
        &genesis(&f.env),
        &1,
        &100,
        &0,
        &DAY,
    );
}

// -----------------------------------------------------------------------------
// Chain continuity
// -----------------------------------------------------------------------------
#[test]
#[should_panic(expected = "Error(Contract, #4)")]
fn a_seal_whose_prev_does_not_match_is_refused() {
    // The local database has diverged from the chain. Stopping here is the point:
    // a fork that were allowed to proceed would produce two internally consistent
    // histories and no way to tell which the verifier was shown.
    let f = setup();
    let first = seal_nth(&f, 1, &genesis(&f.env), 6);
    let _ = first;
    f.client.seal(
        &f.org,
        &2,
        &root(&f.env, 2),
        &root(&f.env, 77), // not the first seal's root
        &6,
        &60_000,
        &(30 * DAY),
        &(59 * DAY),
    );
}

#[test]
#[should_panic(expected = "Error(Contract, #4)")]
fn the_first_seal_must_chain_from_genesis() {
    let f = setup();
    f.client.seal(
        &f.org,
        &1,
        &root(&f.env, 1),
        &root(&f.env, 5), // should be all zeroes
        &6,
        &60_000,
        &0,
        &(29 * DAY),
    );
}

#[test]
fn rewriting_history_requires_resealing_everything_after_it() {
    // Not a refusal but the consequence of the refusals, and the reason the chain
    // matters: having sealed three periods, there is no call that alters period 2.
    // The only route to a different period-2 root is a fresh book, and a fresh
    // book cannot be opened for a namespace that already has one.
    let f = setup();

    let s1 = seal_nth(&f, 1, &genesis(&f.env), 5);
    let s2 = seal_nth(&f, 2, &s1.root, 5);
    let s3 = seal_nth(&f, 3, &s2.root, 5);

    // Every seal is still exactly what it was.
    assert_eq!(f.client.get(&f.org, &1).root, s1.root);
    assert_eq!(f.client.get(&f.org, &2).root, s2.root);
    assert_eq!(f.client.get(&f.org, &3).root, s3.root);

    // And the head still points at the newest.
    assert_eq!(f.client.latest(&f.org).head, 3);
}

// -----------------------------------------------------------------------------
// Content validation
// -----------------------------------------------------------------------------
#[test]
#[should_panic(expected = "Error(Contract, #5)")]
fn an_empty_seal_is_refused() {
    // Sealing nothing is not an attestation, and permitting it would let a
    // business pad its chain with empty periods to bury a gap.
    let f = setup();
    f.client.seal(
        &f.org,
        &1,
        &root(&f.env, 1),
        &genesis(&f.env),
        &0,
        &0,
        &0,
        &DAY,
    );
}

#[test]
#[should_panic(expected = "Error(Contract, #8)")]
fn a_root_of_all_zeroes_is_refused() {
    // That value is the genesis sentinel. Accepting it as a real root would make
    // the next seal's `prev` check ambiguous.
    let f = setup();
    f.client.seal(
        &f.org,
        &1,
        &genesis(&f.env),
        &genesis(&f.env),
        &3,
        &300,
        &0,
        &DAY,
    );
}

#[test]
#[should_panic(expected = "Error(Contract, #6)")]
fn a_period_ending_before_it_starts_is_refused() {
    let f = setup();
    f.client.seal(
        &f.org,
        &1,
        &root(&f.env, 1),
        &genesis(&f.env),
        &3,
        &300,
        &(10 * DAY),
        &(2 * DAY),
    );
}

#[test]
#[should_panic(expected = "Error(Contract, #6)")]
fn periods_must_tile_forwards() {
    // A reversal dated into a later period is a new leaf in that later period,
    // never an edit to a sealed one - so the accounting model never needs to seal
    // backwards, and forbidding it removes the only shape in which a fabricated
    // period could be interleaved between two real ones.
    let f = setup();
    let first = seal_nth(&f, 1, &genesis(&f.env), 5); // covers day 0..29
    f.client.seal(
        &f.org,
        &2,
        &root(&f.env, 2),
        &first.root,
        &5,
        &500,
        &(10 * DAY), // starts inside period 1
        &(40 * DAY),
    );
}

#[test]
fn a_negative_control_total_is_accepted() {
    // Not every period nets positive, and the contract is not an accountant. Its
    // job is to store what it was given unchanged; deciding whether a figure is
    // plausible is the verifier's, who has the statements to compare it against.
    let f = setup();
    let seal = f.client.seal(
        &f.org,
        &1,
        &root(&f.env, 1),
        &genesis(&f.env),
        &3,
        &-4_250,
        &0,
        &DAY,
    );
    assert_eq!(seal.debits, -4_250);
}

// -----------------------------------------------------------------------------
// Reads
// -----------------------------------------------------------------------------
#[test]
#[should_panic(expected = "Error(Contract, #7)")]
fn reading_a_missing_seal_is_refused() {
    let f = setup();
    f.client.get(&f.org, &9);
}

#[test]
fn verify_answers_without_shipping_the_seal_back() {
    let f = setup();
    let first = seal_nth(&f, 1, &genesis(&f.env), 7);

    assert!(f.client.verify(&f.org, &1, &first.root));
    assert!(!f.client.verify(&f.org, &1, &root(&f.env, 200)));
    // A sequence that does not exist reads as "not verified" rather than as a
    // transport error, so a hand-edited proof bundle shows a red tick and not a
    // crash.
    assert!(!f.client.verify(&f.org, &2, &first.root));
}

#[test]
fn history_returns_newest_first_and_is_bounded() {
    let f = setup();

    let mut prev = genesis(&f.env);
    for n in 1..=5u32 {
        prev = seal_nth(&f, n, &prev, 2).root;
    }

    let page = f.client.history(&f.org, &0, &3);
    assert_eq!(page.len(), 3);
    assert_eq!(page.get(0).unwrap().seq, 5);
    assert_eq!(page.get(1).unwrap().seq, 4);
    assert_eq!(page.get(2).unwrap().seq, 3);

    // Paging back from a cursor.
    let older = f.client.history(&f.org, &2, &10);
    assert_eq!(older.len(), 2);
    assert_eq!(older.get(0).unwrap().seq, 2);
    assert_eq!(older.get(1).unwrap().seq, 1);

    // `limit = 0` means "the maximum page", not "nothing".
    assert_eq!(f.client.history(&f.org, &0, &0).len(), 5);
}

#[test]
fn history_of_an_empty_book_is_empty() {
    let f = setup();
    assert_eq!(f.client.history(&f.org, &0, &10).len(), 0);
}

// -----------------------------------------------------------------------------
// Authorisation
// -----------------------------------------------------------------------------
#[test]
#[should_panic(expected = "Unauthorized")]
fn a_stranger_cannot_seal() {
    let env = Env::default();
    let contract_id = env.register(ProofLedger, ());
    let client = ProofLedgerClient::new(&env, &contract_id);
    let org = BytesN::from_array(&env, &[0xCD; 32]);
    let admin = Address::generate(&env);

    // Registration is authorised...
    env.mock_all_auths();
    client.register(&org, &admin);

    // ...and then all mocking is withdrawn, so the seal below carries no
    // signature at all and must be refused by `require_auth`.
    env.set_auths(&[]);
    client.seal(&org, &1, &root(&env, 1), &genesis(&env), &4, &400, &0, &DAY);
}

#[test]
fn rotation_moves_the_right_to_seal_without_touching_history() {
    let f = setup();

    let first = seal_nth(&f, 1, &genesis(&f.env), 9);
    let multisig = Address::generate(&f.env);

    let book = f.client.rotate(&f.org, &multisig);
    assert_eq!(book.admin, multisig);

    // History is untouched by rotation - only the right to extend it moved.
    assert_eq!(book.head, 1);
    assert_eq!(book.root, first.root);
    assert_eq!(f.client.get(&f.org, &1).root, first.root);

    // And the new admin can extend the same chain.
    let second = seal_nth(&f, 2, &first.root, 9);
    assert_eq!(second.seq, 2);
    assert_eq!(f.client.latest(&f.org).entries, 18);
}

#[test]
#[should_panic(expected = "Error(Contract, #2)")]
fn rotating_an_unregistered_namespace_is_refused() {
    let f = setup();
    let stranger = BytesN::from_array(&f.env, &[0x33; 32]);
    f.client.rotate(&stranger, &Address::generate(&f.env));
}

// -----------------------------------------------------------------------------
// Isolation between organisations
// -----------------------------------------------------------------------------
#[test]
fn two_organisations_keep_independent_chains() {
    // One contract instance serves every business, so the namespace is the only
    // thing separating them. A bug here would let one business's seal advance
    // another's head.
    let f = setup();

    let other_org = BytesN::from_array(&f.env, &[0x44; 32]);
    let other_admin = Address::generate(&f.env);
    f.client.register(&other_org, &other_admin);

    let mine = seal_nth(&f, 1, &genesis(&f.env), 10);

    // The other book is still empty, and its first seal chains from genesis -
    // not from mine.
    assert_eq!(f.client.latest(&other_org).head, 0);
    let theirs = f.client.seal(
        &other_org,
        &1,
        &root(&f.env, 50),
        &genesis(&f.env),
        &3,
        &300,
        &0,
        &DAY,
    );

    assert_eq!(f.client.latest(&f.org).head, 1);
    assert_eq!(f.client.latest(&other_org).head, 1);
    assert_ne!(mine.root, theirs.root);
    assert_eq!(f.client.latest(&f.org).entries, 10);
    assert_eq!(f.client.latest(&other_org).entries, 3);
}

#[test]
fn a_book_is_a_struct_a_monitor_can_read_in_one_call() {
    // The CA console's query: how long since this client last sealed, and what
    // date are they sealed up to. Both come from `latest` without touching a
    // single seal entry.
    let f = setup();
    f.env.ledger().with_mut(|l| l.timestamp = 1_800_000_000);

    let seal = seal_nth(&f, 1, &genesis(&f.env), 31);
    let book: Book = f.client.latest(&f.org);

    assert_eq!(book.sealed_at, 1_800_000_000);
    assert_eq!(book.covered_to, seal.to);
    assert_eq!(book.entries, 31);
}
