"""Role management business logic.

The guard rails here exist to stop an organization from breaking its own access
model: system roles are protected, and a role still held by members cannot be
deleted out from under them.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.modules.audit.models import AuditAction
from app.modules.audit.service import AuditService, diff
from app.modules.auth.token_store import token_epochs
from app.modules.organizations.repository import MemberRepository, slugify
from app.modules.rbac.models import Role
from app.modules.rbac.permissions import (
    PERMISSION_GROUPS,
    Permission,
    expand_grants,
)
from app.modules.rbac.repository import RoleRepository
from app.modules.rbac.schemas import (
    PermissionCatalogue,
    PermissionGroupInfo,
    PermissionInfo,
    RoleCreate,
    RoleUpdate,
)
from app.modules.users.models import User

log = get_logger(__name__)


class RoleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.roles = RoleRepository(session)
        self.members = MemberRepository(session)
        self.audit = AuditService(session)

    # =========================================================================
    # Reads
    # =========================================================================
    async def list_roles(self, organization_id: uuid.UUID) -> list[tuple[Role, int]]:
        """Roles with the number of members holding each."""
        roles = await self.roles.list_for_organization(organization_id)
        return [(role, await self.members.count_with_role(role.id)) for role in roles]

    async def get_role(self, organization_id: uuid.UUID, role_id: uuid.UUID) -> tuple[Role, int]:
        role = await self.roles.get_scoped(organization_id, role_id)
        if role is None:
            raise NotFoundError("Role")
        return role, await self.members.count_with_role(role.id)

    @staticmethod
    def permission_catalogue() -> PermissionCatalogue:
        """The catalogue, grouped for the role editor."""
        groups = [
            PermissionGroupInfo(
                key=group.key,
                label=group.label,
                description=group.description,
                permissions=[
                    PermissionInfo(
                        slug=permission.value,
                        resource=permission.resource,
                        action=permission.action,
                    )
                    for permission in group.permissions
                ],
            )
            for group in PERMISSION_GROUPS
        ]
        return PermissionCatalogue(groups=groups, total=len(list(Permission)))

    # =========================================================================
    # Writes
    # =========================================================================
    async def create_role(
        self,
        organization_id: uuid.UUID,
        data: RoleCreate,
        actor: User,
        ctx: RequestContext,
    ) -> Role:
        slug = slugify(data.name)

        if await self.roles.get_by_slug(organization_id, slug) is not None:
            raise ConflictError(
                "A role with that name already exists",
                code="role_exists",
                details={"field": "name"},
            )

        role = await self.roles.add(
            Role(
                organization_id=organization_id,
                name=data.name,
                slug=slug,
                description=data.description,
                permissions=data.permissions,
                is_system=False,
            )
        )

        await self.audit.record(
            AuditAction.ROLE_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="role",
            resource_id=role.id,
            summary=f"Created role {role.name}",
            context={"permissions": data.permissions},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        log.info(
            "role created",
            extra={"organization_id": str(organization_id), "role": role.slug},
        )
        return role

    async def update_role(
        self,
        organization_id: uuid.UUID,
        role_id: uuid.UUID,
        data: RoleUpdate,
        actor: User,
        ctx: RequestContext,
    ) -> Role:
        """Update a role.

        A system role's *permissions* are editable - an org may legitimately want
        its accountants to approve invoices - but its name and slug are not,
        because code and seed data refer to the slug.
        """
        role = await self.roles.get_scoped(organization_id, role_id)
        if role is None:
            raise NotFoundError("Role")

        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        if not changes:
            return role

        if role.is_system and "name" in changes:
            raise BusinessRuleError(
                "Built-in roles cannot be renamed. Create a custom role instead.",
                code="cannot_rename_system_role",
            )

        if role.is_full_access and "permissions" in changes:
            raise BusinessRuleError(
                "The Owner role must retain full access",
                code="cannot_restrict_owner_role",
            )

        before = {field: getattr(role, field) for field in changes}

        # Only one default role per organization; clear the previous holder
        # before setting a new one, or the partial unique index rejects the write.
        if changes.get("is_default") is True:
            current = await self.roles.get_default(organization_id)
            if current is not None and current.id != role.id:
                current.is_default = False
                await self.session.flush()

        await self.roles.update(role, **changes)

        # Permission changes must reach existing sessions immediately, so every
        # affected member's tokens are invalidated and re-minted.
        if "permissions" in changes:
            for member in await self.members.list_for_organization(organization_id):
                if member.role_id == role.id:
                    await token_epochs.bump(member.user_id)

        await self.audit.record(
            AuditAction.ROLE_UPDATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="role",
            resource_id=role.id,
            summary=f"Updated role {role.name}",
            changes=diff(before, changes),
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return role

    async def delete_role(
        self,
        organization_id: uuid.UUID,
        role_id: uuid.UUID,
        actor: User,
        ctx: RequestContext,
    ) -> None:
        """Delete a custom role that nobody currently holds."""
        role = await self.roles.get_scoped(organization_id, role_id)
        if role is None:
            raise NotFoundError("Role")

        if role.is_system:
            raise BusinessRuleError(
                "Built-in roles cannot be deleted", code="cannot_delete_system_role"
            )

        # Also enforced by the RESTRICT foreign key; checked here so the caller
        # gets an actionable message rather than an integrity error.
        member_count = await self.members.count_with_role(role.id)
        if member_count:
            raise BusinessRuleError(
                f"{member_count} member(s) still have this role. Reassign them first.",
                code="role_in_use",
                details={"member_count": member_count},
            )

        role_name = role.name
        await self.audit.record(
            AuditAction.ROLE_DELETED,
            actor=actor,
            organization_id=organization_id,
            resource_type="role",
            resource_id=role.id,
            summary=f"Deleted role {role_name}",
            context={"permissions": role.permissions},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        await self.roles.hard_delete(role)

    @staticmethod
    def effective_permissions(role: Role) -> list[str]:
        return sorted(expand_grants(role.permissions))
