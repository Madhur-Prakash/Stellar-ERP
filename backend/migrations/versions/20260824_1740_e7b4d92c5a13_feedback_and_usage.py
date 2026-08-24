"""Feedback, and first-party usage analytics.

``feedback`` holds what somebody typed into the feedback box. Both ``user_id`` and
``organization_id`` are nullable, and that is the design rather than laziness: the
most useful report in any product comes from somebody who could not get *in*, and
a NOT NULL foreign key would silence exactly that person.

``usage_event`` holds which screens get opened and which actions get taken, in the
operator's own PostgreSQL. The obvious alternative - a hosted analytics script in
the browser - would mean shipping every user's navigation to a third party from a
product sold on the promise that its data stays on your own server.

Note what ``usage_event`` has no column for: there is no ``payload``. ``context``
is JSONB but the service that writes it allow-lists the keys, so there is no place
an eager future caller can put an invoice total. An events table with an open
payload is how an analytics table ends up inside the compliance boundary.

**Additive.** Two new tables, nothing existing altered.

Revision ID: e7b4d92c5a13
Revises: d5a3c81b9f04
Created: 2026-08-24 17:40:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7b4d92c5a13"
down_revision: str | None = "d5a3c81b9f04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FEEDBACK_KINDS = ("problem", "idea", "praise", "question")
FEEDBACK_STATUSES = ("new", "read", "actioned", "declined")


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "kind",
            sa.Enum(*FEEDBACK_KINDS, native_enum=False, length=20, name="feedbackkind"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                *FEEDBACK_STATUSES, native_enum=False, length=20, name="feedbackstatus"
            ),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("screen", sa.String(length=120), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("app_version", sa.String(length=40), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("triage_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # `SET NULL`, not `CASCADE`: a message survives the account that sent it.
        # Deleting a user should not delete their bug report.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_feedback_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_feedback_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_feedback"),
    )
    op.create_index("ix_feedback_created_at", "feedback", ["created_at"])
    op.create_index("ix_feedback_kind", "feedback", ["kind"])
    op.create_index("ix_feedback_status", "feedback", ["status"])
    op.create_index("ix_feedback_user_id", "feedback", ["user_id"])
    op.create_index("ix_feedback_organization_id", "feedback", ["organization_id"])
    op.create_index("ix_feedback_request_id", "feedback", ["request_id"])
    # The inbox: newest unread first.
    op.create_index("ix_feedback_status_created", "feedback", ["status", "created_at"])

    op.create_table(
        "usage_event",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        # No `updated_at`. An event is a fact about a moment, and a column implying
        # it can change would be a lie the schema tells.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("surface", sa.String(length=20), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_usage_event_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_usage_event_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_usage_event"),
    )
    op.create_index("ix_usage_event_created_at", "usage_event", ["created_at"])
    op.create_index("ix_usage_event_action", "usage_event", ["action"])
    op.create_index("ix_usage_event_user_id", "usage_event", ["user_id"])
    op.create_index("ix_usage_event_organization_id", "usage_event", ["organization_id"])
    # The only two queries that run: what has this organization been doing, and
    # which actions are used at all.
    op.create_index(
        "ix_usage_event_org_created", "usage_event", ["organization_id", "created_at"]
    )
    op.create_index(
        "ix_usage_event_action_created", "usage_event", ["action", "created_at"]
    )


def downgrade() -> None:
    # `DROP TABLE` takes the indexes with it; enumerating them adds a way for the
    # downgrade to fail half-way and nothing else. See `d5a3c81b9f04`.
    op.drop_table("usage_event")
    op.drop_table("feedback")
