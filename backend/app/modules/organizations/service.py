"""Organization, membership, and invitation business logic.

The recurring concern in this module is **not letting an organization become
unmanageable**. Three invariants are enforced, each of which corresponds to a way
a tenant could otherwise lock itself out permanently:

* the owner cannot be removed, suspended, or demoted;
* a role still assigned to members cannot be deleted;
* system roles cannot be deleted or have their slug changed.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    InvalidTokenError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.logging import get_logger
from app.core.security import generate_token, hash_token
from app.modules.accounting.service import provision_books
from app.modules.audit.models import AuditAction
from app.modules.audit.service import AuditService, diff
from app.modules.auth.token_store import token_epochs
from app.modules.notifications import email as mailer
from app.modules.organizations.models import (
    Invitation,
    InvitationStatus,
    MemberStatus,
    Organization,
    OrganizationMember,
)
from app.modules.organizations.repository import (
    InvitationRepository,
    MemberRepository,
    OrganizationRepository,
)
from app.modules.organizations.schemas import (
    InvitationCreate,
    MemberUpdate,
    OrganizationCreate,
    OrganizationUpdate,
)
from app.modules.rbac.permissions import SystemRole
from app.modules.rbac.repository import RoleRepository
from app.modules.users.models import User
from app.modules.users.repository import UserRepository

log = get_logger(__name__)


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.organizations = OrganizationRepository(session)
        self.members = MemberRepository(session)
        self.invitations = InvitationRepository(session)
        self.roles = RoleRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditService(session)

    # =========================================================================
    # Organizations
    # =========================================================================
    async def create(
        self, data: OrganizationCreate, owner: User, ctx: RequestContext
    ) -> Organization:
        """Create an organization with the caller as owner.

        Seeding roles and creating the owner membership happen in the same
        transaction as the insert. A half-created organization - existing but with
        no roles and no owner - would be permanently unusable and invisible to its
        creator.
        """
        if data.slug:
            if await self.organizations.slug_taken(data.slug):
                raise ConflictError(
                    "That URL is already taken", code="slug_taken", details={"field": "slug"}
                )
            slug = data.slug
        else:
            slug = await self.organizations.generate_unique_slug(data.name)

        organization = Organization(
            name=data.name,
            slug=slug,
            legal_name=data.legal_name,
            country=data.country,
            currency=data.currency,
            timezone=data.timezone,
            fiscal_year_start_month=data.fiscal_year_start_month,
        )
        await self.organizations.add(organization)

        seeded = await self.roles.seed_system_roles(organization.id)
        await self.members.add(
            OrganizationMember(
                organization_id=organization.id,
                user_id=owner.id,
                role_id=seeded[SystemRole.OWNER].id,
                is_owner=True,
                status=MemberStatus.ACTIVE,
                joined_at=dt.datetime.now(dt.UTC),
            )
        )

        # A new organization gets working books immediately: the default chart, the
        # standard journals, and the current fiscal year. Shared with the registration
        # path, which is where this was once missing entirely.
        await provision_books(
            self.session,
            organization.id,
            fiscal_year_start_month=organization.fiscal_year_start_month,
        )

        owner.last_organization_id = organization.id
        await self.session.flush()

        await self.audit.record(
            AuditAction.ORG_CREATED,
            actor=owner,
            organization_id=organization.id,
            resource_type="organization",
            resource_id=organization.id,
            summary=f"Created organization {organization.name}",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        log.info(
            "organization created",
            extra={"organization_id": str(organization.id), "slug": organization.slug},
        )
        return organization

    async def get(self, organization_id: uuid.UUID) -> Organization:
        organization = await self.organizations.get(organization_id)
        if organization is None:
            raise NotFoundError("Organization")
        return organization

    async def update(
        self,
        organization_id: uuid.UUID,
        data: OrganizationUpdate,
        actor: User,
        ctx: RequestContext,
    ) -> Organization:
        organization = await self.get(organization_id)

        changes = data.model_dump(exclude_unset=True)
        if not changes:
            return organization

        before = {field: getattr(organization, field) for field in changes}
        await self.organizations.update(organization, **changes)

        await self.audit.record(
            AuditAction.ORG_UPDATED,
            actor=actor,
            organization_id=organization.id,
            resource_type="organization",
            resource_id=organization.id,
            summary=f"Updated {organization.name}",
            changes=diff(before, changes),
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return organization

    async def delete(self, organization_id: uuid.UUID, actor: User, ctx: RequestContext) -> None:
        """Soft-delete an organization. Owner only.

        Soft, not hard: statutory retention means a company's books must remain
        recoverable, and an accidental deletion of the entire ledger has to be
        reversible.
        """
        organization = await self.get(organization_id)

        membership = await self.members.get_membership(organization_id, actor.id)
        if membership is None or not membership.is_owner:
            raise PermissionDeniedError(message="Only the organization owner can delete it")

        await self.organizations.soft_delete(organization)

        await self.audit.record(
            AuditAction.ORG_DELETED,
            actor=actor,
            organization_id=organization.id,
            resource_type="organization",
            resource_id=organization.id,
            summary=f"Deleted organization {organization.name}",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        log.warning("organization deleted", extra={"organization_id": str(organization.id)})

    async def list_for_user(self, user: User) -> list[dict[str, object]]:
        """Organizations the user belongs to, for the switcher."""
        memberships = await self.users.active_memberships(user.id)

        items: list[dict[str, object]] = []
        for membership in memberships:
            items.append(
                {
                    "id": membership.organization.id,
                    "name": membership.organization.name,
                    "slug": membership.organization.slug,
                    "logo_url": membership.organization.logo_url,
                    "plan": membership.organization.plan,
                    "role_name": membership.role.name,
                    "is_owner": membership.is_owner,
                    "member_count": await self.members.count_active(membership.organization_id),
                }
            )
        return items

    # =========================================================================
    # Members
    # =========================================================================
    async def list_members(self, organization_id: uuid.UUID) -> list[OrganizationMember]:
        return list(await self.members.list_for_organization(organization_id))

    async def _get_member_scoped(
        self, organization_id: uuid.UUID, member_id: uuid.UUID
    ) -> OrganizationMember:
        """Fetch a member, asserting they belong to this organization.

        The tenant check is what stops an admin of org A from manipulating a
        member of org B by passing a guessed id.
        """
        member = await self.members.get_with_relations(member_id)
        if member is None or member.organization_id != organization_id:
            raise NotFoundError("Member")
        return member

    async def update_member(
        self,
        organization_id: uuid.UUID,
        member_id: uuid.UUID,
        data: MemberUpdate,
        actor: User,
        ctx: RequestContext,
    ) -> OrganizationMember:
        """Change a member's role or job title."""
        member = await self._get_member_scoped(organization_id, member_id)

        if data.role_id is not None and data.role_id != member.role_id:
            if member.is_owner:
                raise BusinessRuleError(
                    "The owner's role cannot be changed. Transfer ownership first.",
                    code="cannot_change_owner_role",
                )

            role = await self.roles.get_scoped(organization_id, data.role_id)
            if role is None:
                raise NotFoundError("Role")

            previous_role_name = member.role.name
            member.role_id = role.id
            await self.session.flush()

            # Permissions are embedded in access tokens, so the change would not
            # take effect until they expired. Bumping the epoch forces every one
            # of this member's tokens to be re-minted with the new role.
            await token_epochs.bump(member.user_id)

            await self.audit.record(
                AuditAction.MEMBER_ROLE_CHANGED,
                actor=actor,
                organization_id=organization_id,
                resource_type="member",
                resource_id=member.id,
                summary=f"Changed {member.user.email} from {previous_role_name} to {role.name}",
                changes={"role": {"before": previous_role_name, "after": role.name}},
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
            )

        if data.job_title is not None:
            member.job_title = data.job_title
            await self.session.flush()

        return member

    async def suspend_member(
        self,
        organization_id: uuid.UUID,
        member_id: uuid.UUID,
        actor: User,
        ctx: RequestContext,
    ) -> OrganizationMember:
        """Revoke a member's access without deleting their history.

        Preferred over removal: the audit trail keeps referring to them, and
        reinstating is one click.
        """
        member = await self._get_member_scoped(organization_id, member_id)

        if member.is_owner:
            raise BusinessRuleError(
                "The organization owner cannot be suspended", code="cannot_suspend_owner"
            )
        if member.user_id == actor.id:
            raise BusinessRuleError("You cannot suspend yourself", code="cannot_suspend_self")

        member.status = MemberStatus.SUSPENDED
        await self.session.flush()

        # Cut off access immediately rather than at token expiry.
        await token_epochs.bump(member.user_id)

        await self.audit.record(
            AuditAction.MEMBER_SUSPENDED,
            actor=actor,
            organization_id=organization_id,
            resource_type="member",
            resource_id=member.id,
            summary=f"Suspended {member.user.email}",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return member

    async def reactivate_member(
        self,
        organization_id: uuid.UUID,
        member_id: uuid.UUID,
        actor: User,
        ctx: RequestContext,
    ) -> OrganizationMember:
        member = await self._get_member_scoped(organization_id, member_id)

        member.status = MemberStatus.ACTIVE
        await self.session.flush()
        await token_epochs.bump(member.user_id)

        await self.audit.record(
            AuditAction.MEMBER_REACTIVATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="member",
            resource_id=member.id,
            summary=f"Reactivated {member.user.email}",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return member

    async def remove_member(
        self,
        organization_id: uuid.UUID,
        member_id: uuid.UUID,
        actor: User,
        ctx: RequestContext,
    ) -> None:
        """Remove a member from the organization.

        The membership row is hard-deleted - it is a join record, not a business
        document, and the audit trail preserves the fact that they were here. The
        user account itself is untouched.
        """
        member = await self._get_member_scoped(organization_id, member_id)

        if member.is_owner:
            raise BusinessRuleError(
                "The organization owner cannot be removed. Transfer ownership first.",
                code="cannot_remove_owner",
            )
        if member.user_id == actor.id:
            raise BusinessRuleError(
                "You cannot remove yourself. Ask another admin, or leave the organization.",
                code="cannot_remove_self",
            )

        email = member.user.email
        removed_user_id = member.user_id

        await self.audit.record(
            AuditAction.MEMBER_REMOVED,
            actor=actor,
            organization_id=organization_id,
            resource_type="member",
            resource_id=member.id,
            summary=f"Removed {email} from the organization",
            context={"removed_user_id": str(removed_user_id)},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

        await self.members.hard_delete(member)
        await token_epochs.bump(removed_user_id)

        log.info(
            "member removed",
            extra={
                "organization_id": str(organization_id),
                "removed_user_id": str(removed_user_id),
            },
        )

    async def leave(self, organization_id: uuid.UUID, user: User, ctx: RequestContext) -> None:
        """Voluntarily leave an organization."""
        membership = await self.members.get_membership(organization_id, user.id)
        if membership is None:
            raise NotFoundError("Membership")
        if membership.is_owner:
            raise BusinessRuleError(
                "The owner cannot leave. Transfer ownership or delete the organization.",
                code="owner_cannot_leave",
            )

        await self.audit.record(
            AuditAction.MEMBER_REMOVED,
            actor=user,
            organization_id=organization_id,
            resource_type="member",
            resource_id=membership.id,
            summary=f"{user.email} left the organization",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

        await self.members.hard_delete(membership)

        # Clear the stale pointer so the next sign-in does not try to resume here.
        if user.last_organization_id == organization_id:
            user.last_organization_id = None

        await self.session.flush()
        await token_epochs.bump(user.id)

    # =========================================================================
    # Invitations
    # =========================================================================
    async def invite(
        self,
        organization_id: uuid.UUID,
        data: InvitationCreate,
        actor: User,
        ctx: RequestContext,
    ) -> tuple[Invitation, str]:
        """Invite an address to join. Returns ``(invitation, plaintext_token)``.

        Only the token's digest is stored, so a leaked database cannot be used to
        join organizations.
        """
        email = data.email.strip().lower()
        organization = await self.get(organization_id)

        existing_user = await self.users.get_by_email(email)
        if existing_user is not None:
            membership = await self.members.get_membership(organization_id, existing_user.id)
            if membership is not None:
                raise ConflictError(
                    "That person is already a member of this organization",
                    code="already_member",
                )

        if await self.invitations.get_pending(organization_id, email) is not None:
            raise ConflictError(
                "An invitation is already pending for that address",
                code="invitation_pending",
                details={"hint": "Revoke the existing invitation to send a new one"},
            )

        if data.role_id is not None:
            role = await self.roles.get_scoped(organization_id, data.role_id)
            if role is None:
                raise NotFoundError("Role")
        else:
            role = await self.roles.get_default(organization_id)
            if role is None:  # pragma: no cover - seeding guarantees a default
                raise BusinessRuleError("This organization has no default role")

        token = generate_token()
        invitation = await self.invitations.add(
            Invitation(
                organization_id=organization_id,
                email=email,
                # Assign the relationship, not just the foreign key: the response
                # schema reads ``invitation.role``, and with only ``role_id`` set
                # that read triggers a lazy load outside the async greenlet
                # context and raises MissingGreenlet.
                role=role,
                token_hash=hash_token(token),
                message=data.message,
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=settings.invite_ttl_days),
                invited_by_id=actor.id,
            )
        )

        await mailer.send_invitation_email(
            to=email,
            organization_name=organization.name,
            inviter_name=actor.full_name,
            role_name=role.name,
            token=token,
            message=data.message,
        )

        await self.audit.record(
            AuditAction.MEMBER_INVITED,
            actor=actor,
            organization_id=organization_id,
            resource_type="invitation",
            resource_id=invitation.id,
            summary=f"Invited {email} as {role.name}",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        log.info(
            "invitation sent",
            extra={"organization_id": str(organization_id), "role": role.slug},
        )
        return invitation, token

    async def list_invitations(self, organization_id: uuid.UUID) -> list[Invitation]:
        # Refresh stale rows so the admin's list does not show expired
        # invitations still labelled "pending".
        await self.invitations.expire_stale(organization_id)
        return list(await self.invitations.list_for_organization(organization_id))

    async def revoke_invitation(
        self,
        organization_id: uuid.UUID,
        invitation_id: uuid.UUID,
        actor: User,
        ctx: RequestContext,
    ) -> None:
        invitation = await self.invitations.get(invitation_id)
        if invitation is None or invitation.organization_id != organization_id:
            raise NotFoundError("Invitation")
        if invitation.status is not InvitationStatus.PENDING:
            raise ConflictError("That invitation is no longer pending")

        invitation.status = InvitationStatus.REVOKED
        invitation.revoked_at = dt.datetime.now(dt.UTC)
        # Rewrite the digest so the emailed link cannot be redeemed. Leaving it
        # intact would rely solely on the status check.
        invitation.token_hash = hash_token(generate_token())
        await self.session.flush()

        await self.audit.record(
            AuditAction.MEMBER_INVITE_REVOKED,
            actor=actor,
            organization_id=organization_id,
            resource_type="invitation",
            resource_id=invitation.id,
            summary=f"Revoked the invitation for {invitation.email}",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

    async def resend_invitation(
        self,
        organization_id: uuid.UUID,
        invitation_id: uuid.UUID,
        actor: User,
        ctx: RequestContext,
    ) -> Invitation:
        """Re-send an invitation with a fresh token and a reset expiry.

        A new token is issued rather than resending the old one, because the
        plaintext was never retained - only its digest.
        """
        invitation = await self.invitations.get(invitation_id)
        if invitation is None or invitation.organization_id != organization_id:
            raise NotFoundError("Invitation")
        if invitation.status not in (InvitationStatus.PENDING, InvitationStatus.EXPIRED):
            raise ConflictError("That invitation has already been accepted or revoked")

        organization = await self.get(organization_id)
        role = await self.roles.get_scoped(organization_id, invitation.role_id)
        if role is None:  # pragma: no cover - FK is RESTRICT
            raise NotFoundError("Role")

        token = generate_token()
        invitation.token_hash = hash_token(token)
        # Populate the relationship for the response schema (see `invite`).
        invitation.role = role
        invitation.status = InvitationStatus.PENDING
        invitation.expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(
            days=settings.invite_ttl_days
        )
        await self.session.flush()

        await mailer.send_invitation_email(
            to=invitation.email,
            organization_name=organization.name,
            inviter_name=actor.full_name,
            role_name=role.name,
            token=token,
            message=invitation.message,
        )

        await self.audit.record(
            AuditAction.MEMBER_INVITE_RESENT,
            actor=actor,
            organization_id=organization_id,
            resource_type="invitation",
            resource_id=invitation.id,
            summary=f"Resent the invitation to {invitation.email}",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return invitation

    async def preview_invitation(self, token: str) -> tuple[Invitation, bool]:
        """Resolve an invitation for the acceptance page.

        Returns ``(invitation, requires_registration)`` so the UI can route to
        sign-up or sign-in without a second round trip.
        """
        invitation = await self.invitations.get_by_token(token)
        if invitation is None:
            raise InvalidTokenError("This invitation link is not valid")
        if not invitation.is_redeemable:
            raise InvalidTokenError("This invitation has expired or already been used")

        existing_user = await self.users.get_by_email(invitation.email)
        return invitation, existing_user is None

    async def accept_invitation(
        self, token: str, user: User, ctx: RequestContext
    ) -> OrganizationMember:
        """Accept an invitation as an already-signed-in user."""
        invitation = await self.invitations.get_by_token(token)
        if invitation is None:
            raise InvalidTokenError("This invitation link is not valid")
        if not invitation.is_redeemable:
            raise InvalidTokenError("This invitation has expired or already been used")

        # The invited address must be the accepting user's own, or a forwarded
        # link would grant a stranger access.
        if invitation.email.strip().lower() != user.email.strip().lower():
            raise PermissionDeniedError(
                message="This invitation was sent to a different email address"
            )

        existing = await self.members.get_membership(invitation.organization_id, user.id)
        if existing is not None:
            invitation.status = InvitationStatus.ACCEPTED
            invitation.accepted_at = dt.datetime.now(dt.UTC)
            invitation.accepted_by_id = user.id
            await self.session.flush()
            return existing

        member = await self.members.add(
            OrganizationMember(
                organization_id=invitation.organization_id,
                user_id=user.id,
                # `get_by_token` eager-loads the invitation's role, so passing the
                # object populates the member's relationship with no extra IO -
                # and lets the caller read `member.role.name` safely.
                role=invitation.role,
                status=MemberStatus.ACTIVE,
                joined_at=dt.datetime.now(dt.UTC),
                invited_by_id=invitation.invited_by_id,
            )
        )

        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = dt.datetime.now(dt.UTC)
        invitation.accepted_by_id = user.id

        user.last_organization_id = invitation.organization_id
        await self.session.flush()

        # New membership means new permissions; re-mint their tokens.
        await token_epochs.bump(user.id)

        await self.audit.record(
            AuditAction.MEMBER_JOINED,
            actor=user,
            organization_id=invitation.organization_id,
            resource_type="member",
            resource_id=member.id,
            summary=f"{user.email} accepted the invitation",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return member
