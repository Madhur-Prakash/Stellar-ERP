"""Declarative base and the mixins every table is built from.

Two deliberate choices here shape the whole schema:

* **A naming convention on every constraint.** Without it PostgreSQL invents
  names, Alembic autogenerate cannot find them again, and later migrations
  cannot drop or alter them. This is the difference between a schema that stays
  migratable for years and one that does not.
* **UUIDv7 primary keys.** Random UUIDs scatter B-tree inserts across the
  index; UUIDv7 is time-ordered, so inserts stay append-friendly while keys
  remain non-guessable and safe to expose in URLs.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from typing import Any, ClassVar

from sqlalchemy import DateTime, ForeignKey, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Alembic needs stable, predictable constraint names to diff against.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def uuid7() -> uuid.UUID:
    """Generate a UUIDv7 (RFC 9562): 48-bit millisecond timestamp + randomness.

    Time-ordered, so primary-key inserts append to the right edge of the index
    instead of landing at random offsets. Python 3.13's stdlib has no ``uuid7``
    yet, so it is assembled here.

    Bit layout, most-significant bit first (a 128-bit big-endian integer, so a
    field starting at bit ``i`` with width ``w`` is shifted left by
    ``128 - i - w``)::

        bits   0..47   unix timestamp in milliseconds
        bits  48..51   version == 0b0111
        bits  52..63   rand_a  (12 bits)
        bits  64..65   variant == 0b10
        bits  66..127  rand_b  (62 bits)
    """
    timestamp_ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000) & 0xFFFFFFFFFFFF
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


class Base(DeclarativeBase):
    """Root of the ORM hierarchy."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Map Python UUIDs onto native ``uuid`` columns rather than char(32).
    type_annotation_map: ClassVar[dict[Any, Any]] = {uuid.UUID: PgUUID(as_uuid=True)}

    # Fetch database-generated values in the same statement that writes them, via
    # ``RETURNING``.
    #
    # Without this, a column the server computes - ``updated_at``, every
    # ``server_default`` - is left *expired* after a flush, and the next attribute read
    # emits a lazy SELECT. Under asyncio that read happens outside greenlet context and
    # raises ``MissingGreenlet``, which surfaces as a 503, so serialising an object the
    # request just updated would fail on any endpoint whose response carries a timestamp.
    # PostgreSQL supports RETURNING on both INSERT and UPDATE, so this costs no extra
    # round trip - it removes one.
    # A directive rather than a plain dict: a mutable class attribute would be shared
    # by every mapper, and annotating it ``ClassVar`` to say otherwise contradicts the
    # instance-variable declaration on ``DeclarativeBase``. This gives each subclass its
    # own, and matches how ``__tablename__`` is derived below.
    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"eager_defaults": True}

    @declared_attr.directive
    def __tablename__(cls) -> str:
        """Derive ``snake_case`` plural-ish table names from the class name.

        ``OrganizationMember`` -> ``organization_member``. Subclasses can still
        set ``__tablename__`` explicitly to override.
        """
        name = cls.__name__
        chars: list[str] = []
        for index, char in enumerate(name):
            if char.isupper() and index > 0:
                chars.append("_")
            chars.append(char.lower())
        return "".join(chars)

    def __repr__(self) -> str:
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"

    def to_dict(self, exclude: set[str] | None = None) -> dict[str, Any]:
        """Shallow column dump. For debugging and audit snapshots only -
        API responses go through Pydantic schemas.
        """
        skip = exclude or set()
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
            if column.name not in skip
        }


class UUIDPrimaryKeyMixin:
    """Time-ordered UUID primary key."""

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid7,
        # Server-side fallback so raw SQL inserts (fixtures, data migrations)
        # still get a key. Built into PostgreSQL 13+, no extension needed. Note
        # this yields a v4 - only ORM inserts get the time-ordered v7.
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    """``created_at`` / ``updated_at``, both maintained by the database.

    Server-side defaults mean the timestamps are correct even for rows written
    outside the ORM, and immune to clock skew between app instances.
    """

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SoftDeleteMixin:
    """Soft deletion via a nullable ``deleted_at``.

    Accounting records must never truly disappear - an audit trail with holes in
    it is not an audit trail. Repositories filter ``deleted_at IS NULL`` by
    default.
    """

    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class OrgScopedMixin:
    """Tenant discriminator for every organization-owned table.

    Declared as a mixin so the multi-tenancy story is one consistent column
    (and one consistent index) across all modules rather than per-table
    guesswork. ``ondelete="CASCADE"`` ties tenant data to the tenant's lifetime.
    """

    @declared_attr
    @classmethod
    def organization_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            ForeignKey("organization.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
