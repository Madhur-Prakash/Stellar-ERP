"""Role data access."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import ClassVar

from sqlalchemy import select

from app.db.repository import BaseRepository
from app.modules.rbac.models import Role
from app.modules.rbac.permissions import (
    SYSTEM_ROLE_DESCRIPTIONS,
    SYSTEM_ROLE_PERMISSIONS,
    SystemRole,
)


class RoleRepository(BaseRepository[Role]):
    model = Role
    sortable_fields: ClassVar[frozenset[str]] = frozenset({"created_at", "name"})

    async def get_by_slug(self, organization_id: uuid.UUID, slug: str) -> Role | None:
        query = select(Role).where(
            Role.organization_id == organization_id,
            Role.slug == slug.strip().lower(),
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_scoped(self, organization_id: uuid.UUID, role_id: uuid.UUID) -> Role | None:
        """Fetch a role, asserting it belongs to the given organization.

        The tenant filter is part of the query, not a check afterwards: it makes
        cross-tenant access a non-result rather than something a caller has to
        remember to validate.
        """
        query = select(Role).where(Role.id == role_id, Role.organization_id == organization_id)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list_for_organization(self, organization_id: uuid.UUID) -> Sequence[Role]:
        query = (
            select(Role)
            .where(Role.organization_id == organization_id)
            .order_by(Role.is_system.desc(), Role.name)
        )
        return (await self.session.execute(query)).scalars().all()

    async def get_default(self, organization_id: uuid.UUID) -> Role | None:
        query = select(Role).where(
            Role.organization_id == organization_id, Role.is_default.is_(True)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def seed_system_roles(self, organization_id: uuid.UUID) -> dict[SystemRole, Role]:
        """Create the baseline roles for a new organization.

        Each org gets its own copy (see :class:`app.modules.rbac.models.Role`), so
        one tenant editing "Accountant" cannot affect another. ``viewer`` is the
        default for invitees - least privilege, and easy to escalate deliberately.
        """
        created: dict[SystemRole, Role] = {}

        for system_role in SystemRole:
            grants = [str(grant) for grant in SYSTEM_ROLE_PERMISSIONS[system_role]]
            role = Role(
                organization_id=organization_id,
                name=system_role.value.replace("_", " ").title(),
                slug=system_role.value,
                description=SYSTEM_ROLE_DESCRIPTIONS[system_role],
                permissions=grants,
                is_system=True,
                is_default=system_role is SystemRole.VIEWER,
            )
            self.session.add(role)
            created[system_role] = role

        await self.session.flush()
        return created
