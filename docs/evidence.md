# Submission evidence

Generated 2026-08-31T10:08:59+00:00 from this install's own database and the **testnet** ledger. Every on-chain figure below links to a public explorer, so none of it has to be taken on trust.

Contract: [`CCB66KMNINKN…`](https://stellar.expert/explorer/testnet/contract/CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR)

> **This install contains demo data and the figures below include it.** 12 organization(s) and 12 feedback row(s) were written by `scripts/seed_demo.py`, not by real users. Seeded rows are fine for a screenshot or a demo recording and are **not** evidence: the checklist's *user feedback summary* and *10+ wallet interactions* both mean real people. Run `scripts/seed_demo.py --wipe` before quoting any of this.

---

## Wallet interactions

Each organization that switches sealing on is given **its own Stellar account**, funded on the network, and registered on the proof-ledger contract. Every seal it writes afterwards is a transaction signed by that account. Per-organization signers are also what removes sequence-number contention: there is no shared account for two writers to collide on.

| | |
| --- | --- |
| Organizations with a book | **6** |
| Organizations that have actually sealed | **0** |
| Signed on-chain interactions | **6** |
| Confirmed seals | 0 |
| Journal entries committed | 0 |

*Signed interactions* counts one `register` per registered book plus every confirmed `seal`. Both are transactions the organization's own key signed.

| Organization | Signer account | Registered | Seals | Entries | Latest seal |
| --- | --- | --- | --- | --- | --- |
| Sunrise Dairy Supply (demo) | [`GDGESNIAEKMI…`](https://stellar.expert/explorer/testnet/account/GDGESNIAEKMI6QT5U7U7TAK6CCVJUH5HZKOCU5CZOGCSHIGARARWIVL5) | [`98c6cd1d19d8…`](https://stellar.expert/explorer/testnet/tx/98c6cd1d19d8cd9edaa493c1e03fbf60f55b83cb1985bfe259c7298cc3723d3f) | 0 | 0 | — |
| Saraswati Stationers (demo) | [`GDC6CXNIKXXZ…`](https://stellar.expert/explorer/testnet/account/GDC6CXNIKXXZABUEN2624UH3U7GJCHUXEWYIPQLDXUZC3YKFL2YHB4WK) | [`de48baa51721…`](https://stellar.expert/explorer/testnet/tx/de48baa517216d1c299bc1c32c297b8f3f189e4e731eeec5c7719a95521cbad4) | 0 | 0 | — |
| Gurgaon Print Works (demo) | [`GCYWHTRB5THL…`](https://stellar.expert/explorer/testnet/account/GCYWHTRB5THL67LYTOHKQ5DGETAOEXC44YQNCPGN33XHUWAD7KQL6W5B) | [`a449d9f00c6a…`](https://stellar.expert/explorer/testnet/tx/a449d9f00c6ad53c0f3d45284449aa76340ca040ce4c6964099f53e5032458b6) | 0 | 0 | — |
| Vidarbha Agro Tools (demo) | [`GAHACP7GPNDT…`](https://stellar.expert/explorer/testnet/account/GAHACP7GPNDTH2XKNEVBREGJXEFRQD2Y2N6HP3HVFAYFNY5FDEPM447Z) | [`b6d9dcf1ce37…`](https://stellar.expert/explorer/testnet/tx/b6d9dcf1ce37b856ff4342cdea2c6419e61a0ad5d4d9d6b93e3cc126effa8197) | 0 | 0 | — |
| Konark Hardware (demo) | [`GAHFFJ5QOLZI…`](https://stellar.expert/explorer/testnet/account/GAHFFJ5QOLZIB5526R7A56DGBGWYJKAMCOYK4EP7GF5DPKVRBS4W5ZHF) | [`7bb33ea2bd61…`](https://stellar.expert/explorer/testnet/tx/7bb33ea2bd61dc16680023d6fc9dd00c4ccd354ffb3e53aec6dba3ed818f052c) | 0 | 0 | — |
| Kaveri Textiles (demo) | [`GDCC4VNV7JKL…`](https://stellar.expert/explorer/testnet/account/GDCC4VNV7JKLGNKA2KWBAPFB4M2IA5BKTWH4RBWI4HHSBWUTSDLAKZWI) | [`d4c7262f68aa…`](https://stellar.expert/explorer/testnet/tx/d4c7262f68aa6e76980fef0b1413fe0cc65a19e7ca70a6f2e1abcca2e026b553) | 0 | 0 | — |

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
| `seal.now` | 10 | 6 | 6 |
| `screen.trust` | 8 | 6 | 6 |
| `attestation.enabled` | 6 | 6 | 6 |
| `screen.dashboard` | 6 | 5 | 5 |
| `screen.analytics` | 1 | 1 | 1 |

The events table has **no free-text payload column**. An open payload is how an analytics table ends up inside the compliance boundary, so actions are allow-listed and the context keys are too.