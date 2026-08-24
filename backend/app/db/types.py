"""Shared column types.

The important one is :data:`Money`.

**Money is never a float.** ``0.1 + 0.2 != 0.3`` in binary floating point, and in a
ledger that error compounds: a trial balance that should sum to zero comes out at
``-0.000000001``, the balance sheet fails to balance, and the cause is invisible.
``NUMERIC`` is exact decimal arithmetic, and asyncpg maps it to
:class:`decimal.Decimal` in Python, so the exactness survives the round trip.

Precision is ``NUMERIC(18, 4)``:

* **4 decimal places**, not 2, because unit prices and tax rates need sub-paisa
  precision during calculation. Rounding happens once, at presentation, not
  repeatedly mid-computation.
* **18 total digits** leaves 14 for the integer part - up to ~99 trillion, which
  is beyond any SME's books even in a low-denomination currency.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from sqlalchemy import Date, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import mapped_column

#: Monetary amount. Exact decimal - see the module docstring.
Money = Annotated[Decimal, mapped_column(Numeric(18, 4))]

#: A quantity of stock. Same exactness argument; 4dp covers fractional units
#: (kg, litres, hours) without drift.
Quantity = Annotated[Decimal, mapped_column(Numeric(18, 4))]

#: A percentage or rate, e.g. a GST rate of 18.0000.
Rate = Annotated[Decimal, mapped_column(Numeric(9, 4))]

#: A calendar date with no time component. Accounting dates are dates, not
#: instants: an entry belongs to a day in the books, and attaching a timezone to
#: it invites the classic off-by-one where a late-evening entry lands in the
#: wrong month for a user in another zone.
LedgerDate = Annotated[dt.date, mapped_column(Date)]

#: ISO 4217 currency code.
CurrencyCode = Annotated[str, mapped_column(String(3))]

#: Zero, for defaults. Module-level so callers share one instance.
ZERO: Decimal = Decimal("0.0000")


def enum_column[E: StrEnum](enum_cls: type[E], *, length: int) -> SAEnum:
    """A ``StrEnum`` column stored as its **value**, with a real CHECK constraint.

    Every enum column in the schema goes through this. Two SQLAlchemy defaults
    make the naive spelling - ``Enum(MyEnum, native_enum=False)`` - quietly wrong:

    1. **It stores the member *name*, not the value.** ``EntryStatus.DRAFT``
       persists as ``'DRAFT'`` while the API serialises ``'draft'``, so the
       database and the JSON disagree. Worse, any SQL predicate written against
       the value - a partial index ``WHERE status = 'pending'``, a ``CHECK`` -
       silently never matches, so the constraint exists but enforces nothing.
       ``values_callable`` fixes this by persisting ``member.value``.

    2. **``create_constraint`` defaults to False.** Without it there is no
       ``CHECK`` at all: the column is an unconstrained ``VARCHAR`` that will
       accept any string a bad migration or manual UPDATE puts there.

    ``native_enum=False`` is still deliberate - a real PostgreSQL ``ENUM`` type
    needs ``ALTER TYPE`` to gain a value, which is awkward to reverse. A
    ``VARCHAR`` plus ``CHECK`` is a one-line, fully reversible migration.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        validate_strings=True,
        create_constraint=True,
        values_callable=lambda cls: [member.value for member in cls],
    )
