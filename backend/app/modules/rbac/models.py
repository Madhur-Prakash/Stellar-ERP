"""The role table.

Roles are data; permissions are code (see :mod:`app.modules.rbac.permissions`).
A role holds a JSONB array of grant slugs - concrete (``invoice:read``) or
wildcard (``invoice:*``, ``*:*``).

Why JSONB rather than a ``role_permission`` join table:

* A role's grants are always read as a whole set, never queried individually, so
  the join buys nothing and costs a query per authorization check.
* The permission catalogue lives in code, so the join table's foreign key would
  point at a table that only exists to mirror an enum.
* Editing a role becomes one atomic column update instead of a diff of N rows.

If a later stage needs "which roles grant X", JSONB containment
(``permissions @> '["invoice:read"]'``) is indexable with GIN.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.rbac.permissions import expand_grants

if TYPE_CHECKING:
    from app.modules.organizations.models import Organization, OrganizationMember


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A named bundle of permission grants, scoped to one organization.

    Every organization gets its own copy of the seeded roles rather than sharing
    global rows. That costs five extra rows per tenant and buys the ability to
    edit "Accountant" for one client without touching anyone else's.
    """

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    #: Grant slugs. Wildcards allowed; expanded on read via :attr:`permission_set`.
    permissions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )

    #: Seeded roles are protected from deletion and slug changes so an
    #: organization cannot destroy its own baseline access model.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Assigned to members joining by invitation when none is specified.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- Relationships ---
    organization: Mapped[Organization] = relationship(back_populates="roles")
    members: Mapped[list[OrganizationMember]] = relationship(back_populates="role")

    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_role_org_slug"),
        # At most one default role per organization.
        Index(
            "uq_role_single_default",
            "organization_id",
            unique=True,
            postgresql_where=text("is_default IS TRUE"),
        ),
    )

    @property
    def permission_set(self) -> frozenset[str]:
        """Wildcards resolved to the concrete permissions they imply."""
        return expand_grants(self.permissions)

    @property
    def is_full_access(self) -> bool:
        return "*:*" in self.permissions
