"""Has the migrated schema fallen behind the models?

Run against a migrated database. Exits non-zero, naming the constraint and the values, if
any enum column's ``CHECK`` refuses a value the corresponding Python enum declares.

**Why this exists as its own check.** Two safety nets both miss this, and each for a good
reason:

* The **test suite** builds its schema with ``create_all`` from the models. Deliberate - the
  schema under test should be the one the code describes - but it means the CHECK is always
  generated from the current enum, so it always agrees and never can disagree.
* **``alembic check``** does not compare CHECK expressions. Adding a value to a ``StrEnum``
  changes only a CHECK, so autogenerate sees nothing to do. It printed "No new upgrade
  operations detected" while ``audit_log.action`` was missing 49 of its 95 values.

The consequence was not subtle. Every write that records an audit row - uploading a document,
creating a customer, posting an invoice, adjusting stock - failed at the database with a 409,
because the audit row is part of the same transaction. 762 passing tests could not see it.

Usage::

    uv run python scripts/check_schema_drift.py
"""

from __future__ import annotations

import asyncio
import re
import sys

from sqlalchemy import Enum as SAEnum
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.db.registry  # noqa: F401  - importing it populates the metadata
from app.core.config import settings
from app.db.base import Base

#: A CHECK that is *only* a value list on one column - what ``enum_column`` generates.
#:
#: PostgreSQL normalises ``action IN ('a', 'b')`` to
#: ``CHECK (((action)::text = ANY ((ARRAY[...])::text[])))``, so the pattern has to match
#: the stored form rather than the form the migration wrote.
#:
#: Deliberately narrow. A looser pattern also matches business rules that mention a status
#: column, such as ``ck_invoice_posted_has_journal_entry``, and those name two or three
#: statuses without listing them all - so they read as permanent drift, and a check that
#: always fails is a check everyone learns to ignore.
ENUM_CHECK = re.compile(r"^CHECK \(+\(?(?P<column>\w+)\)?::text = ANY \(")

CONSTRAINTS_SQL = text(
    """
    SELECT c.relname AS table_name,
           n.conname  AS constraint_name,
           pg_get_constraintdef(n.oid) AS definition
    FROM pg_constraint n
    JOIN pg_class c ON c.oid = n.conrelid
    JOIN pg_namespace s ON s.oid = c.relnamespace
    WHERE n.contype = 'c' AND s.nspname = 'public'
    """
)


def declared_enums() -> dict[tuple[str, str], set[str]]:
    """Every enum column the models define, and the values it permits."""
    found: dict[tuple[str, str], set[str]] = {}
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if isinstance(column.type, SAEnum) and column.type.enum_class is not None:
                found[(table.name, column.name)] = {
                    member.value for member in column.type.enum_class
                }
    return found


async def main() -> int:
    declared = declared_enums()
    if not declared:
        print("no enum columns found - the model metadata did not load", file=sys.stderr)
        return 2

    engine = create_async_engine(settings.sqlalchemy_dsn)
    try:
        async with engine.connect() as conn:
            rows = list((await conn.execute(CONSTRAINTS_SQL)).all())
    finally:
        await engine.dispose()

    problems: list[str] = []
    compared = 0

    for (table_name, column_name), values in sorted(declared.items()):
        for row in rows:
            if row.table_name != table_name:
                continue
            match = ENUM_CHECK.match(row.definition)
            if match is None or match.group("column") != column_name:
                continue

            compared += 1
            allowed = set(re.findall(r"'([^']+)'", row.definition))
            missing = sorted(values - allowed)
            if missing:
                problems.append(
                    f"  {table_name}.{column_name}  ({row.constraint_name})\n"
                    f"      the database rejects {len(missing)} value(s) the enum declares:\n"
                    f"      {', '.join(missing)}"
                )

    if compared == 0:
        print(
            "matched no enum CHECK constraints - either the database is not migrated "
            "or the pattern needs revisiting",
            file=sys.stderr,
        )
        return 2

    if problems:
        print(
            f"schema drift: {len(problems)} constraint(s) behind the models\n"
            + "\n".join(problems)
            + "\n\nWrite a migration that rebuilds the constraint. See "
            "migrations/versions/20260731_2145_b7c4e19d2a83_audit_action_check.py.",
            file=sys.stderr,
        )
        return 1

    print(f"schema matches the models ({compared} enum constraints compared)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
