"""Organization, membership, and invitation contracts."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from app.core.schemas import BaseSchema, Email, ResponseSchema, ShortStr, SlugStr
from app.modules.organizations.models import (
    InvitationStatus,
    MemberStatus,
    OrganizationPlan,
)
from app.modules.users.schemas import MemberUserSummary

CountryCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=2, max_length=2)
]
CurrencyCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=3, max_length=3)
]

#: Statutory identifiers: always upper-cased and trimmed before validation, so a
#: pasted "  29abcde1234f1z5 " is accepted rather than rejected on whitespace.
_Upper = {"strip_whitespace": True, "to_upper": True}
UpperStr15 = Annotated[str, StringConstraints(**_Upper, max_length=15)]
UpperStr10 = Annotated[str, StringConstraints(**_Upper, max_length=10)]
UpperStr21 = Annotated[str, StringConstraints(**_Upper, max_length=21)]


# =============================================================================
# Organization
# =============================================================================
class OrganizationCreate(BaseSchema):
    name: ShortStr
    #: Optional - derived from the name, uniquely, when omitted.
    slug: SlugStr | None = None
    legal_name: ShortStr | None = None
    country: CountryCode = "IN"
    currency: CurrencyCode = "INR"
    timezone: Annotated[str, StringConstraints(max_length=64)] = "Asia/Kolkata"
    #: 1-12. April (4) is the Indian fiscal year start and the default.
    fiscal_year_start_month: Annotated[int, Field(ge=1, le=12)] = 4


class OrganizationUpdate(BaseSchema):
    """Editable organization fields.

    ``slug`` is absent: it appears in URLs that people bookmark and share, so
    renaming it silently breaks those links. ``plan`` is absent because billing
    changes it, not a profile form.
    """

    name: ShortStr | None = None
    legal_name: ShortStr | None = None
    email: Email | None = None
    phone: Annotated[str, StringConstraints(max_length=32)] | None = None
    website: Annotated[str, StringConstraints(max_length=255)] | None = None
    logo_url: Annotated[str, StringConstraints(max_length=500)] | None = None

    address_line1: Annotated[str, StringConstraints(max_length=255)] | None = None
    address_line2: Annotated[str, StringConstraints(max_length=255)] | None = None
    city: Annotated[str, StringConstraints(max_length=100)] | None = None
    state: Annotated[str, StringConstraints(max_length=100)] | None = None
    postal_code: Annotated[str, StringConstraints(max_length=20)] | None = None
    country: CountryCode | None = None

    currency: CurrencyCode | None = None
    timezone: Annotated[str, StringConstraints(max_length=64)] | None = None
    fiscal_year_start_month: Annotated[int, Field(ge=1, le=12)] | None = None

    gstin: UpperStr15 | None = None
    pan: UpperStr10 | None = None
    cin: UpperStr21 | None = None

    @field_validator("gstin")
    @classmethod
    def _validate_gstin(cls, value: str | None) -> str | None:
        """Check the GSTIN's structural format.

        A GSTIN is 15 characters: 2-digit state code, 10-character PAN, an entity
        number, a fixed 'Z', and a checksum. Validating the shape catches typos at
        entry; full checksum verification arrives with the GST module in Stage 2,
        where it can be surfaced properly in the filing workflow.
        """
        if value in (None, ""):
            return None
        import re

        if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", value or ""):
            raise ValueError("Not a valid GSTIN format")
        return value

    @field_validator("pan")
    @classmethod
    def _validate_pan(cls, value: str | None) -> str | None:
        """PAN is five letters, four digits, one letter."""
        if value in (None, ""):
            return None
        import re

        if not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", value or ""):
            raise ValueError("Not a valid PAN format")
        return value


class OrganizationRead(ResponseSchema):
    id: uuid.UUID
    name: str
    slug: str
    legal_name: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    logo_url: str | None = None

    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str

    currency: str
    timezone: str
    fiscal_year_start_month: int

    gstin: str | None = None
    pan: str | None = None
    cin: str | None = None

    plan: OrganizationPlan
    is_active: bool
    onboarded_at: dt.datetime | None = None
    created_at: dt.datetime


class OrganizationListItem(ResponseSchema):
    """Compact form for the organization switcher."""

    id: uuid.UUID
    name: str
    slug: str
    logo_url: str | None = None
    plan: OrganizationPlan
    role_name: str
    is_owner: bool
    member_count: int


# =============================================================================
# Members
# =============================================================================
class RoleSummary(ResponseSchema):
    id: uuid.UUID
    name: str
    slug: str
    is_system: bool


class MemberRead(ResponseSchema):
    id: uuid.UUID
    user: MemberUserSummary
    role: RoleSummary
    status: MemberStatus
    is_owner: bool
    job_title: str | None = None
    joined_at: dt.datetime | None = None
    last_active_at: dt.datetime | None = None
    created_at: dt.datetime


class MemberUpdate(BaseSchema):
    """Change a member's role or job title.

    ``status`` is not here - suspend and reactivate are separate endpoints, so the
    audit trail records the intent rather than an opaque field diff.
    """

    role_id: uuid.UUID | None = None
    job_title: Annotated[str, StringConstraints(max_length=120)] | None = None


# =============================================================================
# Invitations
# =============================================================================
class InvitationCreate(BaseSchema):
    email: Email
    #: Defaults to the organization's default role (Viewer) when omitted.
    role_id: uuid.UUID | None = None
    message: Annotated[str, StringConstraints(max_length=500)] | None = None


class InvitationRead(ResponseSchema):
    id: uuid.UUID
    email: str
    role: RoleSummary
    status: InvitationStatus
    message: str | None = None
    expires_at: dt.datetime
    accepted_at: dt.datetime | None = None
    created_at: dt.datetime
    is_expired: bool = False


class InvitationPreview(ResponseSchema):
    """Shown on the acceptance page *before* the recipient signs in.

    Deliberately minimal: anyone holding the link can see this, so it reveals the
    organization name and role but nothing about its members or data.
    """

    organization_name: str
    organization_logo_url: str | None = None
    role_name: str
    invited_by_name: str | None = None
    email: str
    expires_at: dt.datetime
    requires_registration: bool = Field(
        description="True when no account exists for this address yet"
    )


class AcceptInvitationRequest(BaseSchema):
    token: str
