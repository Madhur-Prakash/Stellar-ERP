"""The user account.

A user is a *global* identity, not an organization-scoped one. They authenticate
once and switch between the organizations they belong to - see
:class:`app.modules.organizations.models.OrganizationMember`.

The table is named ``app_user`` because ``user`` is a reserved word in
PostgreSQL. SQLAlchemy would quote it correctly, but every hand-written query,
psql session, and BI tool downstream would need to remember the quotes. Not
worth the papercut.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.modules.auth.models import UserSession
    from app.modules.organizations.models import OrganizationMember


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "app_user"

    # --- Identity ---
    #: Stored lower-cased by the service layer so uniqueness is case-insensitive
    #: in practice; addresses differing only by case are the same human.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    email_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    phone: Mapped[str | None] = mapped_column(String(32))

    # --- Credentials ---
    #: Nullable on purpose: an account created by invitation or magic link is
    #: perfectly valid without a password and must not be given a fake one.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: Forces a password change on next login (admin-initiated reset).
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- Two-factor authentication (TOTP) ---
    #: Fernet-encrypted at rest - a leaked database must not yield working
    #: second factors. See :func:`app.core.security.encrypt_secret`.
    totp_secret: Mapped[str | None] = mapped_column(String(500))
    totp_enabled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: Argon2 digests of single-use recovery codes; never the codes themselves.
    recovery_code_hashes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )

    # --- Status ---
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Platform staff. Bypasses org membership; grants nothing inside an org's
    #: books by itself, so support access still has to be deliberate.
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- Preferences ---
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    theme: Mapped[str] = mapped_column(String(10), nullable=False, default="system")

    #: The organization to open on next sign-in. Convenience only - never a
    #: source of authority; permissions always come from the membership row.
    last_organization_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    # --- Activity ---
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Relationships ---
    memberships: Mapped[list[OrganizationMember]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="OrganizationMember.user_id",
    )
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_app_user_active", "is_active", postgresql_where=text("deleted_at IS NULL")),
    )

    # --- Derived state ---
    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    @property
    def is_two_factor_enabled(self) -> bool:
        """True only when a secret exists *and* enrolment was confirmed.

        Both halves matter: a secret is written during setup, before the user
        proves they can generate a valid code. Treating that intermediate state
        as "enabled" would lock out anyone who abandoned enrolment midway.
        """
        return self.totp_secret is not None and self.totp_enabled_at is not None

    @property
    def can_authenticate(self) -> bool:
        return self.is_active and not self.is_deleted

    @property
    def initials(self) -> str:
        """Two-letter fallback avatar."""
        parts = [p for p in self.full_name.split() if p]
        if not parts:
            return self.email[:2].upper()
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()
