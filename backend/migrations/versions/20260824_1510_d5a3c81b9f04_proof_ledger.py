"""Ledger 3 - the proof ledger.

Three tables and one widened CHECK.

``attestation_setting`` - one row per organization: whether sealing is on, the
on-chain namespace, the contract, and the signer. The signer's secret seed lives
here Fernet-encrypted, which is why it is a typed column in a table nothing else
writes rather than a key in the organization's ``settings`` JSONB.

``seal_leaf`` - one row per posted journal entry, holding the canonical hash of
that entry. Written in the same transaction as the posting.

``seal`` - one row per batch of leaves committed to the chain. **This row is also
the outbox**: a ``pending`` seal is the intent, and the worker drains it. A
separate outbox table would be a second record of the same fact, and this codebase
has been bitten by a figure stored twice.

The audit CHECK
---------------
Nine new ``AuditAction`` values join the constraint. Skipping that is not cosmetic:
the audit row is written inside the same transaction as the event, so a CHECK
violation would roll back the *seal* and surface as a 409 on a feature that is
otherwise working. Migration ``c4d18a6f2b30`` learned this the same way.

Two indexes worth reading
-------------------------
``ix_seal_leaf_unsealed`` is partial on ``seal_id IS NULL``, so it holds only the
backlog - a few hundred rows for a business in its fifth year rather than a few
hundred thousand - and the backlog is the only part the worker ever scans.

``uq_seal_org_seq_live`` is a partial *unique* index on
``(organization_id, seq) WHERE status <> 'failed'``. Partial deliberately: a seal
that fails permanently at sequence 7 must not block the replacement that has to
reuse 7, because the contract's ``head`` never moved and 7 is still the only number
it will accept.

**Additive and safe to apply before deploying the code.** Nothing existing is
altered except the widened CHECK, which accepts everything it accepted before.

Revision ID: d5a3c81b9f04
Revises: c4d18a6f2b30
Created: 2026-08-24 15:10:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a3c81b9f04"
down_revision: str | None = "c4d18a6f2b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_audit_log_ck_audit_log_auditaction"

#: The 96 values as of ``c4d18a6f2b30``, plus the nine the proof ledger adds.
#:
#: Written out literally rather than imported from the enum, for the reason the
#: previous migration gives: a migration has to produce the same SQL forever, and
#: reading the enum at run time would make this file's meaning change every time
#: somebody adds an action.
ACTIONS: tuple[str, ...] = (
    "account.created",
    "account.deleted",
    "account.updated",
    "attestation.disabled",
    "attestation.enabled",
    "attestation.registered",
    "attestation.signer_rotated",
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
    "proof.exported",
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
    "seal.confirmed",
    "seal.created",
    "seal.failed",
    "seal.reconciled",
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

#: The values before this migration - what ``downgrade`` restores.
PREVIOUS_ACTIONS: tuple[str, ...] = tuple(
    value
    for value in ACTIONS
    if not value.startswith(("attestation.", "seal.", "proof."))
)

SEAL_STATUSES = ("pending", "submitted", "confirmed", "failed")
SEAL_TRIGGERS = ("period_close", "schedule", "manual", "backfill")
SEAL_CADENCES = ("on_period_close", "daily", "manual")


def _rebuild_audit_check(values: tuple[str, ...]) -> None:
    """Replace the audit CHECK with one listing exactly ``values``.

    Dropped and recreated rather than altered: PostgreSQL has no
    ``ALTER CONSTRAINT`` for a CHECK's expression.
    """
    op.execute(f"ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    allowed = ", ".join(f"'{value}'" for value in values)
    op.execute(
        f"ALTER TABLE audit_log ADD CONSTRAINT {CONSTRAINT} CHECK (action IN ({allowed}))"
    )


def upgrade() -> None:
    # ---------------------------------------------------------------- settings
    op.create_table(
        "attestation_setting",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("org_namespace", sa.String(length=64), nullable=False),
        sa.Column("contract_id", sa.String(length=64), nullable=True),
        sa.Column("network", sa.String(length=16), nullable=True),
        sa.Column("signer_public_key", sa.String(length=56), nullable=True),
        sa.Column("signer_secret_encrypted", sa.String(length=500), nullable=True),
        sa.Column("external_signer", sa.Boolean(), nullable=False),
        sa.Column(
            "cadence",
            sa.Enum(*SEAL_CADENCES, native_enum=False, length=20, name="sealcadence"),
            nullable=False,
        ),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registration_tx", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "enabled = false OR (contract_id IS NOT NULL AND network IS NOT NULL)",
            name="ck_attestation_setting_enabled_is_configured",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_attestation_setting_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_attestation_setting"),
        sa.UniqueConstraint(
            "organization_id", name="uq_attestation_setting_organization_id"
        ),
    )
    op.create_index(
        "ix_attestation_setting_created_at", "attestation_setting", ["created_at"]
    )
    op.create_index(
        "ix_attestation_setting_organization_id", "attestation_setting", ["organization_id"]
    )
    # A unique *index*, not a unique constraint plus a separate index. The column is
    # declared `unique=True, index=True`, and SQLAlchemy renders that as one unique
    # index - so anything else here is schema drift that `alembic check` reports on
    # every subsequent run.
    op.create_index(
        "ix_attestation_setting_org_namespace",
        "attestation_setting",
        ["org_namespace"],
        unique=True,
    )

    # -------------------------------------------------------------------- seal
    # Created before `seal_leaf`, which carries a foreign key to it.
    op.create_table(
        "seal",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("merkle_root", sa.String(length=64), nullable=False),
        sa.Column("prev_root", sa.String(length=64), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        # NUMERIC(38, 0) rather than BIGINT: a lifetime turnover in paise exhausts a
        # signed 64-bit integer well inside the range this product targets, and a
        # control total that silently wraps is worse than no control total.
        sa.Column("debit_minor", sa.Numeric(precision=38, scale=0), nullable=False),
        sa.Column("first_leaf_seq", sa.BigInteger(), nullable=False),
        sa.Column("last_leaf_seq", sa.BigInteger(), nullable=False),
        sa.Column("covered_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("covered_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_date_from", sa.Date(), nullable=False),
        sa.Column("entry_date_to", sa.Date(), nullable=False),
        sa.Column(
            "trigger",
            sa.Enum(*SEAL_TRIGGERS, native_enum=False, length=20, name="sealtrigger"),
            nullable=False,
        ),
        sa.Column("accounting_period_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(*SEAL_STATUSES, native_enum=False, length=20, name="sealstatus"),
            nullable=False,
        ),
        sa.Column("network", sa.String(length=16), nullable=True),
        sa.Column("contract_id", sa.String(length=64), nullable=True),
        sa.Column("tx_hash", sa.String(length=64), nullable=True),
        sa.Column("ledger_sequence", sa.BigInteger(), nullable=True),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("seq > 0", name="ck_seal_seq_positive"),
        sa.CheckConstraint("entry_count > 0", name="ck_seal_count_positive"),
        sa.CheckConstraint(
            "last_leaf_seq >= first_leaf_seq", name="ck_seal_leaf_range_ordered"
        ),
        sa.CheckConstraint(
            "covered_to >= covered_from", name="ck_seal_covered_window_ordered"
        ),
        sa.CheckConstraint(
            "entry_date_to >= entry_date_from", name="ck_seal_entry_dates_ordered"
        ),
        # A confirmed seal must carry what makes it independently checkable. `tx_hash`
        # is deliberately not required: a seal discovered on chain by the reconciler
        # after an ambiguous timeout has no recoverable transaction hash, and it is
        # fully verifiable regardless - a verifier checks `verify(namespace, seq, root)`
        # against the contract, never a transaction hash.
        sa.CheckConstraint(
            "status <> 'confirmed' OR (sealed_at IS NOT NULL AND contract_id IS NOT NULL)",
            name="ck_seal_confirmed_is_checkable",
        ),
        sa.ForeignKeyConstraint(
            ["accounting_period_id"],
            ["accounting_period.id"],
            name="fk_seal_accounting_period_id_accounting_period",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name="fk_seal_created_by_id_app_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_seal_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["seal.id"],
            name="fk_seal_superseded_by_id_seal",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_seal"),
    )
    op.create_index("ix_seal_created_at", "seal", ["created_at"])
    op.create_index("ix_seal_organization_id", "seal", ["organization_id"])
    op.create_index("ix_seal_accounting_period_id", "seal", ["accounting_period_id"])
    op.create_index("ix_seal_merkle_root", "seal", ["merkle_root"])
    op.create_index("ix_seal_status", "seal", ["status"])
    op.create_index("ix_seal_tx_hash", "seal", ["tx_hash"])
    op.create_index("ix_seal_sealed_at", "seal", ["sealed_at"])
    op.create_index("ix_seal_org_seq_desc", "seal", ["organization_id", "seq"])
    op.create_index("ix_seal_status_created", "seal", ["status", "created_at"])
    # Partial unique: one live seal per sequence number, but a failed attempt must
    # not block the retry that has to reuse the number.
    op.create_index(
        "uq_seal_org_seq_live",
        "seal",
        ["organization_id", "seq"],
        unique=True,
        postgresql_where=sa.text("status <> 'failed'"),
    )
    # Partial for the same reason, and the reason is sharper here: a failed seal's
    # leaves are *released* back to the backlog, so the replacement covers exactly
    # the same range. An unconditional unique constraint on the range would make
    # that replacement impossible and the organization could never seal again.
    op.create_index(
        "uq_seal_org_last_leaf",
        "seal",
        ["organization_id", "last_leaf_seq"],
        unique=True,
        postgresql_where=sa.text("status <> 'failed'"),
    )

    # --------------------------------------------------------------- seal_leaf
    op.create_table(
        "seal_leaf",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        # No `updated_at`. An `updated_at` on a leaf would imply a leaf can change,
        # and it cannot: it is a hash of an immutable entry.
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("journal_entry_id", sa.Uuid(), nullable=False),
        sa.Column("leaf_seq", sa.BigInteger(), nullable=False),
        sa.Column("leaf_hash", sa.String(length=64), nullable=False),
        sa.Column("canonical_version", sa.SmallInteger(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("total_debit", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("seal_id", sa.Uuid(), nullable=True),
        sa.Column("leaf_index", sa.Integer(), nullable=True),
        sa.CheckConstraint("leaf_seq > 0", name="ck_seal_leaf_leaf_seq_positive"),
        sa.CheckConstraint(
            "leaf_index IS NULL OR leaf_index >= 0",
            name="ck_seal_leaf_leaf_index_non_negative",
        ),
        # Both or neither. A half-filled pair produces an inclusion proof for the
        # wrong position, which verifies against nothing and looks like tampering.
        sa.CheckConstraint(
            "(seal_id IS NULL AND leaf_index IS NULL) OR "
            "(seal_id IS NOT NULL AND leaf_index IS NOT NULL)",
            name="ck_seal_leaf_sealed_leaf_has_index",
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"],
            ["journal_entry.id"],
            name="fk_seal_leaf_journal_entry_id_journal_entry",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_seal_leaf_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["seal_id"], ["seal.id"], name="fk_seal_leaf_seal_id_seal", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_seal_leaf"),
        sa.UniqueConstraint("organization_id", "leaf_seq", name="uq_seal_leaf_org_seq"),
    )
    op.create_index("ix_seal_leaf_created_at", "seal_leaf", ["created_at"])
    op.create_index("ix_seal_leaf_organization_id", "seal_leaf", ["organization_id"])
    # Unique index rather than constraint-plus-index, for the same reason as
    # `ix_attestation_setting_org_namespace` above. One leaf per entry.
    op.create_index(
        "ix_seal_leaf_journal_entry_id", "seal_leaf", ["journal_entry_id"], unique=True
    )
    op.create_index("ix_seal_leaf_leaf_hash", "seal_leaf", ["leaf_hash"])
    op.create_index("ix_seal_leaf_entry_date", "seal_leaf", ["entry_date"])
    op.create_index("ix_seal_leaf_seal_id", "seal_leaf", ["seal_id"])
    op.create_index("ix_seal_leaf_seal_index", "seal_leaf", ["seal_id", "leaf_index"])
    # Partial: holds only the backlog, which is the only part ever scanned.
    op.create_index(
        "ix_seal_leaf_unsealed",
        "seal_leaf",
        ["organization_id", "leaf_seq"],
        postgresql_where=sa.text("seal_id IS NULL"),
    )

    _rebuild_audit_check(ACTIONS)


def downgrade() -> None:
    """Drop the three tables and narrow the audit CHECK back.

    Narrowing is safe here, unlike in ``c4d18a6f2b30``: the rows the narrowed
    constraint would reject are the ones this migration's own tables produced, and
    they are being dropped in the same breath. Any that predate the drop are
    deleted first and explicitly, rather than left to break the constraint - a
    downgrade that fails halfway is worse than one that says what it removed.
    """
    op.execute(
        "DELETE FROM audit_log WHERE action IN "
        "('attestation.enabled', 'attestation.disabled', 'attestation.registered', "
        "'attestation.signer_rotated', 'seal.created', 'seal.confirmed', "
        "'seal.failed', 'seal.reconciled', 'proof.exported')"
    )
    _rebuild_audit_check(PREVIOUS_ACTIONS)

    # `DROP TABLE` takes every index and constraint on the table with it, so the
    # twenty-odd explicit `drop_index` calls autogenerate would emit here are
    # redundant - and worse than redundant: PostgreSQL backs a UNIQUE constraint
    # with an index of the same name, so `DROP INDEX` on one fails with "cannot
    # drop index because constraint requires it" and aborts the whole downgrade
    # half-way. That happened during development, and the symptom was a database
    # left at the new revision with the old schema.
    #
    # Order matters: `seal_leaf` references `seal`.
    op.drop_table("seal_leaf")
    op.drop_table("seal")
    op.drop_table("attestation_setting")
