"""Reviewer corrections to machine-read document fields.

Two changes, and they are one feature: a reviewer can now type over what OCR read.

`document.corrected_fields` records *which* fields a human set. It is not derivable from
anything else on the row - a corrected field carries a confidence of 1, which is
indistinguishable from an engine that was certain, and "did this figure come from OCR or
from a person?" is exactly what an audit asks about a bill whose total is disputed.

`document.corrected` joins the `audit_log` action CHECK. The constraint is a VARCHAR plus
a CHECK listing every permitted value (see `app.db.types.enum_column` for why that beats a
native PostgreSQL ENUM), and the trade-off it names is that adding a value takes a
migration. Skipping it is not a cosmetic omission: the audit row is written inside the
same transaction as the correction, so the CHECK violation would roll back the edit and
surface as a 409 on a feature that is otherwise working perfectly.

**Additive and safe to apply before deploying the code.** The column has a server default,
so existing rows get `[]` without a rewrite, and the widened CHECK accepts everything it
accepted before.

Revision ID: c4d18a6f2b30
Revises: 83f89950b919
Created: 2026-08-06 01:50:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d18a6f2b30"
down_revision: str | None = "83f89950b919"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_audit_log_ck_audit_log_auditaction"

#: Every ``AuditAction`` value as of this migration - the 95 from `b7c4e19d2a83` plus
#: `document.corrected`.
#:
#: Written out literally rather than imported from the enum, for the reason that migration
#: gives: a migration has to produce the same SQL forever, and reading the enum at run time
#: would make this file's meaning change every time someone adds an action.
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
    "document.corrected",
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

    Dropped and recreated rather than altered: PostgreSQL has no ``ALTER CONSTRAINT`` for
    a CHECK's expression.
    """
    op.execute(f"ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    allowed = ", ".join(f"'{value}'" for value in values)
    op.execute(f"ALTER TABLE audit_log ADD CONSTRAINT {CONSTRAINT} CHECK (action IN ({allowed}))")


def upgrade() -> None:
    op.add_column(
        "document",
        sa.Column(
            "corrected_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    _rebuild(ACTIONS)


def downgrade() -> None:
    """Drop the column, and drop the constraint rather than narrowing it back.

    Narrowing would mean deleting every ``document.corrected`` row from the audit log, and
    a downgrade has no business destroying audit history - that is the one table whose
    whole purpose is to be the record of what happened. Dropping it leaves the column
    unconstrained until this migration is re-applied: a schema that accepts more than it
    should for a while, which is strictly better than one that has silently lost rows.
    """
    op.execute(f"ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.drop_column("document", "corrected_fields")
