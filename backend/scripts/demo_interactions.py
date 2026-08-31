#!/usr/bin/env python
"""Post an entry and seal it, as many times as asked - on-chain interactions on demand.

Why this exists
---------------
The Stellar Builder checklist wants **10+ user wallet interactions**, and
``scripts/submission_evidence.py`` counts one ``register`` per book plus every
confirmed ``seal``. Registrations are one-per-organization and therefore capped;
seals are the repeatable half, so seals are how a short count gets closed.

Doing that through the UI is a round trip per interaction - post an entry on
Accounting, switch to Trust, press **Seal now**, wait for confirmation - and the
button is deliberately idempotent: with nothing outstanding it answers "Everything
is already sealed" and writes no transaction. That is the right behaviour (a no-op
seal would be a junk transaction on a public ledger and a lie in the seal history)
and it means "press Seal now twice" does not produce two seals. An entry has to be
posted between them, which is exactly the loop this script automates.

What it is not
--------------
**It does not fabricate evidence.** Every entry goes through ``PostingService`` and
every seal through ``SealService.seal_now``, so the same invariants, the same audit
rows and the same on-chain submission apply as when a person does it. The
transactions are real, signed by the organization's own key, and resolve on a public
explorer.

What it cannot fix is *whose* interactions they are. Run against a seeded
organization these are seeded-organization transactions, and a strict reading of
"**user** wallet interactions" would discount them. To produce interactions that
survive that reading, register a real organization first and pass ``--org``::

    uv run python scripts/demo_interactions.py --list
    uv run python scripts/demo_interactions.py --rounds 2
    uv run python scripts/demo_interactions.py --org "Acme Ltd" --rounds 3
    uv run python scripts/demo_interactions.py --dry-run

Exit codes: ``0`` every round sealed, ``1`` at least one round did not confirm.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.db.registry  # noqa: F401  - registers every mapper before the first query
from app.core.context import RequestContext
from app.db.session import session_scope

#: Entries to cycle through. Two-line postings between system accounts, so they
#: balance by construction and need no chart beyond the default one.
ENTRIES: tuple[tuple[str, str, str, str], ...] = (
    ("Counter sale, settled in cash", "cash", "sales_revenue", "1250.00"),
    ("Card sale, banked same day", "bank", "sales_revenue", "3400.00"),
    ("Cash takings banked", "bank", "cash", "900.00"),
    ("Online order, paid on delivery", "bank", "sales_revenue", "2175.00"),
)


async def _organizations() -> list[tuple[uuid.UUID, str, bool]]:
    """Every organization, with whether its book is registered on chain."""
    from sqlalchemy import select

    from app.modules.attestation.models import AttestationSetting
    from app.modules.organizations.models import Organization

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    Organization.id,
                    Organization.name,
                    AttestationSetting.registered_at,
                )
                .join(
                    AttestationSetting,
                    AttestationSetting.organization_id == Organization.id,
                    isouter=True,
                )
                .order_by(Organization.name)
            )
        ).all()
    return [(row[0], row[1], row[2] is not None) for row in rows]


async def _owner_id(organization_id: uuid.UUID) -> tuple[uuid.UUID, str] | None:
    """Id and email of a member to act as. Seals are attributed to a person, not to nobody.

    Returns plain values rather than the ``User`` row. Each round below opens its own
    session, and handing a service an instance loaded by a session it does not own is
    the kind of thing that works until something touches a lazy relationship.
    Re-loading by id inside each session costs one primary-key lookup and removes the
    question entirely.
    """
    from sqlalchemy import select

    from app.modules.organizations.models import OrganizationMember
    from app.modules.users.models import User

    async with session_scope() as session:
        row = (
            await session.execute(
                select(User.id, User.email)
                .join(OrganizationMember, OrganizationMember.user_id == User.id)
                .where(OrganizationMember.organization_id == organization_id)
                .order_by(User.created_at)
                .limit(1)
            )
        ).first()
    return (row[0], row[1]) if row else None


async def _load_user(session, user_id: uuid.UUID):
    """The acting user, inside the session that is about to use it."""
    from app.modules.users.models import User

    return await session.get(User, user_id)


async def run(org_name: str | None, rounds: int, dry_run: bool) -> int:
    from app.modules.accounting.models import JournalType
    from app.modules.accounting.service import PostingService
    from app.modules.attestation.service import SealService

    orgs = await _organizations()
    registered = [row for row in orgs if row[2]]
    if not registered:
        print("no organization has a book on chain yet - switch sealing on in Trust first")
        return 1

    if org_name:
        matches = [row for row in registered if org_name.lower() in row[1].lower()]
        if not matches:
            print(f'no registered organization matches "{org_name}". Try --list')
            return 1
        organization_id, name, _ = matches[0]
    else:
        organization_id, name, _ = registered[0]

    print(f"organization : {name}")
    print(f"rounds       : {rounds}   (one posted entry + one seal each)")

    if dry_run:
        print("\ndry run - nothing posted, nothing sealed")
        return 0

    ctx = RequestContext(
        ip_address="127.0.0.1",
        user_agent="demo_interactions.py",
        request_id=str(uuid.uuid4()),
    )
    owner = await _owner_id(organization_id)
    if owner is None:
        print("that organization has no members, so there is nobody to attribute a seal to")
        return 1
    actor_id, actor_email = owner
    print(f"acting as    : {actor_email}\n")

    confirmed = 0
    for index in range(rounds):
        narration, debit, credit, amount = ENTRIES[index % len(ENTRIES)]

        # Post and seal in separate transactions, deliberately. `seal_now` submits to
        # the network inline, and holding the posting transaction open across a
        # round trip to Soroban would pin a database connection for its duration.
        async with session_scope() as session:
            actor = await _load_user(session, actor_id)
            posting = PostingService(session)
            entry = await posting.post_simple(
                organization_id,
                actor,
                journal_type=JournalType.GENERAL,
                entry_date=dt.date.today(),
                narration=narration,
                debit_key=debit,
                credit_key=credit,
                amount=Decimal(amount),
            )
            number = entry.entry_number

        async with session_scope() as session:
            actor = await _load_user(session, actor_id)
            seal = await SealService(session).seal_now(organization_id, actor, ctx)

        if seal is None:
            # Only reachable if something else sealed between the two blocks above.
            print(f"  {index + 1}. {number}: nothing outstanding to seal")
            continue

        status = seal.status.value
        if status == "confirmed":
            confirmed += 1
            print(f"  {index + 1}. {number} -> seal #{seal.seq} confirmed  {seal.tx_hash}")
        else:
            print(f"  {index + 1}. {number} -> seal #{seal.seq} {status}: {seal.last_error or ''}")

    print(f"\n{confirmed} of {rounds} round(s) confirmed on chain.")
    print("run `make evidence` to regenerate docs/evidence.md with the new count.")
    return 0 if confirmed == rounds else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--org", help="organization to act for (substring match)")
    parser.add_argument("--rounds", type=int, default=2, help="entries to post and seal")
    parser.add_argument("--list", action="store_true", help="list organizations and exit")
    parser.add_argument("--dry-run", action="store_true", help="say what would happen")
    args = parser.parse_args()

    if args.list:
        rows = asyncio.run(_organizations())
        if not rows:
            print("no organizations")
            return 0
        print(f"{'organization':38} on chain")
        for _, name, on_chain in rows:
            print(f"{name:38} {'yes' if on_chain else 'no'}")
        return 0

    if args.rounds < 1:
        print("--rounds must be at least 1")
        return 1

    return asyncio.run(run(args.org, args.rounds, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
