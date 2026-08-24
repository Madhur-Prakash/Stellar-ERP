"""Role and permission endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.schemas import MessageResponse, with_computed
from app.modules.auth.dependencies import (
    ActiveOrganizationId,
    CurrentUser,
    DbSession,
    RequestCtx,
    require_permission,
)
from app.modules.rbac.permissions import Permission
from app.modules.rbac.schemas import (
    PermissionCatalogue,
    RoleCreate,
    RoleDetail,
    RoleRead,
    RoleUpdate,
)
from app.modules.rbac.service import RoleService

router = APIRouter(prefix="/roles", tags=["Roles & permissions"])


def get_role_service(session: DbSession) -> RoleService:
    return RoleService(session)


RoleServiceDep = Annotated[RoleService, Depends(get_role_service)]


@router.get(
    "/permissions",
    response_model=PermissionCatalogue,
    summary="The permission catalogue",
)
async def list_permissions(
    _: Annotated[None, Depends(require_permission(Permission.ROLE_READ))],
) -> PermissionCatalogue:
    """Every permission the backend enforces, grouped for the role editor."""
    return RoleService.permission_catalogue()


@router.get("", response_model=list[RoleRead], summary="List roles")
async def list_roles(
    organization_id: ActiveOrganizationId,
    service: RoleServiceDep,
    _: Annotated[None, Depends(require_permission(Permission.ROLE_READ))],
) -> list[RoleRead]:
    return [
        with_computed(RoleRead, role, member_count=count)
        for role, count in await service.list_roles(organization_id)
    ]


@router.post(
    "",
    response_model=RoleDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a custom role",
)
async def create_role(
    data: RoleCreate,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: RoleServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ROLE_CREATE))],
) -> RoleDetail:
    role = await service.create_role(organization_id, data, user, ctx)
    return with_computed(
        RoleDetail,
        role,
        member_count=0,
        effective_permissions=RoleService.effective_permissions(role),
    )


@router.get("/{role_id}", response_model=RoleDetail, summary="Get a role")
async def get_role(
    role_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: RoleServiceDep,
    _: Annotated[None, Depends(require_permission(Permission.ROLE_READ))],
) -> RoleDetail:
    """Returns both the stored grants and their expanded form."""
    role, member_count = await service.get_role(organization_id, role_id)
    return with_computed(
        RoleDetail,
        role,
        member_count=member_count,
        effective_permissions=RoleService.effective_permissions(role),
    )


@router.patch("/{role_id}", response_model=RoleDetail, summary="Update a role")
async def update_role(
    role_id: uuid.UUID,
    data: RoleUpdate,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: RoleServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ROLE_UPDATE))],
) -> RoleDetail:
    """Permission changes apply immediately to everyone holding the role."""
    role = await service.update_role(organization_id, role_id, data, user, ctx)
    # Not `_`: that name is taken by the permission dependency above, which is
    # typed `None`, so reusing it here is a type error.
    _fresh, member_count = await service.get_role(organization_id, role_id)
    return with_computed(
        RoleDetail,
        role,
        member_count=member_count,
        effective_permissions=RoleService.effective_permissions(role),
    )


@router.delete("/{role_id}", response_model=MessageResponse, summary="Delete a role")
async def delete_role(
    role_id: uuid.UUID,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: RoleServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ROLE_DELETE))],
) -> MessageResponse:
    """Custom roles only, and only when no member holds them."""
    await service.delete_role(organization_id, role_id, user, ctx)
    return MessageResponse(message="Role deleted.")
