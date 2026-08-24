#![no_std]
//! # Proof Ledger — Ledger 3
//!
//! The third ledger of Stellar ERP. Ledger 1 is the double-entry journal and
//! Ledger 2 is the audit trail; both live in the business's own PostgreSQL and
//! are therefore trusted only by the business. This contract holds cryptographic
//! *commitments* to those two, so that a stranger — a bank, a buyer, an auditor —
//! can establish that the books they are shown today are the books that existed
//! when they were sealed.
//!
//! ## What is stored here, and what is not
//!
//! Stored: a 32-byte Merkle root per accounting period, the number of entries it
//! covers, a control total in minor units, the period's start and end dates, and
//! the ledger timestamp at which the seal was accepted.
//!
//! **Not** stored, ever: any amount attached to a party, a customer name, a
//! GSTIN, a product, a salary, an account number, or a document. Nothing personal
//! is written, so there is nothing here that a data-erasure request could ever
//! need to reach. The organisation is identified only by `org`, a salted hash of
//! its internal id, so the record is unlinkable to a named business until the
//! business itself discloses the namespace.
//!
//! ## Why this is a contract and not a memo
//!
//! A chain that accepts any hash handed to it is a log. This is a referee. The
//! contract re-enforces, at the boundary, the same rules the journal enforces
//! internally:
//!
//! * **Append-only.** A written seal is never updated or deleted. There is no
//!   `update`, no `delete`, and — deliberately — no administrative override. An
//!   admin who could rewrite a seal would reintroduce the exact problem this
//!   ledger exists to remove.
//! * **Strict sequencing.** A seal's `seq` must be exactly `head + 1`. A business
//!   cannot quietly skip a period, because the gap would leave `head` behind and
//!   every subsequent seal would be rejected.
//! * **Chain continuity.** A seal's `prev` must equal the stored root of the
//!   previous seal. Rewriting history therefore means re-sealing every period
//!   after the one that was altered — and each of those re-seals carries the
//!   network's own timestamp, so the back-dating is not merely detectable, it is
//!   permanent and loud.
//! * **The network timestamps it, not the caller.** [`ProofLedger::seal`] takes
//!   no `at` argument. It is read from [`soroban_sdk::Ledger::timestamp`]. This is
//!   the single most important line in the contract: a caller-supplied timestamp
//!   would make every claim this ledger makes worthless.
//!
//! ## What a seal proves
//!
//! That the books presented today are byte-identical to the books that existed
//! when the seal was written, and that the seal was written at a time the network
//! attests to and the business cannot back-date.
//!
//! It does **not** prove the entries were true when they were made. No
//! cryptographic scheme can. What it eliminates is *retroactive* fabrication,
//! which is how accounts are actually cooked in practice — by editing history to
//! fit a story told later.
//!
//! ## Storage durability
//!
//! Everything is written to `persistent` storage and its TTL extended on every
//! touch. `temporary` would be wrong to the point of being dangerous: an expired
//! temporary entry is gone, and a missing seal in an append-only chain is
//! indistinguishable from evidence of tampering. A persistent entry that outlives
//! its TTL is *archived*, not deleted — restoring it is a fee, not a loss — and
//! the proof bundle a business hands a verifier carries the root anyway, so a
//! restore is only ever needed to re-read what the verifier already holds.

use soroban_sdk::{
    contract, contracterror, contractevent, contractimpl, contracttype, panic_with_error, Address,
    BytesN, Env, Vec,
};

// -----------------------------------------------------------------------------
// TTL policy
// -----------------------------------------------------------------------------
/// Ledgers closed in a day at the network's ~5 second cadence.
const DAY_IN_LEDGERS: u32 = 17_280;

/// Extend when fewer than 30 days of life remain.
///
/// Generous on purpose. A business that seals monthly touches its book twelve
/// times a year, and a threshold tighter than the gap between two touches would
/// let entries drift into archival between ordinary uses.
const TTL_THRESHOLD: u32 = DAY_IN_LEDGERS * 30;

/// Extend to 120 days. Comfortably inside every current network's
/// `max_entry_ttl`, so the call cannot fail for asking too much — which it would,
/// and the seal would fail with it.
const TTL_EXTEND_TO: u32 = DAY_IN_LEDGERS * 120;

/// The `prev` value of the very first seal in a book, and the `root` of a book
/// with no seals yet. Not a magic number so much as a documented sentinel: it
/// makes "is this the genesis seal?" a comparison rather than a special case.
const GENESIS: [u8; 32] = [0u8; 32];

// -----------------------------------------------------------------------------
// Errors
// -----------------------------------------------------------------------------
/// Every way a call can be refused.
///
/// Named rather than numbered at the call site so a failing submission in the
/// backend's log says *why*. `SequenceOutOfOrder` and `ChainBroken` in particular
/// are the two a caller will actually hit: the first is a duplicate submission
/// after an ambiguous timeout, which is the common and benign case; the second
/// means the local database and the chain disagree about history, which is not.
#[contracterror]
#[derive(Copy, Clone, Debug, Eq, PartialEq, PartialOrd, Ord)]
#[repr(u32)]
pub enum Error {
    /// `register` called for a namespace that already has a book.
    AlreadyRegistered = 1,
    /// Any call for a namespace that was never registered.
    NotRegistered = 2,
    /// `seq` was not exactly `head + 1`. A retry of an already-accepted seal
    /// lands here, which is what makes submission idempotent by consensus
    /// rather than by the caller's retry logic.
    SequenceOutOfOrder = 3,
    /// `prev` did not match the stored root of the previous seal.
    ChainBroken = 4,
    /// A seal covering no entries. Sealing nothing is not an attestation, and
    /// allowing it would let a business pad its chain with empty periods to bury
    /// a gap.
    EmptySeal = 5,
    /// `to` was earlier than `from`, or the period starts before the previous
    /// period ended.
    PeriodOutOfOrder = 6,
    /// No seal exists at the requested sequence number.
    SealNotFound = 7,
    /// A root of all zeroes. That is the genesis sentinel, so accepting it as a
    /// real root would make the first seal's `prev` check ambiguous.
    RootIsSentinel = 8,
}

// -----------------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------------
/// One sealed accounting period.
///
/// Eight fields, and each is here because a verifier needs it:
///
/// * `root`, `prev`, `seq` — the chain itself.
/// * `count` and `debits` — control totals. They let a verifier sanity-check the
///   statements they were handed *without* walking every Merkle path: if the
///   business claims 412 entries totalling ₹1.2 crore for March and the seal says
///   409 and ₹1.19 crore, the conversation is over before anyone hashes anything.
/// * `from` and `to` — which period this is, so a verifier can tell that the
///   twelve seals they were shown actually tile the year with no month missing.
/// * `at` — when the network accepted it. Set here, never by the caller.
#[contracttype]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Seal {
    /// Position in this book's chain. Strictly `previous + 1`, starting at 1.
    pub seq: u32,
    /// Merkle root over the canonical hashes of the period's journal entries.
    pub root: BytesN<32>,
    /// The previous seal's `root`; [`GENESIS`] for the first seal in a book.
    pub prev: BytesN<32>,
    /// How many journal entries this root covers.
    pub count: u32,
    /// Sum of the period's debits in minor units — paise, cents. An integer, so
    /// the control total that reaches the chain cannot drift the way a decimal
    /// rendered through a float would.
    pub debits: i128,
    /// Period start, as a Unix timestamp at midnight UTC of the accounting date.
    pub from: u64,
    /// Period end, same encoding.
    pub to: u64,
    /// Ledger timestamp at which this seal was accepted. **Set by the network.**
    pub at: u64,
}

/// The head of one organisation's chain.
///
/// Kept as its own entry rather than derived by scanning seals, because `latest`
/// is the hot read: the backend's reconciler calls it on every startup to find
/// out what the chain believes, and a verifier calls it to check that the seal
/// they were handed has not been superseded. One key lookup, no iteration.
#[contracttype]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Book {
    /// The account authorised to seal and to rotate. In the intended deployment
    /// this is a 2-of-3 multisig account — the business, its chartered
    /// accountant, and a neutral signer — which is what raises the claim from
    /// "unchanged since T" to "unchanged since T, and a professional carrying
    /// statutory liability co-signed it".
    pub admin: Address,
    /// Sequence number of the newest seal; `0` when the book is empty.
    pub head: u32,
    /// `root` of the newest seal; [`GENESIS`] when the book is empty.
    pub root: BytesN<32>,
    /// `at` of the newest seal; `0` when the book is empty. Lets a monitor answer
    /// "how long since this business last sealed?" in one call.
    pub sealed_at: u64,
    /// `to` of the newest seal — the accounting date the books are sealed up to,
    /// which is the figure a lender actually wants. `sealed_at` says when the
    /// seal was written; this says what it covers.
    pub covered_to: u64,
    /// Cumulative entries across every seal in this book. `u64`, not `u32`: the
    /// per-seal count cannot exceed a period's entries, but the lifetime total of
    /// a busy business over decades is not something to leave to a 32-bit sum.
    pub entries: u64,
}

/// Storage layout.
///
/// One book per namespace, one entry per seal. Seals are keyed by
/// `(namespace, seq)` rather than held in a growing `Vec` inside the book: a
/// vector would be re-serialised in full on every append, so the cost of sealing
/// would climb with the length of the chain, and a business in its tenth year
/// would pay more to seal than one in its first.
#[contracttype]
#[derive(Clone)]
pub enum DataKey {
    /// `BytesN<32>` namespace -> [`Book`].
    Book(BytesN<32>),
    /// `(namespace, seq)` -> [`Seal`].
    Seal(BytesN<32>, u32),
}

// -----------------------------------------------------------------------------
// Events
// -----------------------------------------------------------------------------
// Emitted for every state change, with `org` as an indexed topic.
//
// These are not decoration. The backend's reconciler and the chartered
// accountant's multi-client console both want "what happened to this book, and
// when" without paging through storage reads, and an indexed topic is what makes
// that a subscription rather than a poll. A monitor watching `Sealed` for a set
// of namespaces learns that a client stopped sealing without ever calling the
// contract.

/// A seal was accepted.
#[contractevent]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Sealed {
    #[topic]
    pub org: BytesN<32>,
    pub seq: u32,
    pub root: BytesN<32>,
    pub count: u32,
    /// The network's timestamp, echoed into the event so an indexer never has to
    /// infer it from the ledger the event was found in.
    pub at: u64,
}

/// A book was opened.
#[contractevent]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Opened {
    #[topic]
    pub org: BytesN<32>,
    pub admin: Address,
}

/// A book changed hands.
#[contractevent]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Rotated {
    #[topic]
    pub org: BytesN<32>,
    pub from: Address,
    pub to: Address,
}

// -----------------------------------------------------------------------------
// Contract
// -----------------------------------------------------------------------------
#[contract]
pub struct ProofLedger;

#[contractimpl]
impl ProofLedger {
    /// Open a book for `org`, administered by `admin`.
    ///
    /// `org` is expected to be `SHA-256(organization_id ‖ install_salt)`, but the
    /// contract does not and cannot check that — it is 32 opaque bytes here. What
    /// matters on chain is only that it is stable and that whoever chose it can
    /// authorise against it.
    ///
    /// Refuses a second registration for the same namespace. Re-registering would
    /// be a way to abandon an inconvenient chain and start a fresh one under the
    /// same identity, which is precisely the move the sequencing rules exist to
    /// prevent.
    pub fn register(env: Env, org: BytesN<32>, admin: Address) -> Book {
        admin.require_auth();

        let key = DataKey::Book(org.clone());
        if env.storage().persistent().has(&key) {
            panic_with_error!(&env, Error::AlreadyRegistered);
        }

        let book = Book {
            admin: admin.clone(),
            head: 0,
            root: BytesN::from_array(&env, &GENESIS),
            sealed_at: 0,
            covered_to: 0,
            entries: 0,
        };

        env.storage().persistent().set(&key, &book);
        env.storage()
            .persistent()
            .extend_ttl(&key, TTL_THRESHOLD, TTL_EXTEND_TO);

        Opened {
            org: org.clone(),
            admin: admin.clone(),
        }
        .publish(&env);

        book
    }

    /// Append a seal to `org`'s chain.
    ///
    /// The only mutating call that matters, and every argument it refuses is a
    /// rule the journal already enforces:
    ///
    /// * `seq` must be exactly `head + 1`. **This is what makes submission
    ///   idempotent.** The backend writes its seal intent to a transactional
    ///   outbox and a worker submits it; when that submission times out, the
    ///   worker does not know whether it landed. Resubmitting is safe precisely
    ///   because a seal that already landed makes the retry's `seq` stale, and
    ///   consensus — not our retry logic — rejects it.
    /// * `prev` must equal the stored root. A local database that has diverged
    ///   from the chain is stopped here rather than silently forking.
    /// * `count` must be non-zero, `to` must not precede `from`, and `from` must
    ///   not precede the previous seal's `to`.
    ///
    /// There is no `at` parameter. The timestamp is the network's.
    #[allow(clippy::too_many_arguments)]
    pub fn seal(
        env: Env,
        org: BytesN<32>,
        seq: u32,
        root: BytesN<32>,
        prev: BytesN<32>,
        count: u32,
        debits: i128,
        from: u64,
        to: u64,
    ) -> Seal {
        let book_key = DataKey::Book(org.clone());
        let mut book: Book = match env.storage().persistent().get(&book_key) {
            Some(book) => book,
            None => panic_with_error!(&env, Error::NotRegistered),
        };

        // Authorisation before validation, so a caller who is not the admin
        // learns nothing about the state of a book they cannot write to.
        book.admin.require_auth();

        let sentinel = BytesN::from_array(&env, &GENESIS);
        if root == sentinel {
            panic_with_error!(&env, Error::RootIsSentinel);
        }
        if count == 0 {
            panic_with_error!(&env, Error::EmptySeal);
        }
        if to < from {
            panic_with_error!(&env, Error::PeriodOutOfOrder);
        }
        // `!=` rather than `<=`: a chain must not have a hole and must not
        // re-seal a sequence, and both of those are the same check.
        if seq != book.head + 1 {
            panic_with_error!(&env, Error::SequenceOutOfOrder);
        }
        if prev != book.root {
            panic_with_error!(&env, Error::ChainBroken);
        }
        // Periods tile forwards. A reversal dated into a later period is a new
        // leaf in that later period, never an edit to a sealed one, so the
        // accounting model never needs to seal backwards — and forbidding it
        // removes the only shape in which a business could interleave a
        // fabricated period between two real ones.
        if book.head > 0 && from < book.covered_to {
            panic_with_error!(&env, Error::PeriodOutOfOrder);
        }

        let entry = Seal {
            seq,
            root: root.clone(),
            prev,
            count,
            debits,
            from,
            to,
            // The line the whole design rests on.
            at: env.ledger().timestamp(),
        };

        let seal_key = DataKey::Seal(org.clone(), seq);
        env.storage().persistent().set(&seal_key, &entry);
        env.storage()
            .persistent()
            .extend_ttl(&seal_key, TTL_THRESHOLD, TTL_EXTEND_TO);

        book.head = seq;
        book.root = root.clone();
        book.sealed_at = entry.at;
        book.covered_to = to;
        book.entries += count as u64;

        env.storage().persistent().set(&book_key, &book);
        env.storage()
            .persistent()
            .extend_ttl(&book_key, TTL_THRESHOLD, TTL_EXTEND_TO);

        Sealed {
            org: org.clone(),
            seq,
            root,
            count,
            at: entry.at,
        }
        .publish(&env);

        entry
    }

    /// Read one seal.
    pub fn get(env: Env, org: BytesN<32>, seq: u32) -> Seal {
        let key = DataKey::Seal(org, seq);
        match env.storage().persistent().get::<DataKey, Seal>(&key) {
            Some(seal) => {
                env.storage()
                    .persistent()
                    .extend_ttl(&key, TTL_THRESHOLD, TTL_EXTEND_TO);
                seal
            }
            None => panic_with_error!(&env, Error::SealNotFound),
        }
    }

    /// The head of `org`'s chain.
    ///
    /// The reconciler's first call on startup: the chain, not the local database,
    /// is the authority on what has been sealed.
    pub fn latest(env: Env, org: BytesN<32>) -> Book {
        let key = DataKey::Book(org);
        match env.storage().persistent().get::<DataKey, Book>(&key) {
            Some(book) => {
                env.storage()
                    .persistent()
                    .extend_ttl(&key, TTL_THRESHOLD, TTL_EXTEND_TO);
                book
            }
            None => panic_with_error!(&env, Error::NotRegistered),
        }
    }

    /// Whether a book exists for `org`.
    ///
    /// A plain boolean rather than making the caller catch a panic, because the
    /// backend asks this on every startup and a trapped error is a poor way to
    /// express "not set up yet".
    pub fn is_registered(env: Env, org: BytesN<32>) -> bool {
        env.storage().persistent().has(&DataKey::Book(org))
    }

    /// Check a claimed root against what is stored, in one call.
    ///
    /// The verifier's convenience: it has already recomputed a root from the
    /// proof bundle it was handed, and this answers "is that what the chain says
    /// for that sequence?" without shipping the whole [`Seal`] back and comparing
    /// field by field in the browser. Returns `false` for a missing seal rather
    /// than panicking, so an out-of-range sequence reads as "not verified"
    /// instead of as a transport error.
    pub fn verify(env: Env, org: BytesN<32>, seq: u32, root: BytesN<32>) -> bool {
        match env
            .storage()
            .persistent()
            .get::<DataKey, Seal>(&DataKey::Seal(org, seq))
        {
            Some(seal) => seal.root == root,
            None => false,
        }
    }

    /// Read a window of the chain, newest-first, for a continuity display.
    ///
    /// Bounded to [`MAX_PAGE`] entries per call. A verifier checking that a
    /// year's seals form an unbroken chain needs twelve of them, and letting a
    /// caller ask for ten thousand in one invocation is how a read runs out of
    /// instruction budget and fails in a way that looks like the contract is
    /// broken.
    pub fn history(env: Env, org: BytesN<32>, before_seq: u32, limit: u32) -> Vec<Seal> {
        const MAX_PAGE: u32 = 24;

        let book: Book = match env
            .storage()
            .persistent()
            .get::<DataKey, Book>(&DataKey::Book(org.clone()))
        {
            Some(book) => book,
            None => panic_with_error!(&env, Error::NotRegistered),
        };

        // `before_seq == 0` means "from the head", so a caller does not have to
        // read the book first just to start paging.
        let start = if before_seq == 0 || before_seq > book.head {
            book.head
        } else {
            before_seq
        };
        let take = if limit == 0 || limit > MAX_PAGE {
            MAX_PAGE
        } else {
            limit
        };

        let mut out: Vec<Seal> = Vec::new(&env);
        let mut seq = start;
        while seq > 0 && out.len() < take {
            if let Some(seal) = env
                .storage()
                .persistent()
                .get::<DataKey, Seal>(&DataKey::Seal(org.clone(), seq))
            {
                out.push_back(seal);
            }
            seq -= 1;
        }
        out
    }

    /// Hand this book to a different signer.
    ///
    /// The upgrade path from a single key to 2-of-3 co-signing: the business
    /// creates a multisig account, then rotates the book onto it. Both the
    /// outgoing and incoming admin must authorise — the outgoing so a stolen key
    /// alone cannot hand the book away silently, the incoming so a book cannot be
    /// parked on an account that never agreed to hold it and can therefore never
    /// seal again.
    ///
    /// Rotation touches no seal. History does not change hands; only the right to
    /// extend it does.
    pub fn rotate(env: Env, org: BytesN<32>, new_admin: Address) -> Book {
        let key = DataKey::Book(org.clone());
        let mut book: Book = match env.storage().persistent().get(&key) {
            Some(book) => book,
            None => panic_with_error!(&env, Error::NotRegistered),
        };

        book.admin.require_auth();
        new_admin.require_auth();

        let previous = book.admin.clone();
        book.admin = new_admin.clone();

        env.storage().persistent().set(&key, &book);
        env.storage()
            .persistent()
            .extend_ttl(&key, TTL_THRESHOLD, TTL_EXTEND_TO);

        Rotated {
            org,
            from: previous,
            to: new_admin,
        }
        .publish(&env);

        book
    }
}

#[cfg(test)]
mod test;
