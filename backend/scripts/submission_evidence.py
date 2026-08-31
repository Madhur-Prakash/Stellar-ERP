#!/usr/bin/env python
"""Generate the submission's evidence from this install's own data.

Three of the Stellar Builder checklist items are not claims a README can make -
they are facts about a running deployment:

* **Proof of 10+ user wallet interactions.** Every organization that switches
  sealing on gets its own funded Stellar account, and every seal it writes is a
  transaction signed by that account. So the proof is a list of ``G...`` addresses
  and transaction hashes, each of which resolves on a public explorer.
* **Basic user feedback summary.** Counts, statuses and the mean rating, from the
  feedback the in-app widget collected.
* **Analytics.** What was actually used, over a window, and by how many
  organizations.

Writing those by hand would make them assertions. Reading them out of the database
and printing the explorer link beside each one makes them checkable by somebody who
has no reason to believe us - which is the whole argument this product makes about
accounting, and it would be odd to make the argument and then not apply it to our
own submission.

    uv run python scripts/submission_evidence.py
    uv run python scripts/submission_evidence.py --out ../docs/evidence.md
    uv run python scripts/submission_evidence.py --days 60 --json

Reads. Never writes to the database, and never prints a signer's secret - the
column is not even selected by the query behind ``adoption``.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

# Importable as a script from the backend root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.db.registry  # noqa: F401  - registers every mapper before the first query
from app.core.config import settings
from app.db.session import session_scope
from app.modules.attestation.service import AttestationService
from app.modules.feedback.service import FeedbackService, UsageService

#: Rendered where a figure is genuinely absent, rather than a misleading zero.
NONE = "-"

#: The marker `scripts/seed_demo.py` stamps on everything it writes.
#:
#: Counted and called out explicitly, because this report reads the same tables the
#: seeder writes to and its whole purpose is to be quotable. A seeded row inflating a
#: number here, in a document written to be pasted into a submission, is the single
#: most damaging thing this script could do quietly.
SEED_MARKER = "demo-seed.example.com"
SEED_ORG_SUFFIX = "(demo)"


def _explorer(kind: str, value: str | None) -> str:
    """A markdown link to the public explorer, or a dash.

    Every on-chain figure in this report carries one. A hash a reader cannot click
    through to is a hash they have to take on trust, which defeats the point of
    printing it.
    """
    if not value:
        return NONE
    base = settings.stellar_explorer_base
    if not base:
        return f"`{value}`"
    return f"[`{value[:12]}…`]({base}/{kind}/{value})"


def _when(value: dt.datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else NONE


async def _seeded_counts(session: Any) -> dict[str, int]:
    """How many rows in this report came from the demo seeder."""
    from sqlalchemy import func, select

    from app.modules.feedback.models import Feedback
    from app.modules.organizations.models import Organization

    seeded_feedback = await session.scalar(
        select(func.count())
        .select_from(Feedback)
        .where(Feedback.contact_email.like(f"%@{SEED_MARKER}"))
    )
    seeded_orgs = await session.scalar(
        select(func.count())
        .select_from(Organization)
        .where(Organization.name.like(f"%{SEED_ORG_SUFFIX}"))
    )
    return {"feedback": int(seeded_feedback or 0), "organizations": int(seeded_orgs or 0)}


async def gather(days: int) -> dict[str, Any]:
    async with session_scope() as session:
        adoption = await AttestationService(session).adoption(limit=500)
        feedback = await FeedbackService(session).summary()
        usage = await UsageService(session).rollup(days=days)
        seeded = await _seeded_counts(session)

    sealing = [row for row in adoption if row["seals"] > 0]
    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "network": settings.stellar_network,
        "contract_id": settings.soroban_contract_id,
        "explorer_base": settings.stellar_explorer_base,
        "organizations": adoption,
        # Deliberately not `len(adoption)`. Switching sealing on is not the same as
        # having sealed, and conflating them is exactly the flattering arithmetic a
        # submission should not contain.
        "organizations_with_a_book": len(adoption),
        "organizations_sealing": len(sealing),
        "wallet_interactions": sum(int(row["seals"]) for row in adoption)
        + sum(1 for row in adoption if row["registration_tx"]),
        "total_seals": sum(int(row["seals"]) for row in adoption),
        "total_entries_sealed": sum(int(row["entries_sealed"]) for row in adoption),
        "feedback": feedback,
        "usage": usage,
        "seeded": seeded,
    }


def render(data: dict[str, Any]) -> str:
    rows: list[dict[str, Any]] = data["organizations"]
    out: list[str] = []
    add = out.append

    add("# Submission evidence")
    add("")
    # The docs index counts this file as one of its own, so it carries the same nav
    # bar as every hand-written page. It lives here rather than in the .md because
    # anything written into the file itself is gone on the next `make evidence`.
    add("<!-- nav:start -->")
    add(
        "[Docs](README.md) · [Spec](spec.md) · [Architecture](architecture.md) · "
        "[Database](database.md) · [Accounting](accounting.md) · "
        "[Proof ledger](attestation.md) · [API](api.md) · [Security](security.md) · "
        "[Audit](security-audit.md) · [Commands](commands.md) · "
        "[Screenshots](screenshots.md) · [Demo video](demo-video.md) · "
        "**Evidence** · [Development](development.md) · [Deployment](deployment.md)"
    )
    add("<!-- nav:end -->")
    add("")
    add(
        f"Generated {data['generated_at']} from this install's own database and the "
        f"**{data['network']}** ledger. Every on-chain figure below links to a public "
        "explorer, so none of it has to be taken on trust."
    )
    add("")
    add(f"Contract: {_explorer('contract', data['contract_id'])}")
    add("")

    seeded = data.get("seeded") or {}
    if seeded.get("feedback") or seeded.get("organizations"):
        add(
            "> **This install contains demo data and the figures below include it.** "
            f"{seeded.get('organizations', 0)} organization(s) and "
            f"{seeded.get('feedback', 0)} feedback row(s) were written by "
            "`scripts/seed_demo.py`, not by real users. Seeded rows are fine for a "
            "screenshot or a demo recording and are **not** evidence: the checklist's "
            "*user feedback summary* and *10+ wallet interactions* both mean real "
            "people. Run `scripts/seed_demo.py --wipe` before quoting any of this."
        )
        add("")
    add("---")
    add("")

    # -- wallet interactions --------------------------------------------------
    add("## Wallet interactions")
    add("")
    add(
        "Each organization that switches sealing on is given **its own Stellar "
        "account**, funded on the network, and registered on the proof-ledger "
        "contract. Every seal it writes afterwards is a transaction signed by that "
        "account. Per-organization signers are also what removes sequence-number "
        "contention: there is no shared account for two writers to collide on."
    )
    add("")
    add("| | |")
    add("| --- | --- |")
    add(f"| Organizations with a book | **{data['organizations_with_a_book']}** |")
    add(f"| Organizations that have actually sealed | **{data['organizations_sealing']}** |")
    add(f"| Signed on-chain interactions | **{data['wallet_interactions']}** |")
    add(f"| Confirmed seals | {data['total_seals']} |")
    add(f"| Journal entries committed | {data['total_entries_sealed']} |")
    add("")
    add(
        "*Signed interactions* counts one `register` per registered book plus every "
        "confirmed `seal`. Both are transactions the organization's own key signed."
    )
    add("")

    if rows:
        add("| Organization | Signer account | Registered | Seals | Entries | Latest seal |")
        add("| --- | --- | --- | --- | --- | --- |")
        for row in rows:
            add(
                f"| {row['organization_name']} "
                f"| {_explorer('account', row['signer_public_key'])} "
                f"| {_explorer('tx', row['registration_tx'])} "
                f"| {row['seals']} "
                f"| {row['entries_sealed']} "
                f"| {_explorer('tx', row['head_tx_hash'])} |"
            )
        add("")
        add(
            "The signer's secret is never selected by the query behind this table, so it "
            "cannot appear here even by accident."
        )
    else:
        add(
            "> No organization has switched sealing on yet. Open **Trust**, enable it, and "
            "post a few entries - this table fills itself in."
        )
    add("")
    add("---")
    add("")

    # -- feedback -------------------------------------------------------------
    feedback = data["feedback"]
    add("## User feedback")
    add("")
    add(
        "Collected by the in-app widget, which works signed out as well - somebody who "
        "cannot get past the sign-in screen is exactly the person whose report is worth "
        "having, and a form behind the sign-in would never hear from them."
    )
    add("")
    add(f"**{feedback['total']}** submissions.")
    add("")
    if feedback["total"]:
        add("| Kind | Count |")
        add("| --- | --- |")
        for kind, count in sorted(feedback["by_kind"].items(), key=lambda kv: -kv[1]):
            add(f"| {kind} | {count} |")
        add("")
        add("| Status | Count |")
        add("| --- | --- |")
        for status, count in sorted(feedback["by_status"].items(), key=lambda kv: -kv[1]):
            add(f"| {status} | {count} |")
        add("")
        if feedback["average_rating"] is not None:
            add(
                f"Mean rating **{feedback['average_rating']}** across "
                f"{feedback['rated_count']} rated submissions."
            )
            if feedback["rated_count"] < 5:
                add("")
                add(
                    "> A mean over that few ratings is noise. The count travels with it "
                    "for exactly that reason - quote both or neither."
                )
    else:
        add("> Nothing submitted yet.")
    add("")
    add("---")
    add("")

    # -- usage ----------------------------------------------------------------
    usage = data["usage"]
    add("## Usage")
    add("")
    add(
        f"First-party analytics, stored in this install's own PostgreSQL and never sent "
        f"anywhere. Last **{usage['days']}** days."
    )
    add("")
    add(f"**{usage['active_organizations']}** organizations, **{usage['active_users']}** users.")
    add("")
    if usage["actions"]:
        add("| Action | Events | Organizations | Users |")
        add("| --- | --- | --- | --- |")
        for action in usage["actions"]:
            add(
                f"| `{action['action']}` | {action['events']} "
                f"| {action['organizations']} | {action['users']} |"
            )
        add("")
        add(
            "The events table has **no free-text payload column**. An open payload is how "
            "an analytics table ends up inside the compliance boundary, so actions are "
            "allow-listed and the context keys are too."
        )
    else:
        add("> No events recorded in this window.")
    add("")

    return "\n".join(out) + "\n"


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="submission_evidence.py",
        description="Generate submission evidence from this install's own data.",
    )
    parser.add_argument("--days", type=int, default=30, help="usage window (default: 30)")
    parser.add_argument("--out", type=Path, help="write markdown here instead of stdout")
    parser.add_argument("--json", action="store_true", help="emit raw JSON instead of markdown")
    args = parser.parse_args(argv)

    data = await gather(args.days)

    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    text = render(data)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)

    # A non-zero exit when the headline claim is not yet true, so this cannot be
    # wired into CI and quietly pass while the submission says "10+".
    if data["wallet_interactions"] < 10:
        print(
            f"\nnote: {data['wallet_interactions']} signed interactions so far; "
            "the checklist asks for 10 or more.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
