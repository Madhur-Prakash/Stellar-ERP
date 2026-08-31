# Submission evidence

<!-- nav:start -->
[Docs](README.md) · [Spec](spec.md) · [Architecture](architecture.md) · [Database](database.md) · [Accounting](accounting.md) · [Proof ledger](attestation.md) · [API](api.md) · [Security](security.md) · [Audit](security-audit.md) · [Commands](commands.md) · [Screenshots](screenshots.md) · [Demo video](demo-video.md) · **Evidence** · [Development](development.md) · [Deployment](deployment.md)
<!-- nav:end -->

Generated 2026-08-31T12:15:50+00:00 from this install's own database and the **testnet** ledger. Every on-chain figure below links to a public explorer, so none of it has to be taken on trust.

Contract: [`CCB66KMNINKN…`](https://stellar.expert/explorer/testnet/contract/CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR)

> **This install contains demo data and the figures below include it.** 12 organization(s) and 12 feedback row(s) were written by `scripts/seed_demo.py`, not by real users. Seeded rows are fine for a screenshot or a demo recording and are **not** evidence: the checklist's *user feedback summary* and *10+ wallet interactions* both mean real people. Subtract the seeded counts above before quoting any figure here.
>
> **`scripts/seed_demo.py --wipe` is not the fix, and will make this worse.** It deletes seeded organizations and users, and a seeded organization that switched sealing on is carrying `register` and `seal` transactions in the wallet-interaction count. Wiping removes the rows this report reads, so the count drops to whatever the real accounts have done - which may be nothing. The transactions themselves stay on chain forever; only the ability to report them goes. Wipe only when deliberately rebuilding from scratch.

---

## Wallet interactions

Each organization that switches sealing on is given **its own Stellar account**, funded on the network, and registered on the proof-ledger contract. Every seal it writes afterwards is a transaction signed by that account. Per-organization signers are also what removes sequence-number contention: there is no shared account for two writers to collide on.

| | |
| --- | --- |
| Organizations with a book | **6** |
| Organizations that have actually sealed | **1** |
| Signed on-chain interactions | **8** |
| Confirmed seals | 2 |
| Journal entries committed | 5 |

*Signed interactions* counts one `register` per registered book plus every confirmed `seal`. Both are transactions the organization's own key signed.

| Organization | Signer account | Registered | Seals | Entries | Latest seal |
| --- | --- | --- | --- | --- | --- |
| Bharat Cold Storage (demo) | [`GC2RXRZNR5JA…`](https://stellar.expert/explorer/testnet/account/GC2RXRZNR5JA43TGNN3E7VXF6QD3HMCB7C4R67JCRVQXIGG4PDAGUWPH) | [`375bfebec260…`](https://stellar.expert/explorer/testnet/tx/375bfebec2606a14e89962d9b572d88dd30de187fdd6101c1dab4c384b4432fd) | 2 | 5 | [`0355f9521c01…`](https://stellar.expert/explorer/testnet/tx/0355f9521c013a99b9af8280428651096e884b5bcda52988dae219c9ae76bd95) |
| Konark Hardware (demo) | [`GCLM6TCQQWUT…`](https://stellar.expert/explorer/testnet/account/GCLM6TCQQWUTY46ZBP2TMBLT7ET7YZUNXIKCENFQIB5EXJELUMMFOXM2) | [`3d5bf6ff0bfc…`](https://stellar.expert/explorer/testnet/tx/3d5bf6ff0bfc4863a117d8d585783181c917442b30e7e266277e75b11a2184f8) | 0 | 0 | - |
| Nirmal Traders (demo) | [`GBNYJZ27OUM3…`](https://stellar.expert/explorer/testnet/account/GBNYJZ27OUM3FNDYCKJFBIJDZBOAF4R7YX2M4NB7JY2AP3VZHO77NC4D) | [`ee1f4eee7725…`](https://stellar.expert/explorer/testnet/tx/ee1f4eee7725555bda96d8123de3c79ce75431c79f842eeff79ab74e5fc69783) | 0 | 0 | - |
| Saraswati Stationers (demo) | [`GA5XXZEJM253…`](https://stellar.expert/explorer/testnet/account/GA5XXZEJM253LKZ7OVCNFA2BBKLEB4XY5LYZJT5PQWKRPUQK5KXOYB7P) | [`100c642eee7a…`](https://stellar.expert/explorer/testnet/tx/100c642eee7ac25068577170d951efc039bcc5f81461354d532eb20f73d85ce1) | 0 | 0 | - |
| Deccan Auto Spares (demo) | [`GBR2TDA6VIZC…`](https://stellar.expert/explorer/testnet/account/GBR2TDA6VIZCR65WGOX5VSHHLV2QT6KQPOY6D3QYVROP274L574STTAF) | [`9e08ebc2ad73…`](https://stellar.expert/explorer/testnet/tx/9e08ebc2ad73068a562c00228966d523e038a07db0c56d1d00ade78a648fc01c) | 0 | 0 | - |
| Kaveri Textiles (demo) | [`GC2Y6QSFMXU6…`](https://stellar.expert/explorer/testnet/account/GC2Y6QSFMXU6E4NAMBUVLMPQKVMSXSVTEODGOEUD5GS24NJY7CSDXLF2) | [`f8fdd7a29ed8…`](https://stellar.expert/explorer/testnet/tx/f8fdd7a29ed838685f3fd2f01dcdbd43d539712e3c6b36889e9d5b4fd1f68042) | 0 | 0 | - |

The signer's secret is never selected by the query behind this table, so it cannot appear here even by accident.

---

## User feedback

Collected by the in-app widget, which works signed out as well - somebody who cannot get past the sign-in screen is exactly the person whose report is worth having, and a form behind the sign-in would never hear from them.

**12** submissions.

| Kind | Count |
| --- | --- |
| praise | 6 |
| idea | 4 |
| question | 1 |
| problem | 1 |

| Status | Count |
| --- | --- |
| new | 12 |

Mean rating **4.2** across 10 rated submissions.

---

## Usage

First-party analytics, stored in this install's own PostgreSQL and never sent anywhere. Last **30** days.

**6** organizations, **6** users.

| Action | Events | Organizations | Users |
| --- | --- | --- | --- |
| `screen.trust` | 18 | 6 | 6 |
| `screen.dashboard` | 14 | 6 | 6 |
| `seal.now` | 12 | 6 | 6 |
| `screen.accounting` | 7 | 1 | 1 |
| `attestation.enabled` | 6 | 6 | 6 |
| `screen.billing` | 6 | 1 | 1 |
| `proof.rejected` | 5 | 1 | 1 |
| `screen.verify` | 5 | 1 | 1 |
| `screen.analytics` | 4 | 2 | 2 |
| `screen.sales` | 4 | 1 | 1 |
| `proof.verified` | 3 | 1 | 1 |
| `screen.accounts` | 2 | 1 | 1 |
| `screen.settings` | 2 | 1 | 1 |
| `screen.documents` | 2 | 1 | 1 |
| `screen.inventory` | 2 | 1 | 1 |

The events table has **no free-text payload column**. An open payload is how an analytics table ends up inside the compliance boundary, so actions are allow-listed and the context keys are too.