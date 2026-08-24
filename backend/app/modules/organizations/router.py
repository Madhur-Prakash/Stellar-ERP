"""Organization, member, and invitation endpoints.

Every route except organization *creation* and invitation *preview* is scoped to
the caller's active organization, taken from the signed token rather than a path
parameter. That is what makes cross-tenant access structurally impossible: there
is no organization id in the URL for a client to tamper with.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.schemas import MessageResponse
from app.modules.auth.dependencies import (
    ActiveOrganizationId,
    CurrentUser,
    DbSession,
    RequestCtx,
    VerifiedUser,
    require_permission,
)
from app.modules.organizations.schemas import (
    AcceptInvitationRequest,
    InvitationCreate,
    InvitationPreview,
    InvitationRead,
    MemberRead,
    MemberUpdate,
    OrganizationCreate,
    OrganizationListItem,
    OrganizationRead,
    OrganizationUpdate,
)
from app.modules.organizations.service import OrganizationService
from app.modules.rbac.permissions import Permission

router = APIRouter(prefix="/organizations", tags=["Organizations"])


def get_organization_service(session: DbSession) -> OrganizationService:
    return OrganizationService(session)


OrgServiceDep = Annotated[OrganizationService, Depends(get_organization_service)]


# =============================================================================
# Organizations
# =============================================================================
@router.get("", response_model=list[OrganizationListItem], summary="Your organizations")
async def list_my_organizations(
    user: CurrentUser,
    service: OrgServiceDep,
) -> list[OrganizationListItem]:
    """Feeds the organization switcher."""
    items = await service.list_for_user(user)
    return [OrganizationListItem.model_validate(item) for item in items]


@router.post(
    "",
    response_model=OrganizationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organization",
)
async def create_organization(
    data: OrganizationCreate,
    user: VerifiedUser,
    service: OrgServiceDep,
    ctx: RequestCtx,
) -> OrganizationRead:
    """The caller becomes owner. Requires a verified email address."""
    organization = await service.create(data, user, ctx)
    return OrganizationRead.model_validate(organization)


@router.get("/current", response_model=OrganizationRead, summary="The active organization")
async def get_current_organization(
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    _: Annotated[None, Depends(require_permission(Permission.ORG_READ))],
) -> OrganizationRead:
    return OrganizationRead.model_validate(await service.get(organization_id))


@router.patch(
    "/current",
    response_model=OrganizationRead,
    summary="Update the active organization",
)
async def update_current_organization(
    data: OrganizationUpdate,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ORG_UPDATE))],
) -> OrganizationRead:
    organization = await service.update(organization_id, data, user, ctx)
    return OrganizationRead.model_validate(organization)


@router.delete(
    "/current",
    response_model=MessageResponse,
    summary="Delete the active organization",
)
async def delete_current_organization(
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ORG_DELETE))],
) -> MessageResponse:
    """Owner only. Soft-deletes, so the books remain recoverable."""
    await service.delete(organization_id, user, ctx)
    return MessageResponse(
        message="Organization deleted.",
        # Not "contact support" - this is self-hosted, so the operator *is* support.
        # The rows survive: recovery is clearing `organization.deleted_at` in the
        # database, and nothing in the application can do it.
        detail=(
            "Nothing in the app can undo this. The database rows remain, so recovery "
            "means clearing `organization.deleted_at` in the database."
        ),
    )


@router.post("/current/leave", response_model=MessageResponse, summary="Leave the organization")
async def leave_organization(
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    ctx: RequestCtx,
) -> MessageResponse:
    """No permission required - anyone may leave, except the owner."""
    await service.leave(organization_id, user, ctx)
    return MessageResponse(message="You have left the organization.")


# =============================================================================
# Members
# =============================================================================
@router.get(
    "/current/members",
    response_model=list[MemberRead],
    summary="List members",
)
async def list_members(
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    _: Annotated[None, Depends(require_permission(Permission.MEMBER_READ))],
) -> list[MemberRead]:
    members = await service.list_members(organization_id)
    return [MemberRead.model_validate(member) for member in members]


@router.patch(
    "/current/members/{member_id}",
    response_model=MemberRead,
    summary="Update a member's role or job title",
)
async def update_member(
    member_id: uuid.UUID,
    data: MemberUpdate,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.MEMBER_UPDATE))],
) -> MemberRead:
    """A role change takes effect immediately - the member's tokens are re-minted."""
    member = await service.update_member(organization_id, member_id, data, user, ctx)
    return MemberRead.model_validate(member)


@router.post(
    "/current/members/{member_id}/suspend",
    response_model=MemberRead,
    summary="Suspend a member",
)
async def suspend_member(
    member_id: uuid.UUID,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.MEMBER_UPDATE))],
) -> MemberRead:
    """Revokes access while preserving history. Reversible."""
    member = await service.suspend_member(organization_id, member_id, user, ctx)
    return MemberRead.model_validate(member)


@router.post(
    "/current/members/{member_id}/reactivate",
    response_model=MemberRead,
    summary="Reactivate a suspended member",
)
async def reactivate_member(
    member_id: uuid.UUID,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.MEMBER_UPDATE))],
) -> MemberRead:
    member = await service.reactivate_member(organization_id, member_id, user, ctx)
    return MemberRead.model_validate(member)


@router.delete(
    "/current/members/{member_id}",
    response_model=MessageResponse,
    summary="Remove a member",
)
async def remove_member(
    member_id: uuid.UUID,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.MEMBER_REMOVE))],
) -> MessageResponse:
    """Removes the membership. The user's account is untouched."""
    await service.remove_member(organization_id, member_id, user, ctx)
    return MessageResponse(message="Member removed.")


# =============================================================================
# Invitations
# =============================================================================
@router.get(
    "/current/invitations",
    response_model=list[InvitationRead],
    summary="List invitations",
)
async def list_invitations(
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    _: Annotated[None, Depends(require_permission(Permission.MEMBER_READ))],
) -> list[InvitationRead]:
    invitations = await service.list_invitations(organization_id)
    return [InvitationRead.model_validate(invitation) for invitation in invitations]


@router.post(
    "/current/invitations",
    response_model=InvitationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Invite someone to the organization",
)
async def create_invitation(
    data: InvitationCreate,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.MEMBER_INVITE))],
) -> InvitationRead:
    """Sends an email with a single-use link. Defaults to the Viewer role."""
    invitation, _token = await service.invite(organization_id, data, user, ctx)
    return InvitationRead.model_validate(invitation)


@router.post(
    "/current/invitations/{invitation_id}/resend",
    response_model=InvitationRead,
    summary="Resend an invitation",
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.MEMBER_INVITE))],
) -> InvitationRead:
    """Issues a fresh token and resets the expiry."""
    invitation = await service.resend_invitation(organization_id, invitation_id, user, ctx)
    return InvitationRead.model_validate(invitation)


@router.delete(
    "/current/invitations/{invitation_id}",
    response_model=MessageResponse,
    summary="Revoke an invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: OrgServiceDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.MEMBER_INVITE))],
) -> MessageResponse:
    await service.revoke_invitation(organization_id, invitation_id, user, ctx)
    return MessageResponse(message="Invitation revoked.")


# =============================================================================
# Invitation acceptance (recipient-facing)
# =============================================================================
invitations_router = APIRouter(prefix="/invitations", tags=["Invitations"])


@invitations_router.get(
    "/{token}",
    response_model=InvitationPreview,
    summary="Preview an invitation",
)
async def preview_invitation(token: str, service: OrgServiceDep) -> InvitationPreview:
    """Unauthenticated - the recipient has not signed in yet.

    Returns only the organization name, role, and inviter. Anyone holding the link
    can read this, so it exposes nothing about the organization's members or data.
    """
    invitation, requires_registration = await service.preview_invitation(token)
    return InvitationPreview(
        organization_name=invitation.organization.name,
        organization_logo_url=invitation.organization.logo_url,
        role_name=invitation.role.name,
        invited_by_name=invitation.invited_by.full_name if invitation.invited_by else None,
        email=invitation.email,
        expires_at=invitation.expires_at,
        requires_registration=requires_registration,
    )


@invitations_router.post(
    "/accept",
    response_model=MessageResponse,
    summary="Accept an invitation",
)
async def accept_invitation(
    data: AcceptInvitationRequest,
    user: CurrentUser,
    service: OrgServiceDep,
    ctx: RequestCtx,
) -> MessageResponse:
    """For a user who already has an account.

    New users accept during registration instead, by passing
    ``invitation_token`` to ``/auth/register``.
    """
    member = await service.accept_invitation(data.token, user, ctx)
    return MessageResponse(
        message="Invitation accepted.",
        detail=f"You have joined as {member.role.name}.",
    )
