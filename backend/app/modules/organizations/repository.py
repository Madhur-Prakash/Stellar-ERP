"""Organization, membership, and invitation data access."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Sequence
from typing import ClassVar

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.security import hash_token
from app.db.repository import BaseRepository, rows_affected
from app.modules.organizations.models import (
    Invitation,
    InvitationStatus,
    MemberStatus,
    Organization,
    OrganizationMember,
)

#: Slugs are used in URLs, so anything outside this set is collapsed to hyphens.
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

#: Reserved because they would collide with real or future application routes.
RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        "api",
        "app",
        "admin",
        "auth",
        "login",
        "logout",
        "register",
        "signup",
        "settings",
        "billing",
        "help",
        "support",
        "docs",
        "status",
        "health",
        "static",
        "assets",
        "public",
        "www",
        "mail",
        "new",
        "me",
        "dashboard",
    }
)


def slugify(value: str) -> str:
    """Reduce a display name to a URL-safe slug."""
    slug = _SLUG_STRIP.sub("-", value.strip().lower()).strip("-")
    return slug[:90] or "org"


class OrganizationRepository(BaseRepository[Organization]):
    model = Organization
    sortable_fields: ClassVar[frozenset[str]] = frozenset({"created_at", "name"})

    async def get_by_slug(self, slug: str) -> Organization | None:
        query = self._base_query().where(Organization.slug == slug.strip().lower())
        return (await self.session.execute(query)).scalar_one_or_none()

    async def slug_taken(self, slug: str) -> bool:
        """Checks *all* rows, including soft-deleted ones.

        A slug freed by deletion must stay reserved: reusing it would silently
        redirect bookmarked URLs to a different company's data.
        """
        query = (
            select(func.count())
            .select_from(Organization)
            .where(Organization.slug == slug.strip().lower())
        )
        return bool((await self.session.execute(query)).scalar_one())

    async def generate_unique_slug(self, name: str) -> str:
        """Derive an available slug from a display name.

        Appends ``-2``, ``-3``, … on collision, then falls back to a random
        suffix. The loop is bounded because an unbounded one is a denial-of-
        service vector on a popular name.
        """
        base = slugify(name)
        if base in RESERVED_SLUGS:
            base = f"{base}-co"

        if not await self.slug_taken(base):
            return base

        for suffix in range(2, 50):
            candidate = f"{base}-{suffix}"
            if not await self.slug_taken(candidate):
                return candidate

        return f"{base}-{uuid.uuid4().hex[:8]}"


class MemberRepository(BaseRepository[OrganizationMember]):
    model = OrganizationMember
    sortable_fields: ClassVar[frozenset[str]] = frozenset(
        {"created_at", "joined_at", "last_active_at"}
    )

    async def get_membership(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMember | None:
        """Fetch a membership with its role and user loaded.

        This is the hot path for authorization on every request, so the role is
        eager-loaded - the permission check needs it immediately.
        """
        query = (
            select(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
            )
            .options(
                selectinload(OrganizationMember.role),
                selectinload(OrganizationMember.user),
            )
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_with_relations(self, member_id: uuid.UUID) -> OrganizationMember | None:
        query = (
            select(OrganizationMember)
            .where(OrganizationMember.id == member_id)
            .options(
                selectinload(OrganizationMember.role),
                selectinload(OrganizationMember.user),
                selectinload(OrganizationMember.organization),
            )
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list_for_organization(
        self, organization_id: uuid.UUID, *, status: MemberStatus | None = None
    ) -> Sequence[OrganizationMember]:
        query = (
            select(OrganizationMember)
            .where(OrganizationMember.organization_id == organization_id)
            .options(
                selectinload(OrganizationMember.role),
                selectinload(OrganizationMember.user),
            )
            .order_by(OrganizationMember.is_owner.desc(), OrganizationMember.created_at)
        )
        if status is not None:
            query = query.where(OrganizationMember.status == status)
        return (await self.session.execute(query)).scalars().all()

    async def get_owner(self, organization_id: uuid.UUID) -> OrganizationMember | None:
        query = (
            select(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.is_owner.is_(True),
            )
            .options(selectinload(OrganizationMember.user))
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def count_active(self, organization_id: uuid.UUID) -> int:
        query = (
            select(func.count())
            .select_from(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.status == MemberStatus.ACTIVE,
            )
        )
        return int((await self.session.execute(query)).scalar_one())

    async def count_with_role(self, role_id: uuid.UUID) -> int:
        """Used to block deletion of a role that people still hold."""
        query = (
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.role_id == role_id)
        )
        return int((await self.session.execute(query)).scalar_one())


class InvitationRepository(BaseRepository[Invitation]):
    model = Invitation
    sortable_fields: ClassVar[frozenset[str]] = frozenset({"created_at", "email"})

    async def get_by_token(self, token: str) -> Invitation | None:
        """Look up by the token's digest, with org, role and inviter eager-loaded.

        The acceptance flow needs the org name for the confirmation screen and
        the role to create the membership.

        `invited_by` is loaded here for the same reason, and the omission was a
        real bug: the preview endpoint reads `invitation.invited_by.full_name`
        to render "X invited you", and a lazy load under `AsyncSession` raises
        `MissingGreenlet`. That is a `SQLAlchemyError`, so the handler turned it
        into a 503 and every genuinely valid invitation link rendered as "this
        invitation is no longer valid". Nothing caught it because the invitation
        itself was fine - the failure was one attribute away from the check.
        """
        query = (
            select(Invitation)
            .where(Invitation.token_hash == hash_token(token))
            .options(
                selectinload(Invitation.organization),
                selectinload(Invitation.role),
                selectinload(Invitation.invited_by),
            )
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_pending(self, organization_id: uuid.UUID, email: str) -> Invitation | None:
        query = select(Invitation).where(
            Invitation.organization_id == organization_id,
            func.lower(Invitation.email) == email.strip().lower(),
            Invitation.status == InvitationStatus.PENDING,
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list_for_organization(
        self, organization_id: uuid.UUID, *, status: InvitationStatus | None = None
    ) -> Sequence[Invitation]:
        query = (
            select(Invitation)
            .where(Invitation.organization_id == organization_id)
            .options(selectinload(Invitation.role), selectinload(Invitation.invited_by))
            .order_by(Invitation.created_at.desc())
        )
        if status is not None:
            query = query.where(Invitation.status == status)
        return (await self.session.execute(query)).scalars().all()

    async def expire_stale(self, organization_id: uuid.UUID | None = None) -> int:
        """Flip lapsed pending invitations to ``EXPIRED``.

        Expiry is enforced at read time regardless (:attr:`Invitation.is_redeemable`);
        this only keeps the list view honest so an admin is not looking at rows
        labelled "pending" that can no longer be accepted.
        """
        from sqlalchemy import update

        statement = (
            update(Invitation)
            .where(
                Invitation.status == InvitationStatus.PENDING,
                Invitation.expires_at <= dt.datetime.now(dt.UTC),
            )
            .values(status=InvitationStatus.EXPIRED)
        )
        if organization_id is not None:
            statement = statement.where(Invitation.organization_id == organization_id)

        result = await self.session.execute(statement)
        await self.session.flush()
        return rows_affected(result)
