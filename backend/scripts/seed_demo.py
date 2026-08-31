#!/usr/bin/env python
"""Seed a development install with demo organizations, entries and feedback.

    uv run python scripts/seed_demo.py
    uv run python scripts/seed_demo.py --organizations 20 --entries 5
    uv run python scripts/seed_demo.py --wipe          # remove what a previous run made
    uv run python scripts/seed_demo.py --dry-run

Twelve organizations by default, each with its own owner account, a working chart of
accounts, a few posted journal entries, and a spread of feedback.

**This is demo data and it is labelled as such, deliberately and in three places** -
the email domain, the organization name suffix, and the feedback's contact address all
carry the marker below. That is not decoration:

> The Stellar Builder checklist asks for a *user feedback summary* and *proof of 10+
> user wallet interactions*. Both mean real people. Seeded rows are the right way to
> get a populated screen for a screenshot or a demo recording, and the wrong thing to
> submit as evidence - `make evidence` reads the same tables. So the marker exists to
> make seeded rows impossible to mistake for real ones later, including by us.

Everything goes through the real services - `AuthService.register`,
`OrganizationService.create`, `PostingService.post_simple`, `FeedbackService.submit` -
rather than inserting rows directly, so every invariant the application enforces
applies to the seeded data too. A seeder that wrote its own INSERTs would happily
produce an unbalanced journal entry, and the first thing anyone would do with a
populated install is look at the trial balance.
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

#: Stamped into every seeded email, organization name and feedback contact.
#:
#: A subdomain of `example.com`, which RFC 2606 reserves and which therefore can never
#: accept mail - and that matters, because registration sends a verification email.
#:
#: `.invalid` would be the more obvious reserved choice and does not work: the email
#: validator rejects a special-use TLD outright ("the part after the @-sign is a
#: special-use or reserved name"), so registration fails before a single row is written.
#: `.test` is refused for the same reason. `example.com` is the one reserved name it
#: accepts.
MARKER = "demo-seed"
EMAIL_DOMAIN = f"{MARKER}.example.com"
NAME_SUFFIX = "(demo)"

#: One password for every seeded account, so a demo is one paste rather than twelve.
#: It has to satisfy the real policy (length, classes, no product or personal name),
#: because registration runs the real validator.
PASSWORD = "Sealed#Books-2026"

# Small Indian businesses, because the product is GST-shaped and a demo full of
# "Acme Corp" reads as a template.
BUSINESSES: tuple[tuple[str, str], ...] = (
    ("Nirmal Traders", "Rakesh Iyer"),
    ("Saraswati Stationers", "Ananya Rao"),
    ("Deccan Auto Spares", "Imran Sheikh"),
    ("Kaveri Textiles", "Lakshmi Menon"),
    ("Bharat Cold Storage", "Vikram Nair"),
    ("Sunrise Dairy Supply", "Priya Deshmukh"),
    ("Gurgaon Print Works", "Aman Chopra"),
    ("Coastal Marine Foods", "Fernandes D'Souza"),
    ("Vidarbha Agro Tools", "Sunita Kale"),
    ("Meghna Electricals", "Tarun Ghosh"),
    ("Pallavi Interiors", "Pallavi Reddy"),
    ("Konark Hardware", "Devendra Patil"),
    ("Anand Packaging", "Nisha Bhatt"),
    ("Trilok Logistics", "Harpreet Singh"),
    ("Mysore Silk House", "Kavya Shetty"),
    ("Ratnagiri Chemicals", "Omkar Joshi"),
)

#: Two-line entries a real small business would actually post. Amounts in rupees.
ENTRIES: tuple[tuple[str, str, str, str], ...] = (
    ("Sale on credit, invoice 1041", "accounts_receivable", "sales_revenue", "48250.00"),
    ("Stock received from supplier", "inventory", "accounts_payable", "31400.00"),
    ("Counter sale, settled in cash", "cash", "sales_revenue", "6820.00"),
    ("Customer settled invoice 1041", "bank", "accounts_receivable", "48250.00"),
    ("Paid supplier on account", "accounts_payable", "bank", "31400.00"),
    ("Sale on credit, invoice 1042", "accounts_receivable", "sales_revenue", "12975.00"),
    ("Owner introduced capital", "bank", "owner_capital", "150000.00"),
    ("Stock received, second lot", "inventory", "accounts_payable", "22600.00"),
)

#: What people actually write. Weighted towards praise and ideas because the ask was
#: for good feedback, but not exclusively - an inbox with no problems in it looks
#: manufactured, and the triage screen has four states to exercise.
FEEDBACK: tuple[tuple[str, int | None, str, str], ...] = (
    (
        "praise",
        5,
        "/trust",
        "Showed the verify page to our bank's relationship manager. He checked an "
        "invoice himself on his own laptop and asked whether we could do the same "
        "for last year. First time anyone there has looked at our books directly.",
    ),
    (
        "praise",
        5,
        "/verify",
        "The part I did not expect: it says what it does not prove, right on the "
        "screen. That is the reason I trusted the rest of it.",
    ),
    (
        "praise",
        4,
        "/billing",
        "Recording money in and out is genuinely two fields and a date. Our old "
        "software wanted a customer record before it would let me log a cash sale.",
    ),
    (
        "idea",
        4,
        "/trust",
        "Would like an email when the unsealed backlog goes past two days. I check "
        "the Trust screen out of habit now but I will forget eventually.",
    ),
    (
        "idea",
        None,
        "/invoices",
        "Please put the verification QR straight on the invoice PDF. Half our buyers "
        "will never open a link but they all scan.",
    ),
    (
        "praise",
        5,
        "/accounting",
        "Trial balance actually balances without me hunting a rounding difference. "
        "Small thing. It was never small in the last system.",
    ),
    (
        "idea",
        3,
        "/analytics",
        "The twelve-month trend is useful. A same-quarter-last-year comparison would "
        "be more useful for a seasonal business like ours.",
    ),
    (
        "problem",
        3,
        "/documents",
        "Scanned a supplier bill where the GSTIN was printed over the company stamp "
        "and the extraction picked up the stamp. It flagged low confidence so I "
        "caught it, but worth knowing.",
    ),
    (
        "praise",
        5,
        "/trust",
        "Sealing takes a few seconds and costs nothing. I had assumed anything "
        "touching a blockchain would want a wallet and a fee estimate.",
    ),
    (
        "question",
        None,
        "/trust",
        "If I move the signing key to the 2-of-3 setup with my accountant, do the "
        "seals we already wrote stay verifiable? Reading the docs I think yes.",
    ),
    (
        "praise",
        4,
        "/inventory",
        "Weighted average valuation matches what our CA computes by hand. That is "
        "the check I did first and it passed.",
    ),
    (
        "idea",
        4,
        None,
        "A read-only login for our chartered accountant would save us exporting "
        "statements every quarter.",
    ),
)


def _slugify(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")


async def wipe() -> int:
    """Delete everything a previous run created, matched on the marker.

    Matched on the marker rather than on a timestamp or a stored id list, so it
    stays correct across runs and cannot reach a real account: a real user cannot
    have an address at a reserved `.invalid` domain.
    """
    from sqlalchemy import delete, select

    from app.modules.feedback.models import Feedback, UsageEvent
    from app.modules.organizations.models import Organization
    from app.modules.users.models import User

    async with session_scope() as session:
        user_ids = (
            (await session.execute(select(User.id).where(User.email.like(f"%@{EMAIL_DOMAIN}"))))
            .scalars()
            .all()
        )
        org_ids = (
            (
                await session.execute(
                    select(Organization.id).where(Organization.name.like(f"%{NAME_SUFFIX}"))
                )
            )
            .scalars()
            .all()
        )

        removed = 0
        if org_ids:
            for table in (UsageEvent, Feedback):
                result = await session.execute(
                    delete(table).where(table.organization_id.in_(org_ids))
                )
                removed += result.rowcount or 0
        # Feedback from a seeded contact that carries no organization.
        result = await session.execute(
            delete(Feedback).where(Feedback.contact_email.like(f"%@{EMAIL_DOMAIN}"))
        )
        removed += result.rowcount or 0

        # Organizations and users are left in place: cascading a delete through the
        # journal, the audit trail and the seal tables is exactly the kind of
        # destructive query this script should not be inventing. `--wipe` clears the
        # rows that pollute `make evidence`; drop the database for a clean slate.
        print(f"removed {removed} feedback/usage row(s)")
        print(f"left in place: {len(org_ids)} organization(s), {len(user_ids)} user(s)")
        print("for a full reset: make clean && make setup")
        return removed


async def seed(*, organizations: int, entries: int, dry_run: bool) -> int:
    from app.modules.accounting.models import JournalType
    from app.modules.accounting.service import PostingService
    from app.modules.auth.schemas import RegisterRequest
    from app.modules.auth.service import AuthService
    from app.modules.feedback.models import FeedbackKind
    from app.modules.feedback.service import FeedbackService

    if organizations > len(BUSINESSES):
        print(
            f"note: only {len(BUSINESSES)} business names are defined; "
            f"seeding {len(BUSINESSES)} rather than {organizations}."
        )
        organizations = len(BUSINESSES)

    ctx = RequestContext(
        ip_address="127.0.0.1", user_agent="seed_demo.py", request_id=str(uuid.uuid4())
    )
    today = dt.date.today()
    created: list[tuple[str, str, uuid.UUID]] = []

    if dry_run:
        print(f"would seed {organizations} organization(s), {entries} entr(y/ies) each")
        for name, owner in BUSINESSES[:organizations]:
            print(f"  {name} {NAME_SUFFIX}  <-  {_slugify(owner)}@{EMAIL_DOMAIN}")
        print(f"would add {len(FEEDBACK)} feedback row(s)")
        return 0

    for name, owner_name in BUSINESSES[:organizations]:
        email = f"{_slugify(owner_name)}@{EMAIL_DOMAIN}"
        org_name = f"{name} {NAME_SUFFIX}"

        # One transaction per organization: a failure part-way leaves the ones
        # already made intact and re-running skips them, so this is resumable.
        try:
            async with session_scope() as session:
                auth = AuthService(session)
                user, organization_id = await auth.register(
                    RegisterRequest(
                        email=email,
                        password=PASSWORD,
                        full_name=owner_name,
                        organization_name=org_name,
                    ),
                    ctx,
                )
                if organization_id is None:
                    print(f"  ! {org_name}: registered without an organization, skipped")
                    continue

                posting = PostingService(session)
                for i in range(entries):
                    narration, debit, credit, amount = ENTRIES[i % len(ENTRIES)]
                    await posting.post_simple(
                        organization_id,
                        user,
                        journal_type=JournalType.GENERAL,
                        entry_date=today - dt.timedelta(days=entries - i),
                        narration=narration,
                        debit_key=debit,
                        credit_key=credit,
                        amount=Decimal(amount),
                    )
                created.append((org_name, email, organization_id))
                print(
                    f"  + {org_name:34} {email:38} {entries} entr{'y' if entries == 1 else 'ies'}"
                )
        except Exception as exc:
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            print(f"  = {org_name:34} skipped: {first[:70]}")

    # Feedback last, spread across every seeded organization - not only the ones this
    # run created.
    #
    # Those are different sets, and assuming they were the same was a bug: `--wipe`
    # removes feedback but deliberately leaves organizations alone, so the obvious
    # recovery (wipe, then seed again) found every account already registered, created
    # nothing, and therefore attached no feedback. The inbox stayed empty with no
    # explanation. Existing seeded organizations are looked up instead.
    async with session_scope() as session:
        from sqlalchemy import select

        from app.modules.organizations.models import Organization

        existing = (
            (
                await session.execute(
                    select(Organization.name, Organization.id)
                    .where(Organization.name.like(f"%{NAME_SUFFIX}"))
                    .order_by(Organization.created_at)
                )
            )
            .tuples()
            .all()
        )

    targets: list[tuple[str, str, uuid.UUID]] = created or [
        (name, f"{_slugify(name.removesuffix(NAME_SUFFIX).strip())}@{EMAIL_DOMAIN}", org_id)
        for name, org_id in existing
    ]

    added = 0
    if targets:
        async with session_scope() as session:
            service = FeedbackService(session)
            for i, (kind, rating, screen, message) in enumerate(FEEDBACK):
                org_name, email, organization_id = targets[i % len(targets)]
                await service.submit(
                    kind=FeedbackKind(kind),
                    message=message,
                    rating=rating,
                    screen=screen,
                    contact_email=email,
                    organization_id=organization_id,
                    ctx=ctx,
                )
                added += 1

    print()
    print(f"seeded {len(created)} organization(s) and {added} feedback row(s)")
    if created:
        print(f"sign in as any of them with password: {PASSWORD}")
        print(f"  e.g. {created[0][1]}")
    print()
    print("Every row is marked as demo data - see this script's docstring. `make evidence`")
    print("reads these same tables, so do not submit a seeded count as real adoption.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seed_demo.py",
        description="Seed a development install with demo organizations and feedback.",
    )
    parser.add_argument(
        "--organizations", type=int, default=12, help="how many to create (default 12)"
    )
    parser.add_argument(
        "--entries", type=int, default=3, help="journal entries per organization (default 3)"
    )
    parser.add_argument(
        "--wipe", action="store_true", help="remove the feedback/usage rows a previous run made"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would be created and stop"
    )
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.wipe:
        await wipe()
        return 0
    return await seed(
        organizations=args.organizations,
        entries=args.entries,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
