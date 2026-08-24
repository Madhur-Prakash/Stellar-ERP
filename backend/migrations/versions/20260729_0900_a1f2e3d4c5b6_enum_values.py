"""enum columns store values, not names, and gain real CHECK constraints

Revision ID: a1f2e3d4c5b6
Revises: 7b9866d8ab7e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1f2e3d4c5b6"
down_revision = "7b9866d8ab7e"
branch_labels = None
depends_on = None


# (table, column, {member_name: member_value}, constraint_name)
#
# Written out as literals on purpose. Importing the live enums here would let a
# future edit silently change what this migration does to historical data.
ENUM_COLUMNS: list[tuple[str, str, dict[str, str], str]] = [
    (
        "organization",
        "plan",
        {"FREE": "free", "STARTER": "starter", "GROWTH": "growth", "ENTERPRISE": "enterprise"},
        "ck_organization_organizationplan",
    ),
    (
        "organization_member",
        "status",
        {"ACTIVE": "active", "SUSPENDED": "suspended"},
        "ck_organization_member_memberstatus",
    ),
    (
        "invitation",
        "status",
        {"PENDING": "pending", "ACCEPTED": "accepted", "REVOKED": "revoked", "EXPIRED": "expired"},
        "ck_invitation_invitationstatus",
    ),
    (
        "user_session",
        "login_method",
        {
            "PASSWORD": "password",
            "MAGIC_LINK": "magic_link",
            "OTP": "otp",
            "INVITATION": "invitation",
            "IMPERSONATION": "impersonation",
        },
        "ck_user_session_loginmethod",
    ),
    (
        "user_session",
        "revocation_reason",
        {
            "LOGOUT": "logout",
            "LOGOUT_ALL": "logout_all",
            "PASSWORD_CHANGED": "password_changed",
            "ROTATED": "rotated",
            "REUSE_DETECTED": "reuse_detected",
            "ADMIN_REVOKED": "admin_revoked",
            "ACCOUNT_DISABLED": "account_disabled",
        },
        "ck_user_session_sessionrevocationreason",
    ),
    (
        "audit_log",
        "action",
        {
            "USER_REGISTERED": "user.registered",
            "USER_LOGGED_IN": "user.logged_in",
            "USER_LOGIN_FAILED": "user.login_failed",
            "USER_LOGGED_OUT": "user.logged_out",
            "USER_EMAIL_VERIFIED": "user.email_verified",
            "USER_PASSWORD_CHANGED": "user.password_changed",
            "USER_PASSWORD_RESET_REQUESTED": "user.password_reset_requested",
            "USER_PASSWORD_RESET_COMPLETED": "user.password_reset_completed",
            "USER_MAGIC_LINK_REQUESTED": "user.magic_link_requested",
            "USER_OTP_REQUESTED": "user.otp_requested",
            "USER_LOCKED_OUT": "user.locked_out",
            "TWO_FACTOR_ENABLED": "two_factor.enabled",
            "TWO_FACTOR_DISABLED": "two_factor.disabled",
            "TWO_FACTOR_CHALLENGE_FAILED": "two_factor.challenge_failed",
            "TWO_FACTOR_RECOVERY_CODE_USED": "two_factor.recovery_code_used",
            "TWO_FACTOR_RECOVERY_CODES_REGENERATED": "two_factor.recovery_codes_regenerated",
            "SESSION_REVOKED": "session.revoked",
            "SESSION_ALL_REVOKED": "session.all_revoked",
            "SESSION_REUSE_DETECTED": "session.reuse_detected",
            "USER_PROFILE_UPDATED": "user.profile_updated",
            "ORG_CREATED": "organization.created",
            "ORG_UPDATED": "organization.updated",
            "ORG_DELETED": "organization.deleted",
            "ORG_SWITCHED": "organization.switched",
            "MEMBER_INVITED": "member.invited",
            "MEMBER_INVITE_RESENT": "member.invite_resent",
            "MEMBER_INVITE_REVOKED": "member.invite_revoked",
            "MEMBER_JOINED": "member.joined",
            "MEMBER_ROLE_CHANGED": "member.role_changed",
            "MEMBER_SUSPENDED": "member.suspended",
            "MEMBER_REACTIVATED": "member.reactivated",
            "MEMBER_REMOVED": "member.removed",
            "ROLE_CREATED": "role.created",
            "ROLE_UPDATED": "role.updated",
            "ROLE_DELETED": "role.deleted",
            "SETTINGS_UPDATED": "settings.updated",
            "ACCOUNT_CREATED": "account.created",
            "ACCOUNT_UPDATED": "account.updated",
            "ACCOUNT_DELETED": "account.deleted",
            "FISCAL_YEAR_CREATED": "fiscal_year.created",
            "PERIOD_CLOSED": "period.closed",
            "PERIOD_REOPENED": "period.reopened",
            "JOURNAL_ENTRY_CREATED": "journal_entry.created",
            "JOURNAL_ENTRY_POSTED": "journal_entry.posted",
            "JOURNAL_ENTRY_REVERSED": "journal_entry.reversed",
            "JOURNAL_ENTRY_DELETED": "journal_entry.deleted",
        },
        "ck_audit_log_auditaction",
    ),
    (
        "audit_log",
        "severity",
        {"INFO": "info", "WARNING": "warning", "CRITICAL": "critical"},
        "ck_audit_log_auditseverity",
    ),
    (
        "account",
        "account_type",
        {
            "ASSET": "asset",
            "LIABILITY": "liability",
            "EQUITY": "equity",
            "INCOME": "income",
            "EXPENSE": "expense",
        },
        "ck_account_accounttype",
    ),
    (
        "account",
        "subtype",
        {
            "CASH": "cash",
            "BANK": "bank",
            "ACCOUNTS_RECEIVABLE": "accounts_receivable",
            "INVENTORY": "inventory",
            "OTHER_CURRENT_ASSET": "other_current_asset",
            "FIXED_ASSET": "fixed_asset",
            "ACCUMULATED_DEPRECIATION": "accumulated_depreciation",
            "OTHER_ASSET": "other_asset",
            "ACCOUNTS_PAYABLE": "accounts_payable",
            "TAX_PAYABLE": "tax_payable",
            "OTHER_CURRENT_LIABILITY": "other_current_liability",
            "LONG_TERM_LIABILITY": "long_term_liability",
            "CAPITAL": "capital",
            "DRAWINGS": "drawings",
            "RETAINED_EARNINGS": "retained_earnings",
            "OPERATING_REVENUE": "operating_revenue",
            "OTHER_INCOME": "other_income",
            "COST_OF_GOODS_SOLD": "cost_of_goods_sold",
            "OPERATING_EXPENSE": "operating_expense",
            "PAYROLL_EXPENSE": "payroll_expense",
            "DEPRECIATION_EXPENSE": "depreciation_expense",
            "TAX_EXPENSE": "tax_expense",
            "OTHER_EXPENSE": "other_expense",
        },
        "ck_account_accountsubtype",
    ),
    (
        "fiscal_year",
        "status",
        {"OPEN": "open", "CLOSED": "closed", "LOCKED": "locked"},
        "ck_fiscal_year_periodstatus",
    ),
    (
        "accounting_period",
        "status",
        {"OPEN": "open", "CLOSED": "closed", "LOCKED": "locked"},
        "ck_accounting_period_periodstatus",
    ),
    (
        "journal",
        "journal_type",
        {
            "GENERAL": "general",
            "SALES": "sales",
            "PURCHASE": "purchase",
            "CASH": "cash",
            "BANK": "bank",
            "OPENING": "opening",
        },
        "ck_journal_journaltype",
    ),
    (
        "journal_entry",
        "status",
        {"DRAFT": "draft", "POSTED": "posted", "REVERSED": "reversed"},
        "ck_journal_entry_entrystatus",
    ),
]


def upgrade() -> None:
    """Rewrite stored names to values, then constrain the columns.

    Two independent defects are being corrected:

    1. **Values were stored as member names.** `sqlalchemy.Enum` persists
       `EntryStatus.DRAFT` as `'DRAFT'` while the API serialises `'draft'`. Any
       SQL predicate written against the value therefore never matched - most
       consequentially `uq_invitation_pending_email`, the partial unique index
       meant to guarantee one live invitation per email, which has been inert.

    2. **No CHECK constraint existed.** `Enum(native_enum=False)` only emits one
       when `create_constraint=True`, which defaults to False, so these columns
       have been unconstrained VARCHARs.

    Data is remapped before the constraints are added, so the constraint cannot
    fail on rows written under the old scheme.
    """
    for table, column, mapping, constraint in ENUM_COLUMNS:
        for name, value in mapping.items():
            if name == value:
                continue  # already correct
            # S608: `table`/`column` are literals from ENUM_COLUMNS above, never
            # request data. The *values* are bound parameters, as they must be.
            op.execute(
                sa.text(  # noqa: S608
                    f"UPDATE {table} SET {column} = :value WHERE {column} = :name"
                ).bindparams(value=value, name=name)
            )

        allowed = ", ".join(f"'{value}'" for value in dict.fromkeys(mapping.values()))
        op.create_check_constraint(constraint, table, f"{column} IN ({allowed})")


def downgrade() -> None:
    """Drop the constraints and put the member names back.

    **``IF EXISTS``, not ``op.drop_constraint``.** This migration creates
    ``ck_audit_log_auditaction``, but two later migrations own it afterwards:
    ``b7c4e19d2a83`` rebuilds it to match the grown enum, and ``c4d18a6f2b30`` widens it
    again for ``document.corrected``. Both *drop* it on their own downgrade and neither
    recreates it - so by the time this runs in a full ``downgrade base`` the constraint is
    already gone, and a bare ``DROP CONSTRAINT`` aborts the entire chain with
    ``UndefinedObjectError``.

    A downgrade has to tolerate the schema a *later* migration left behind, which is not
    the shape this migration's own upgrade produced.

    The name is written out rather than passed to ``op.drop_constraint`` because the
    metadata naming convention is ``ck_%(table_name)s_%(constraint_name)s`` and the names
    in :data:`ENUM_COLUMNS` already begin with ``ck_``, so what exists in the database is
    the doubled ``ck_audit_log_ck_audit_log_auditaction``. Spelling it here keeps this
    statement naming the same object as the two migrations above.
    """
    for table, column, mapping, constraint in ENUM_COLUMNS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_{constraint}")

        for name, value in mapping.items():
            if name == value:
                continue
            op.execute(
                sa.text(  # noqa: S608 - see upgrade()
                    f"UPDATE {table} SET {column} = :name WHERE {column} = :value"
                ).bindparams(name=name, value=value)
            )
