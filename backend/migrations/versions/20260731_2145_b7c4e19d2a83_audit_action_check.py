"""Rebuild the audit_log action CHECK to match the AuditAction enum.

The enum column is a ``VARCHAR`` plus a ``CHECK`` listing every permitted value - see
:func:`app.db.types.enum_column` for why that is preferred to a native PostgreSQL ``ENUM``.
The trade-off it names is that adding a value needs "a one-line, fully reversible
migration". That migration was never written, so the constraint still listed the 46 actions
that existed when the audit table was first created while the enum had grown to 95.

**Every action added since then failed at the database.** Uploading a document, creating a
customer, posting an invoice, adjusting stock - each writes an audit row as part of its
transaction, so the CHECK violation rolled back the whole operation and surfaced as a 409.
The application was correct; the schema had simply been left behind.

**Why no test caught it.** The suite builds its schema with ``create_all`` from the models,
so its CHECK is always generated from the current enum and always passes. The documented
safety net for that gap is ``alembic check``, and it reports "No new upgrade operations
detected" here: autogenerate does not compare CHECK constraints. `test_schema_drift.py` now
covers it directly by migrating a scratch database and comparing.

The values are written out literally rather than imported from the enum. A migration has to
produce the same SQL forever - reading the enum at run time would make this file's meaning
change every time a new action is added, which is precisely the property migrations exist to
avoid.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7c4e19d2a83"
down_revision: str | Sequence[str] | None = "1acf72193910"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_audit_log_ck_audit_log_auditaction"

#: Every ``AuditAction`` value as of this migration.
ACTIONS: tuple[str, ...] = (
    "account.created",
    "account.deleted",
    "account.updated",
    "bill.cancelled",
    "bill.created",
    "bill.posted",
    "customer.created",
    "customer.deleted",
    "customer.updated",
    "document.confirmed",
    "document.deleted",
    "document.reextracted",
    "document.rejected",
    "document.uploaded",
    "fiscal_year.created",
    "goods_receipt.cancelled",
    "goods_receipt.created",
    "goods_receipt.posted",
    "invoice.cancelled",
    "invoice.created",
    "invoice.deleted",
    "invoice.posted",
    "invoice.updated",
    "journal_entry.created",
    "journal_entry.deleted",
    "journal_entry.posted",
    "journal_entry.reversed",
    "lead.converted",
    "lead.created",
    "lead.updated",
    "member.invite_resent",
    "member.invite_revoked",
    "member.invited",
    "member.joined",
    "member.reactivated",
    "member.removed",
    "member.role_changed",
    "member.suspended",
    "organization.created",
    "organization.deleted",
    "organization.switched",
    "organization.updated",
    "payment.allocated",
    "payment.cancelled",
    "payment.received",
    "period.closed",
    "period.reopened",
    "product.created",
    "product.deleted",
    "product.updated",
    "purchase_order.approved",
    "purchase_order.cancelled",
    "purchase_order.created",
    "quotation.accepted",
    "quotation.converted",
    "quotation.created",
    "quotation.rejected",
    "quotation.sent",
    "quotation.updated",
    "role.created",
    "role.deleted",
    "role.updated",
    "sales_order.cancelled",
    "sales_order.confirmed",
    "sales_order.created",
    "session.all_revoked",
    "session.reuse_detected",
    "session.revoked",
    "settings.updated",
    "stock.adjusted",
    "stock.transferred",
    "supplier.created",
    "supplier.deleted",
    "supplier.updated",
    "supplier_payment.allocated",
    "supplier_payment.made",
    "two_factor.challenge_failed",
    "two_factor.disabled",
    "two_factor.enabled",
    "two_factor.recovery_code_used",
    "two_factor.recovery_codes_regenerated",
    "user.email_verified",
    "user.locked_out",
    "user.logged_in",
    "user.logged_out",
    "user.login_failed",
    "user.magic_link_requested",
    "user.otp_requested",
    "user.password_changed",
    "user.password_reset_completed",
    "user.password_reset_requested",
    "user.profile_updated",
    "user.registered",
    "warehouse.created",
    "warehouse.updated",
)

def _rebuild(values: tuple[str, ...]) -> None:
    """Replace the CHECK with one listing exactly ``values``.

    Dropped and recreated rather than altered: PostgreSQL has no ``ALTER CONSTRAINT`` for a
    CHECK's expression. ``IF EXISTS`` so this is safe on a database where the constraint was
    never created under this name.
    """
    op.execute(f"ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    allowed = ", ".join(f"'{value}'" for value in values)
    op.execute(f"ALTER TABLE audit_log ADD CONSTRAINT {CONSTRAINT} CHECK (action IN ({allowed}))")


def upgrade() -> None:
    _rebuild(ACTIONS)


def downgrade() -> None:
    """Drop the constraint rather than narrowing it back.

    Narrowing would mean deleting every audit row whose action the older list did not
    contain, and a downgrade has no business destroying audit history - that is the one table
    whose whole purpose is to be the record of what happened. Dropping it leaves the column
    unconstrained until this migration is re-applied, which is a schema that accepts more
    than it should for a while: strictly better than one that has silently lost rows.
    """
    op.execute(f"ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
