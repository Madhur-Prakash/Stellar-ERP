"""Organization, membership, and invitation tables.

The organization is the tenant boundary: every business record in every later
stage hangs off ``organization_id`` (via
:class:`app.db.base.OrgScopedMixin`). Users are *global* accounts that join
organizations through :class:`OrganizationMember`, so one person can be the
accountant for three companies with a single login - the normal case for the
small-business owners and part-time accountants this product targets.

Enum columns go through :func:`app.db.types.enum_column`, never
``sqlalchemy.Enum`` directly - see that function for why the obvious spelling
silently stores the member *name* and creates no constraint at all.
"""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.modules.rbac.models import Role
    from app.modules.users.models import User


class OrganizationPlan(StrEnum):
    FREE = "free"
    STARTER = "starter"
    GROWTH = "growth"
    ENTERPRISE = "enterprise"


class MemberStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A tenant - one company's books.

    Soft-deleted rather than hard-deleted: statutory retention rules mean a
    company's ledger has to remain recoverable long after they stop paying.
    """

    # --- Identity ---
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    legal_name: Mapped[str | None] = mapped_column(String(250))

    # --- Contact ---
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(32))
    website: Mapped[str | None] = mapped_column(String(255))
    logo_url: Mapped[str | None] = mapped_column(String(500))

    # --- Address ---
    address_line1: Mapped[str | None] = mapped_column(String(255))
    address_line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")

    # --- Locale / financial conventions ---
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    #: 4 == April, the Indian fiscal year start. Reports derive periods from this.
    fiscal_year_start_month: Mapped[int] = mapped_column(nullable=False, default=4)

    # --- Statutory identifiers (India; Stage 2+ validates them properly) ---
    gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    pan: Mapped[str | None] = mapped_column(String(10))
    cin: Mapped[str | None] = mapped_column(String(21))

    # --- Lifecycle ---
    plan: Mapped[OrganizationPlan] = mapped_column(
        enum_column(OrganizationPlan, length=20),
        nullable=False,
        default=OrganizationPlan.FREE,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    onboarded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    #: Free-form module preferences. JSONB so feature flags and per-module
    #: settings can evolve without a migration per toggle.
    settings: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    # --- Relationships ---
    members: Mapped[list[OrganizationMember]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    roles: Mapped[list[Role]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    invitations: Mapped[list[Invitation]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_organization_active", "is_active", postgresql_where=text("deleted_at IS NULL")),
    )


class OrganizationMember(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Join row binding a user to an organization with exactly one role.

    One role per membership, not many: multi-role stacking makes "why can this
    person do that?" nearly unanswerable, and custom roles already cover any
    combination an org actually needs.
    """

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        # RESTRICT, not CASCADE: deleting a role that people still hold would
        # silently strip their access. Callers must reassign members first.
        ForeignKey("role.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    #: The one member who cannot be removed or demoted - every org needs a
    #: guaranteed administrator, or it can be locked out permanently.
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[MemberStatus] = mapped_column(
        enum_column(MemberStatus, length=20),
        nullable=False,
        default=MemberStatus.ACTIVE,
    )

    #: Per-member overrides on top of the role, e.g. ``{"deny": ["invoice:approve"]}``.
    permission_overrides: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    job_title: Mapped[str | None] = mapped_column(String(120))
    joined_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    last_active_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Relationships ---
    organization: Mapped[Organization] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships", foreign_keys=[user_id])
    role: Mapped[Role] = relationship(back_populates="members")
    invited_by: Mapped[User | None] = relationship(foreign_keys=[invited_by_id])

    __table_args__ = (
        # A user joins an organization at most once.
        UniqueConstraint("organization_id", "user_id", name="uq_member_org_user"),
        # Partial unique index: at most one owner per organization.
        Index(
            "uq_member_single_owner",
            "organization_id",
            unique=True,
            postgresql_where=text("is_owner IS TRUE"),
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.status is MemberStatus.ACTIVE


class Invitation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A pending offer to join an organization.

    Only the SHA-256 digest of the invite token is stored, so a database leak
    cannot be replayed into unauthorized org access.
    """

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("role.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    status: Mapped[InvitationStatus] = mapped_column(
        enum_column(InvitationStatus, length=20),
        nullable=False,
        default=InvitationStatus.PENDING,
    )
    message: Mapped[str | None] = mapped_column(Text)

    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    accepted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    # --- Relationships ---
    organization: Mapped[Organization] = relationship(back_populates="invitations")
    role: Mapped[Role] = relationship()
    invited_by: Mapped[User | None] = relationship(foreign_keys=[invited_by_id])
    accepted_by: Mapped[User | None] = relationship(foreign_keys=[accepted_by_id])

    __table_args__ = (
        # One live invitation per email per organization. Accepted/revoked rows
        # are kept for history, so the constraint only covers pending ones.
        Index(
            "uq_invitation_pending_email",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    @property
    def is_expired(self) -> bool:
        """Whether the invitation window has closed.

        A property, not a method: ``InvitationRead`` exposes this field, and
        Pydantic's ``from_attributes`` would read a method object rather than
        calling it - yielding a confusing "input should be a valid boolean"
        error at the serialisation boundary.
        """
        return dt.datetime.now(dt.UTC) >= self.expires_at

    @property
    def is_redeemable(self) -> bool:
        return self.status is InvitationStatus.PENDING and not self.is_expired
