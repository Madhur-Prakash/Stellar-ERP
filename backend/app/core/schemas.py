"""Shared Pydantic base classes for API contracts.

Naming: the API speaks ``snake_case`` end to end. Auto-aliasing to ``camelCase``
is the usual reflex for a TypeScript client, but it means every field exists
under two names - one in the database and Python, another in the JSON and the
frontend - and every debugging session pays for the translation. One name
everywhere is worth more than matching JavaScript convention, and the generated
TS types are handed to the frontend anyway.

Schemas are split by direction. A response schema that doubles as a request
schema is how ``is_superuser`` ends up mass-assignable.
"""

from __future__ import annotations

import datetime as dt
import uuid
from ipaddress import IPv4Address, IPv6Address
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
)

# ---------------------------------------------------------------------------
# Reusable constrained field types
# ---------------------------------------------------------------------------
#: Trimmed non-empty short text.
ShortStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

#: A human name.
NameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

#: URL-safe identifier: lowercase alphanumerics and single hyphens.
SlugStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=2,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]

#: Raw password. Never validated for strength here - that is
#: :mod:`app.modules.auth.password_policy`, which produces actionable messages
#: rather than a regex mismatch.
PasswordStr = Annotated[str, StringConstraints(min_length=1, max_length=128)]

#: Email. Normalised to lowercase so uniqueness is case-insensitive.
Email = Annotated[EmailStr, StringConstraints(strip_whitespace=True, to_lower=True)]


def _stringify_ip(value: object) -> object:
    """Coerce an ``ipaddress`` object to its string form.

    PostgreSQL ``INET`` columns come back from asyncpg as
    :class:`ipaddress.IPv4Address` / :class:`~ipaddress.IPv6Address`, not ``str``.
    ``INET`` is still the right column type - it validates on write, indexes
    properly, and supports subnet containment queries later - so the conversion
    belongs here at the serialisation boundary rather than by weakening the
    column to ``VARCHAR``.
    """
    if isinstance(value, IPv4Address | IPv6Address):
        return str(value)
    return value


#: An IP address read from an ``INET`` column, rendered as a plain string.
IpAddress = Annotated[str, BeforeValidator(_stringify_ip)]


class BaseSchema(BaseModel):
    """Base for request bodies and internal DTOs.

    **``use_enum_values`` is deliberately absent here**, unlike on
    :class:`ResponseSchema`. With it enabled, a validated enum field becomes a
    plain ``str``, so ``data.method.is_cash`` raises ``AttributeError`` - the enum
    helpers that make the domain readable stop existing precisely where services
    reach for them. It caused exactly that bug in payment posting.

    Requests do not need it: SQLAlchemy accepts an enum member for an enum column,
    and FastAPI serialises enums fine on the way out (where ``ResponseSchema``
    *does* enable it, so the JSON carries stable values rather than member names).

    Keeping enums as enums through the request path means a service can rely on
    their behaviour, which is the whole reason for declaring them as enums.
    """

    model_config = ConfigDict(
        # Reject unknown fields: a silently ignored typo'd field is a bug the
        # client never learns about.
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class ResponseSchema(BaseModel):
    """Base for response bodies. Reads attributes off ORM objects."""

    model_config = ConfigDict(
        from_attributes=True,
        # Enum members serialise to their value, so JSON stays stable even if a
        # member is renamed in Python.
        use_enum_values=True,
    )


class TimestampedSchema(ResponseSchema):
    """Mixin for entities exposing their audit timestamps."""

    created_at: dt.datetime
    updated_at: dt.datetime


class IdentifiedSchema(ResponseSchema):
    id: uuid.UUID


# ---------------------------------------------------------------------------
# Generic envelopes
# ---------------------------------------------------------------------------
class MessageResponse(ResponseSchema):
    """A human-readable acknowledgement.

    Used by endpoints whose only meaningful output is "done" - and, importantly,
    by the ones that must stay deliberately vague (password reset, magic link) to
    avoid confirming whether an account exists.
    """

    message: str
    detail: str | None = None


class HealthStatus(ResponseSchema):
    status: str
    version: str
    environment: str
    checks: dict[str, Any] = Field(default_factory=dict)


def with_computed[SchemaT: BaseModel](schema: type[SchemaT], obj: Any, **computed: Any) -> SchemaT:
    """Build a response schema from an ORM object plus server-computed fields.

    Response schemas routinely need a value the ORM row cannot supply - a
    ``member_count`` from a separate aggregate, a ``customer_name`` reached through
    a relationship, an ``outstanding`` derived from two columns.

    **The computed values are merged before validation, not after.** The obvious
    implementation is ``model_validate(obj).model_copy(update=computed)``, and it is
    broken: validation runs first, so any *required* field the ORM object cannot
    supply raises ``ValidationError`` before the overlay ever happens. It works only
    when every computed field is optional, which silently stops being true the first
    time someone adds a required one.

    So the schema's declared fields are read off the object, the computed values are
    layered on top, and the merged mapping is validated once.

    Fields present in ``computed`` are never read from the object. That matters
    beyond efficiency: a relationship collection passed in explicitly must not also
    be touched here, or it would trigger a lazy load - see the ``LazyLoadDetected``
    guard in the test suite.
    """
    data: dict[str, Any] = {}
    for name in schema.model_fields:
        if name in computed:
            continue
        # Only fields the object actually has. Anything missing falls through to the
        # schema's own default, or fails validation if it is genuinely required.
        if hasattr(obj, name):
            data[name] = getattr(obj, name)

    data.update(computed)
    return schema.model_validate(data)
