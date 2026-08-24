"""The permission catalogue - the single authoritative list of what can be done.

Design decision: **permissions are defined in code, not rows in a table.**

A permission is a capability the software actually implements. If
``invoice:approve`` exists as a database row but no endpoint checks it, the row
is a lie; if an endpoint checks a permission absent from the table, authorization
silently fails. Keeping the catalogue in code makes it impossible for the two to
drift - the enum *is* the contract, it is greppable, it type-checks, and adding a
capability requires no data migration.

Roles, by contrast, *are* data: each organization composes its own roles from
these slugs, stored as a JSONB array (see :mod:`app.modules.rbac.models`).

Slug format is ``resource:action``. ``*`` is a wildcard, so ``invoice:*`` grants
every invoice action and ``*:*`` is full control.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, NamedTuple

WILDCARD: Final = "*"


class Permission(StrEnum):
    """Every capability the backend enforces.

    Stage 1 implements the platform-level entries. The commercial modules are
    declared here as the stages land so role definitions and the UI's permission
    picker never need to guess at names that do not exist yet.
    """

    # --- Organization ---
    ORG_READ = "organization:read"
    ORG_UPDATE = "organization:update"
    ORG_DELETE = "organization:delete"
    ORG_BILLING = "organization:billing"

    # --- Members ---
    MEMBER_READ = "member:read"
    MEMBER_INVITE = "member:invite"
    MEMBER_UPDATE = "member:update"
    MEMBER_REMOVE = "member:remove"

    # --- Roles ---
    ROLE_READ = "role:read"
    ROLE_CREATE = "role:create"
    ROLE_UPDATE = "role:update"
    ROLE_DELETE = "role:delete"

    # --- Audit ---
    AUDIT_READ = "audit:read"
    AUDIT_EXPORT = "audit:export"

    # --- Settings ---
    SETTINGS_READ = "settings:read"
    SETTINGS_UPDATE = "settings:update"

    # --- Accounting (Stage 2) ---
    ACCOUNT_READ = "account:read"
    ACCOUNT_WRITE = "account:write"
    JOURNAL_READ = "journal:read"
    JOURNAL_WRITE = "journal:write"
    JOURNAL_POST = "journal:post"
    #: Reversing a posted entry is separated from posting: it rewrites the
    #: effective books, so an organization may want to restrict it more tightly.
    JOURNAL_REVERSE = "journal:reverse"
    PERIOD_READ = "period:read"
    #: Closing a period freezes filed numbers. Deliberately not implied by
    #: `journal:post` - a bookkeeper posts daily but should not be able to seal a
    #: quarter.
    PERIOD_CLOSE = "period:close"
    REPORT_READ = "report:read"

    # --- Sales (Stage 3) ---
    CUSTOMER_READ = "customer:read"
    CUSTOMER_WRITE = "customer:write"
    INVOICE_READ = "invoice:read"
    INVOICE_WRITE = "invoice:write"
    INVOICE_APPROVE = "invoice:approve"
    PAYMENT_READ = "payment:read"
    PAYMENT_WRITE = "payment:write"

    # --- Purchases & inventory (Stage 4) ---
    SUPPLIER_READ = "supplier:read"
    SUPPLIER_WRITE = "supplier:write"
    PURCHASE_READ = "purchase:read"
    PURCHASE_WRITE = "purchase:write"
    PURCHASE_APPROVE = "purchase:approve"
    INVENTORY_READ = "inventory:read"
    INVENTORY_WRITE = "inventory:write"

    # --- Scanned documents ---
    DOCUMENT_READ = "document:read"
    DOCUMENT_WRITE = "document:write"
    #: Turning a scanned document into a bill. Separate from `document:write`, and
    #: the endpoint requires `purchase:write` as well: uploading and reviewing a
    #: supplier's PDF is clerical work, while accepting machine-read figures as
    #: money owed is not.
    DOCUMENT_CONFIRM = "document:confirm"

    @property
    def resource(self) -> str:
        return self.value.split(":", 1)[0]

    @property
    def action(self) -> str:
        return self.value.split(":", 1)[1]


class PermissionGroup(NamedTuple):
    """Presentation grouping for the role editor UI."""

    key: str
    label: str
    description: str
    permissions: tuple[Permission, ...]


PERMISSION_GROUPS: Final[tuple[PermissionGroup, ...]] = (
    PermissionGroup(
        "organization",
        "Organization",
        "Company profile, billing, and deletion",
        (
            Permission.ORG_READ,
            Permission.ORG_UPDATE,
            Permission.ORG_DELETE,
            Permission.ORG_BILLING,
        ),
    ),
    PermissionGroup(
        "people",
        "People & access",
        "Members, invitations, and roles",
        (
            Permission.MEMBER_READ,
            Permission.MEMBER_INVITE,
            Permission.MEMBER_UPDATE,
            Permission.MEMBER_REMOVE,
            Permission.ROLE_READ,
            Permission.ROLE_CREATE,
            Permission.ROLE_UPDATE,
            Permission.ROLE_DELETE,
        ),
    ),
    PermissionGroup(
        "governance",
        "Governance",
        "Audit trail and organization settings",
        (
            Permission.AUDIT_READ,
            Permission.AUDIT_EXPORT,
            Permission.SETTINGS_READ,
            Permission.SETTINGS_UPDATE,
        ),
    ),
    PermissionGroup(
        "accounting",
        "Accounting",
        "Chart of accounts, journals, periods, and financial reports",
        (
            Permission.ACCOUNT_READ,
            Permission.ACCOUNT_WRITE,
            Permission.JOURNAL_READ,
            Permission.JOURNAL_WRITE,
            Permission.JOURNAL_POST,
            Permission.JOURNAL_REVERSE,
            Permission.PERIOD_READ,
            Permission.PERIOD_CLOSE,
            Permission.REPORT_READ,
        ),
    ),
    PermissionGroup(
        "sales",
        "Sales",
        "Customers, invoices, and payments",
        (
            Permission.CUSTOMER_READ,
            Permission.CUSTOMER_WRITE,
            Permission.INVOICE_READ,
            Permission.INVOICE_WRITE,
            Permission.INVOICE_APPROVE,
            Permission.PAYMENT_READ,
            Permission.PAYMENT_WRITE,
        ),
    ),
    PermissionGroup(
        "purchasing",
        "Purchasing & inventory",
        "Suppliers, purchase orders, and stock",
        (
            Permission.SUPPLIER_READ,
            Permission.SUPPLIER_WRITE,
            Permission.PURCHASE_READ,
            Permission.PURCHASE_WRITE,
            Permission.PURCHASE_APPROVE,
            Permission.INVENTORY_READ,
            Permission.INVENTORY_WRITE,
        ),
    ),
    PermissionGroup(
        "documents",
        "Scanned documents",
        "Uploading supplier invoices and turning them into bills",
        (
            Permission.DOCUMENT_READ,
            Permission.DOCUMENT_WRITE,
            Permission.DOCUMENT_CONFIRM,
        ),
    ),
)


class SystemRole(StrEnum):
    """Roles seeded into every new organization.

    Seeded as ordinary rows (``is_system=True``) rather than hard-coded checks,
    so an organization can clone one and adjust it. The set is deliberately
    small: the flexibility belongs in custom roles, not in a sprawl of defaults.
    """

    OWNER = "owner"
    ADMIN = "admin"
    ACCOUNTANT = "accountant"
    SALES = "sales"
    VIEWER = "viewer"


#: Permission grants for each seeded role. ``*:*`` is the owner's full control.
SYSTEM_ROLE_PERMISSIONS: Final[dict[SystemRole, tuple[str, ...]]] = {
    SystemRole.OWNER: ("*:*",),
    SystemRole.ADMIN: (
        Permission.ORG_READ,
        Permission.ORG_UPDATE,
        "member:*",
        "role:*",
        Permission.AUDIT_READ,
        Permission.AUDIT_EXPORT,
        "settings:*",
        "account:*",
        "journal:*",
        "period:*",
        Permission.REPORT_READ,
        "customer:*",
        "invoice:*",
        "payment:*",
        "supplier:*",
        "purchase:*",
        "inventory:*",
        "document:*",
    ),
    SystemRole.ACCOUNTANT: (
        Permission.ORG_READ,
        Permission.MEMBER_READ,
        "account:*",
        "journal:*",
        "period:*",
        Permission.REPORT_READ,
        Permission.CUSTOMER_READ,
        "invoice:*",
        "payment:*",
        Permission.SUPPLIER_READ,
        Permission.PURCHASE_READ,
        Permission.INVENTORY_READ,
        "document:*",
        Permission.AUDIT_READ,
    ),
    SystemRole.SALES: (
        Permission.ORG_READ,
        Permission.MEMBER_READ,
        "customer:*",
        Permission.INVOICE_READ,
        Permission.INVOICE_WRITE,
        Permission.PAYMENT_READ,
        Permission.INVENTORY_READ,
        Permission.PERIOD_READ,
        Permission.REPORT_READ,
    ),
    SystemRole.VIEWER: (
        Permission.ORG_READ,
        Permission.MEMBER_READ,
        Permission.ACCOUNT_READ,
        Permission.JOURNAL_READ,
        Permission.PERIOD_READ,
        Permission.REPORT_READ,
        Permission.CUSTOMER_READ,
        Permission.INVOICE_READ,
        Permission.PAYMENT_READ,
        Permission.SUPPLIER_READ,
        Permission.PURCHASE_READ,
        Permission.INVENTORY_READ,
        Permission.DOCUMENT_READ,
    ),
}

SYSTEM_ROLE_DESCRIPTIONS: Final[dict[SystemRole, str]] = {
    SystemRole.OWNER: "Full control, including billing and deleting the organization",
    SystemRole.ADMIN: "Manage people, settings, and all business data",
    SystemRole.ACCOUNTANT: "Full books access - journals, invoices, payments, and reports",
    SystemRole.SALES: "Manage customers and raise invoices",
    SystemRole.VIEWER: "Read-only access across modules",
}

#: Fast membership test for validating role definitions.
ALL_PERMISSION_VALUES: Final[frozenset[str]] = frozenset(p.value for p in Permission)


def is_valid_grant(grant: str) -> bool:
    """Whether ``grant`` is a concrete permission or a well-formed wildcard.

    Accepts ``*:*``, ``resource:*``, and any exact catalogue slug. A wildcard is
    only valid if its resource actually exists, which stops typos like
    ``invoces:*`` from being stored as a permanently dead grant.
    """
    if grant == "*:*":
        return True
    if grant in ALL_PERMISSION_VALUES:
        return True
    if grant.endswith(":*"):
        resource = grant.removesuffix(":*")
        return any(p.resource == resource for p in Permission)
    return False


def expand_grants(grants: list[str] | tuple[str, ...]) -> frozenset[str]:
    """Resolve a role's grant list into the concrete permissions it implies.

    Wildcards are expanded eagerly so the resulting set can be embedded in an
    access token and checked with a plain set membership test - no pattern
    matching in the hot path of every request.
    """
    resolved: set[str] = set()

    for grant in grants:
        if grant == "*:*":
            return ALL_PERMISSION_VALUES
        if grant.endswith(":*"):
            resource = grant.removesuffix(":*")
            resolved.update(p.value for p in Permission if p.resource == resource)
        elif grant in ALL_PERMISSION_VALUES:
            resolved.add(grant)
        # Unknown grants are dropped: a permission removed from the catalogue in
        # a later release must not keep granting access.

    return frozenset(resolved)


def has_permission(granted: frozenset[str] | set[str], required: Permission | str) -> bool:
    """Check one permission against an already-expanded grant set."""
    return str(required) in granted
