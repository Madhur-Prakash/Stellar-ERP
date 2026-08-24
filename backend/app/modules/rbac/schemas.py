"""Role and permission contracts."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from app.core.schemas import BaseSchema, ResponseSchema, ShortStr
from app.modules.rbac.permissions import is_valid_grant


def _validate_grants(grants: list[str]) -> list[str]:
    """Reject grants absent from the code catalogue.

    Validating at the boundary matters because grants are stored as opaque JSONB:
    an unrecognised slug would be accepted, saved, silently dropped during
    expansion, and leave an administrator convinced they granted access they did
    not. Failing loudly here is the only place that mistake is visible.
    """
    unknown = sorted({grant for grant in grants if not is_valid_grant(grant)})
    if unknown:
        raise ValueError(f"Unknown permissions: {', '.join(unknown)}")
    return sorted(set(grants))


class RoleCreate(BaseSchema):
    name: ShortStr
    description: Annotated[str, StringConstraints(max_length=500)] | None = None
    permissions: list[str] = Field(
        default_factory=list,
        description="Permission slugs. Wildcards allowed, e.g. 'invoice:*'.",
    )

    @field_validator("permissions")
    @classmethod
    def _check(cls, value: list[str]) -> list[str]:
        return _validate_grants(value)


class RoleUpdate(BaseSchema):
    name: ShortStr | None = None
    description: Annotated[str, StringConstraints(max_length=500)] | None = None
    permissions: list[str] | None = None
    is_default: bool | None = None

    @field_validator("permissions")
    @classmethod
    def _check(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _validate_grants(value)


class RoleRead(ResponseSchema):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    #: As stored - may contain wildcards.
    permissions: list[str]
    is_system: bool
    is_default: bool
    member_count: int = 0
    created_at: dt.datetime


class RoleDetail(RoleRead):
    """A role plus its wildcards expanded.

    Both forms are returned because they answer different questions: the stored
    grants are what an admin edits, while the expanded set is what the server
    actually enforces. Showing only the former makes ``*:*`` opaque.
    """

    #: Defaulted because ``Role`` has no such column - the value is computed and
    #: overlaid by the router, and validation of the ORM object happens first.
    effective_permissions: list[str] = Field(
        default_factory=list,
        description="Concrete permissions after wildcard expansion",
    )


class PermissionInfo(ResponseSchema):
    slug: str
    resource: str
    action: str


class PermissionGroupInfo(ResponseSchema):
    key: str
    label: str
    description: str
    permissions: list[PermissionInfo]


class PermissionCatalogue(ResponseSchema):
    """The full catalogue, for the role editor UI.

    Served from the server so the picker can never offer a permission the backend
    does not enforce, or omit one it does.
    """

    groups: list[PermissionGroupInfo]
    total: int
