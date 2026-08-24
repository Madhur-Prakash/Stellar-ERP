"""User profile contracts."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from app.core.schemas import BaseSchema, NameStr, ResponseSchema


class UserRead(ResponseSchema):
    """A user as seen by themselves or by an org administrator."""

    id: uuid.UUID
    email: str
    full_name: str
    avatar_url: str | None = None
    initials: str
    phone: str | None = None
    is_active: bool
    is_email_verified: bool
    is_two_factor_enabled: bool
    locale: str
    timezone: str
    theme: str
    last_login_at: dt.datetime | None = None
    created_at: dt.datetime


class UserUpdate(BaseSchema):
    """Self-service profile edit.

    Every field a user is allowed to change, and nothing else. ``is_active``,
    ``is_superuser``, ``email``, and ``email_verified_at`` are deliberately absent
    - a permissive update schema is how privilege escalation happens. Email
    changes need a re-verification flow of their own, which lands with the
    account-settings work in Stage 9.
    """

    full_name: NameStr | None = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)] | None = None
    avatar_url: Annotated[str, StringConstraints(max_length=500)] | None = None
    locale: Annotated[str, StringConstraints(max_length=10)] | None = None
    timezone: Annotated[str, StringConstraints(max_length=64)] | None = None
    theme: Literal["light", "dark", "system"] | None = None


class UserPreferencesUpdate(BaseSchema):
    """Lightweight endpoint for the theme switcher and locale picker.

    Separate from :class:`UserUpdate` so toggling dark mode is a tiny request that
    cannot accidentally clear a profile field by omission.
    """

    theme: Literal["light", "dark", "system"] | None = None
    locale: Annotated[str, StringConstraints(max_length=10)] | None = None
    timezone: Annotated[str, StringConstraints(max_length=64)] | None = None


class MemberUserSummary(ResponseSchema):
    """The user fields shown in an organization's member list."""

    id: uuid.UUID
    email: str
    full_name: str
    avatar_url: str | None = None
    initials: str
    is_email_verified: bool
    last_login_at: dt.datetime | None = None


class UserStats(ResponseSchema):
    """Counters for the profile/security page."""

    active_sessions: int
    organizations: int
    recovery_codes_remaining: int = Field(
        description="Unused 2FA recovery codes; 0 when 2FA is off"
    )
