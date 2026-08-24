"""User data access."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import ClassVar

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.db.repository import BaseRepository
from app.modules.organizations.models import MemberStatus, OrganizationMember
from app.modules.users.models import User


class UserRepository(BaseRepository[User]):
    model = User
    sortable_fields: ClassVar[frozenset[str]] = frozenset(
        {"created_at", "full_name", "email", "last_login_at"}
    )

    async def get_by_email(self, email: str, *, include_deleted: bool = False) -> User | None:
        """Case-insensitive lookup.

        Emails are stored lower-cased by the service layer, but this compares via
        ``lower()`` anyway so a row written by a fixture or data migration with
        mixed case is still found rather than silently duplicating an account.
        """
        query = self._base_query(include_deleted=include_deleted).where(
            func.lower(User.email) == email.strip().lower()
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        query = (
            select(func.count())
            .select_from(User)
            .where(func.lower(User.email) == email.strip().lower())
        )
        return bool((await self.session.execute(query)).scalar_one())

    async def get_with_memberships(self, user_id: uuid.UUID) -> User | None:
        """Load the user with memberships, roles, and organizations eagerly.

        ``selectinload`` rather than lazy access: this feeds ``/auth/me``, and
        lazy loading here would either N+1 or raise ``MissingGreenlet`` under
        async SQLAlchemy.
        """
        query = (
            self._base_query()
            .where(User.id == user_id)
            .options(
                selectinload(User.memberships).selectinload(OrganizationMember.organization),
                selectinload(User.memberships).selectinload(OrganizationMember.role),
            )
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def active_memberships(self, user_id: uuid.UUID) -> Sequence[OrganizationMember]:
        """The user's usable memberships, with org and role loaded.

        Excludes suspended memberships and soft-deleted organizations - a
        suspended member must not be able to switch into that org.
        """
        from app.modules.organizations.models import Organization

        query = (
            select(OrganizationMember)
            .join(Organization, OrganizationMember.organization_id == Organization.id)
            .where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.status == MemberStatus.ACTIVE,
                Organization.deleted_at.is_(None),
                Organization.is_active.is_(True),
            )
            .options(
                selectinload(OrganizationMember.organization),
                selectinload(OrganizationMember.role),
            )
            .order_by(OrganizationMember.created_at)
        )
        return (await self.session.execute(query)).scalars().all()

    async def touch_last_login(self, user: User) -> None:
        now = dt.datetime.now(dt.UTC)
        user.last_login_at = now
        user.last_seen_at = now
        await self.session.flush()
