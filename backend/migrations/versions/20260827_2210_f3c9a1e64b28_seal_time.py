"""Per-organization sealing time.

Adds ``attestation_setting.seal_minute``: the time of day, as minutes past
midnight in the organization's own timezone, at which a ``DAILY`` seal may fire.

**Minutes rather than an hour.** Whole hours are the obvious storage and the wrong
one: the useful sealing time is when nobody is posting, and that is 01:00 for one
business and 03:30 for another whose night shift ends at 03:00. Storing an hour
would have made "half past" unrepresentable, and the migration that widened it
later would have to guess what the stored 3 had meant.

**Nullable, with no default, on purpose.** Null means "use the install's
``SEAL_DAILY_HOUR``", and that is a different state from a stored value. They look
identical until an operator changes the environment variable, at which point every
organization that never expressed a preference should follow it and every
organization that did should not. Backfilling the current default would silently
pin every existing tenant to whatever the value happened to be the afternoon this
migration ran, with no way to tell afterwards which of them had actually chosen it.

The CHECK is here rather than left to the application because a value outside
0-1439 would not fail loudly - it would simply never match, and sealing would stop
with no error anywhere. Silent cessation is the one failure mode this subsystem
must not have.

Revision ID: f3c9a1e64b28
Revises: e7b4d92c5a13
"""

from __future__ import annotations

from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "f3c9a1e64b28"
down_revision: str | None = "e7b4d92c5a13"
branch_labels: str | None = None
depends_on: str | None = None

TABLE: Final = "attestation_setting"
COLUMN: Final = "seal_minute"
CHECK: Final = "seal_minute_is_a_time_of_day"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.SmallInteger(), nullable=True))
    op.create_check_constraint(
        CHECK,
        TABLE,
        "seal_minute IS NULL OR (seal_minute >= 0 AND seal_minute <= 1439)",
    )


def downgrade() -> None:
    # The constraint first: Postgres will not drop a column a CHECK still
    # references without a cascade, and a cascade here would be a blunter
    # instrument than this needs.
    op.drop_constraint(CHECK, TABLE, type_="check")
    op.drop_column(TABLE, COLUMN)
