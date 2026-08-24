"""Session persistence - refresh tokens and device history in one table.

One row per sign-in. It serves three purposes at once:

* **Refresh token storage.** Only the SHA-256 digest is kept, so a database leak
  yields no usable credential.
* **Session management.** "Sign out everywhere" is an ``UPDATE`` over a user's
  rows; access tokens are additionally cut off via the Redis epoch counter.
* **Device history.** IP, user agent, and a coarse device label per session,
  which is what a security-conscious user wants to audit.

Modelling these separately would mean three tables written on every login with
identical lifetimes - the same fact recorded three times.
"""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.modules.users.models import User


class LoginMethod(StrEnum):
    """How the session was established. Surfaced in device history and audit."""

    PASSWORD = "password"
    MAGIC_LINK = "magic_link"
    OTP = "otp"
    INVITATION = "invitation"
    IMPERSONATION = "impersonation"


class SessionRevocationReason(StrEnum):
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    PASSWORD_CHANGED = "password_changed"
    ROTATED = "rotated"
    REUSE_DETECTED = "reuse_detected"
    ADMIN_REVOKED = "admin_revoked"
    ACCOUNT_DISABLED = "account_disabled"


class UserSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # --- Refresh credential ---
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    #: Set when this session's token is rotated, pointing at the replacement.
    #: Lets reuse detection distinguish "stolen token replayed" from "client
    #: retried after a dropped response" - the latter is benign.
    rotated_to_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_session.id", ondelete="SET NULL")
    )
    #: How many times this lineage has refreshed. Purely diagnostic.
    generation: Mapped[int] = mapped_column(nullable=False, default=0)

    # --- Active organization ---
    #: Which org this session is currently operating in. Switching orgs updates
    #: this and re-mints the access token with that org's permissions.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )

    # --- Device / origin ---
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    #: Human-readable summary, e.g. "Chrome on Windows".
    device_label: Mapped[str | None] = mapped_column(String(120))
    device_type: Mapped[str | None] = mapped_column(String(20))

    login_method: Mapped[LoginMethod] = mapped_column(
        enum_column(LoginMethod, length=20),
        nullable=False,
        default=LoginMethod.PASSWORD,
    )

    # --- Lifecycle ---
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[SessionRevocationReason | None] = mapped_column(
        enum_column(SessionRevocationReason, length=30)
    )

    # --- Relationships ---
    user: Mapped[User] = relationship(back_populates="sessions")

    __table_args__ = (
        # Drives "list my active sessions" and bulk revocation.
        Index(
            "ix_user_session_active",
            "user_id",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    # --- Derived state ---
    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: dt.datetime | None = None) -> bool:
        return (now or dt.datetime.now(dt.UTC)) >= self.expires_at

    def is_valid(self, now: dt.datetime | None = None) -> bool:
        return not self.is_revoked and not self.is_expired(now)
