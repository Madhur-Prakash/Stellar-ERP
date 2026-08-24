"""Feedback and usage - collecting, and refusing to collect too much.

The interesting code in this module is the allow-list. Everything else is a write
and two reads.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.logging import current_log_context, get_logger
from app.db.repository import BaseRepository
from app.modules.feedback.models import (
    Feedback,
    FeedbackKind,
    FeedbackStatus,
    UsageEvent,
)
from app.modules.users.models import User

log = get_logger(__name__)

#: Every action name the usage recorder will accept.
#:
#: A closed set, checked at the door. An unknown action is dropped with a warning
#: rather than stored, for the same reason the audit trail uses an enum: a typo'd
#: free string creates a metric nobody will ever search for, and by the time
#: anybody notices, the real events are spread across three spellings.
#:
#: Adding one is a one-line change here, which is the point - it puts a human in
#: front of the decision "should the product be counting this?"
KNOWN_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        # Screens opened. The denominator for everything else.
        "screen.dashboard",
        "screen.billing",
        "screen.accounts",
        "screen.accounting",
        "screen.sales",
        "screen.inventory",
        "screen.documents",
        "screen.analytics",
        "screen.trust",
        "screen.settings",
        "screen.verify",
        # The proof ledger's funnel, which is what Level 4 is actually about:
        # how many businesses reach the Trust screen, how many switch sealing on,
        # how many seal more than once, and how many ever export a proof.
        "attestation.enabled",
        "attestation.disabled",
        "seal.now",
        "proof.export",
        "proof.verified",
        "proof.rejected",
        # Ordinary business actions, so "is anybody using this?" has an answer.
        "entry.posted",
        "invoice.posted",
        "bill.posted",
        "document.uploaded",
        "feedback.submitted",
        # Onboarding.
        "user.registered",
        "organization.created",
    }
)

#: Context keys a caller may attach to a usage event.
#:
#: Everything else is dropped. Note what is *not* here: no id, no amount, no name,
#: no path. The table must stay outside the compliance boundary, and the only way
#: to guarantee that is for it to be structurally impossible to put a customer's
#: name in it - not merely against the rules.
ALLOWED_CONTEXT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "surface",  # web | desktop
        "network",  # testnet | public
        "cadence",  # the sealing schedule chosen
        "outcome",  # ok | failed | unknown
        "count",  # a bounded integer, e.g. entries in a seal
        "verified",  # the verifier's boolean verdict
    }
)

#: Actions that may be recorded with **no signed-in user and no organization**.
#:
#: Everything else needs a session, because an event with no organization cannot
#: answer the question this table exists for - how many *businesses* are doing
#: something.
#:
#: These three are the exception, and they are the point of the whole subsystem: a
#: verification is performed by a bank, a buyer, or an auditor who has no account
#: here and never will. "How many proofs were checked by people outside the
#: business?" is the single most important number this product can report, and
#: requiring a login to count it would guarantee the answer was always zero.
#:
#: They carry no organization, so they never appear in the distinct-organization
#: counts - only in the per-action totals, which is exactly what they are for.
PUBLIC_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "screen.verify",
        "proof.verified",
        "proof.rejected",
    }
)

#: Ceiling on a context value's length, so a stray sentence cannot become storage.
_MAX_CONTEXT_LEN: Final = 40


class FeedbackRepository(BaseRepository[Feedback]):
    model = Feedback
    sortable_fields = frozenset({"created_at", "rating", "kind", "status"})
    default_sort = "-created_at"


class FeedbackService:
    """Accepting a message, and reading the inbox."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = FeedbackRepository(session)

    async def submit(
        self,
        *,
        kind: FeedbackKind,
        message: str,
        rating: int | None = None,
        screen: str | None = None,
        contact_email: str | None = None,
        user: User | None = None,
        organization_id: uuid.UUID | None = None,
        ctx: RequestContext | None = None,
    ) -> Feedback:
        """Store one message.

        Accepted from an unauthenticated caller on purpose. The most useful report
        in any product is from somebody who could not get in, and a foreign key
        requirement would silence exactly that person - so ``user`` and
        ``organization_id`` are optional and the email is free text.
        """
        entry = Feedback(
            kind=kind,
            status=FeedbackStatus.NEW,
            message=message.strip(),
            rating=rating,
            screen=screen,
            user_id=user.id if user else None,
            organization_id=organization_id,
            # Prefer the signed-in address over anything typed: a person reporting
            # a bug should not have to get their own email right for us to reply.
            contact_email=(user.email if user else None) or contact_email,
            user_agent=ctx.user_agent if ctx else None,
            app_version=settings.app_version,
            request_id=current_log_context().get("request_id"),
        )
        await self.repo.add(entry)

        log.info(
            "feedback received",
            extra={
                "kind": kind.value,
                "rating": rating,
                "screen": screen,
                "signed_in": bool(user),
            },
        )
        return entry

    async def inbox(
        self,
        *,
        status: FeedbackStatus | None = None,
        limit: int = 50,
    ) -> Sequence[Feedback]:
        """Newest first, optionally filtered by triage state.

        Superuser-only at the router, and deliberately *not* organization-scoped:
        feedback is about the product, and the person who has to read it is
        whoever maintains the install, not whoever happens to be signed in.
        """
        where = [] if status is None else [Feedback.status == status]
        return await self.repo.list_all(*where, limit=limit)

    async def summary(self) -> dict[str, Any]:
        """Counts and the mean rating - what a maintainer looks at first."""
        by_kind = (
            await self.session.execute(select(Feedback.kind, func.count()).group_by(Feedback.kind))
        ).all()
        by_status = (
            await self.session.execute(
                select(Feedback.status, func.count()).group_by(Feedback.status)
            )
        ).all()
        rating = (
            await self.session.execute(
                select(func.avg(Feedback.rating), func.count(Feedback.rating))
            )
        ).one()

        return {
            "total": sum(int(count) for _, count in by_kind),
            "by_kind": {str(kind): int(count) for kind, count in by_kind},
            "by_status": {str(status): int(count) for status, count in by_status},
            # A mean over a handful of ratings is noise, so the count travels with
            # it and the UI can decline to show an average of three.
            "average_rating": round(float(rating[0]), 2) if rating[0] is not None else None,
            "rated_count": int(rating[1]),
        }

    async def triage(
        self,
        feedback_id: uuid.UUID,
        *,
        status: FeedbackStatus,
        note: str | None = None,
    ) -> Feedback:
        from app.core.exceptions import NotFoundError

        entry = await self.repo.get(feedback_id)
        if entry is None:
            raise NotFoundError("Feedback")
        entry.status = status
        if note is not None:
            entry.triage_note = note
        await self.session.flush()
        return entry


class UsageService:
    """Recording what people do, and nothing about who they do it with."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        action: str,
        *,
        user: User | None = None,
        organization_id: uuid.UUID | None = None,
        surface: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> UsageEvent | None:
        """Store one event, or drop it.

        Returns ``None`` when analytics is switched off or the action is unknown.
        Dropping is the correct behaviour for an unknown action: this is telemetry,
        and telemetry must never be able to fail the thing it is measuring.

        The context is filtered rather than rejected. A caller passing one
        disallowed key alongside three good ones should still get an event, and the
        alternative - refusing the whole write - would tempt somebody to remove the
        allow-list rather than fix the caller.
        """
        if not settings.usage_analytics_enabled:
            return None

        if action not in KNOWN_ACTIONS:
            log.warning(
                "dropping an unknown usage action - add it to KNOWN_ACTIONS if it is real",
                extra={"action": action},
            )
            return None

        event = UsageEvent(
            action=action,
            user_id=user.id if user else None,
            organization_id=organization_id,
            surface=(surface or "web")[:20],
            context=self._filter(context),
        )
        self.session.add(event)
        await self.session.flush()
        return event

    @staticmethod
    def _filter(context: dict[str, Any] | None) -> dict[str, Any]:
        """Keep only allow-listed keys, with short scalar values."""
        if not context:
            return {}

        cleaned: dict[str, Any] = {}
        for key, value in context.items():
            if key not in ALLOWED_CONTEXT_KEYS:
                continue
            if isinstance(value, bool | int):
                cleaned[key] = value
            elif isinstance(value, str):
                cleaned[key] = value[:_MAX_CONTEXT_LEN]
            # Anything else - a dict, a list, a Decimal - is dropped rather than
            # stringified. Stringifying is how a nested object carrying an amount
            # ends up in here as a JSON blob.
        return cleaned

    async def rollup(self, *, days: int = 30) -> dict[str, Any]:
        """Actions and distinct organizations over a window.

        The whole analytics surface, and deliberately small. It answers the two
        questions a maintainer of this product actually has - what is being used,
        and by how many businesses - and nothing else. A richer view belongs in
        whatever the operator already uses to query their own database.
        """
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)

        rows = (
            await self.session.execute(
                select(
                    UsageEvent.action,
                    func.count().label("events"),
                    func.count(func.distinct(UsageEvent.organization_id)).label("orgs"),
                    func.count(func.distinct(UsageEvent.user_id)).label("users"),
                )
                .where(UsageEvent.created_at >= since)
                .group_by(UsageEvent.action)
                .order_by(func.count().desc())
            )
        ).all()

        active_orgs = (
            await self.session.execute(
                select(func.count(func.distinct(UsageEvent.organization_id))).where(
                    UsageEvent.created_at >= since
                )
            )
        ).scalar_one()

        active_users = (
            await self.session.execute(
                select(func.count(func.distinct(UsageEvent.user_id))).where(
                    UsageEvent.created_at >= since
                )
            )
        ).scalar_one()

        return {
            "days": days,
            "since": since.isoformat(),
            "active_organizations": int(active_orgs or 0),
            "active_users": int(active_users or 0),
            "actions": [
                {
                    "action": row.action,
                    "events": int(row.events),
                    "organizations": int(row.orgs),
                    "users": int(row.users),
                }
                for row in rows
            ],
        }
