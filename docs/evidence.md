# Submission evidence

Generated 2026-08-31T08:58:27+00:00 from this install's own database and the **testnet** ledger. Every on-chain figure below links to a public explorer, so none of it has to be taken on trust.

Contract: [`CCB66KMNINKN…`](https://stellar.expert/explorer/testnet/contract/CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR)

> **This install contains demo data and the figures below include it.** 12 organization(s) and 0 feedback row(s) were written by `scripts/seed_demo.py`, not by real users. Seeded rows are fine for a screenshot or a demo recording and are **not** evidence: the checklist's *user feedback summary* and *10+ wallet interactions* both mean real people. Run `scripts/seed_demo.py --wipe` before quoting any of this.

---

## Wallet interactions

Each organization that switches sealing on is given **its own Stellar account**, funded on the network, and registered on the proof-ledger contract. Every seal it writes afterwards is a transaction signed by that account. Per-organization signers are also what removes sequence-number contention: there is no shared account for two writers to collide on.

| | |
| --- | --- |
| Organizations with a book | **1** |
| Organizations that have actually sealed | **1** |
| Signed on-chain interactions | **2** |
| Confirmed seals | 1 |
| Journal entries committed | 1 |

*Signed interactions* counts one `register` per registered book plus every confirmed `seal`. Both are transactions the organization's own key signed.

| Organization | Signer account | Registered | Seals | Entries | Latest seal |
| --- | --- | --- | --- | --- | --- |
| test | [`GDLSYRIBLHSN…`](https://stellar.expert/explorer/testnet/account/GDLSYRIBLHSNYVT26YIF56GDIRDVMJZP4RFTOENSM3JOIFJSGK3QJ5FO) | [`0965c24f8c01…`](https://stellar.expert/explorer/testnet/tx/0965c24f8c01c5ad78fca66c52d40216575dd374f9fdb7bf68e1459a4d38cc2f) | 1 | 1 | [`13377b65376c…`](https://stellar.expert/explorer/testnet/tx/13377b65376cec326d64933b1cb9a139a633f172273bbd1187aeb89a0674cdd6) |

The signer's secret is never selected by the query behind this table, so it cannot appear here even by accident.

---

## User feedback

Collected by the in-app widget, which works signed out as well - somebody who cannot get past the sign-in screen is exactly the person whose report is worth having, and a form behind the sign-in would never hear from them.

**0** submissions.

> Nothing submitted yet.

---

## Usage

First-party analytics, stored in this install's own PostgreSQL and never sent anywhere. Last **30** days.

**1** organizations, **1** users.

| Action | Events | Organizations | Users |
| --- | --- | --- | --- |
| `screen.trust` | 20 | 1 | 1 |
| `screen.dashboard` | 3 | 1 | 1 |
| `seal.now` | 3 | 1 | 1 |
| `attestation.enabled` | 1 | 1 | 1 |
| `screen.billing` | 1 | 1 | 1 |

The events table has **no free-text payload column**. An open payload is how an analytics table ends up inside the compliance boundary, so actions are allow-listed and the context keys are too.

