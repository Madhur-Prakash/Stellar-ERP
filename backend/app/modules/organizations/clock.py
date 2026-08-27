"""What day it is, according to the organization.

``dt.date.today()`` answers for *the server*, and those are not the same question. At 00:30
in Asia/Kolkata a machine running in UTC still calls it yesterday, so for the first five and
a half hours of every Indian day a report "as at today" omits the day it claims to cover, an
invoice dated "today" is stamped yesterday, and an unpaid bill turns overdue a day early. On
1 April the same gap moves the financial-year boundary.

One function, so the rule is stated once: the FastAPI dependency in
:mod:`app.modules.auth.dependencies` and every service that needs a default date both come
here. :func:`app.modules.analytics.periods.local_date` does the timezone arithmetic and stays
pure; this adds only the lookup of which timezone to use.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.periods import local_date, local_datetime
from app.modules.organizations.models import Organization


async def organization_today(session: AsyncSession, organization_id: uuid.UUID) -> dt.date:
    """Today's date in the organization's own timezone.

    One indexed lookup by primary key. Cheap enough to call per request, and every operation
    that needs it already runs several queries - the alternative was threading a date through
    every service signature, which is the kind of change that gets half-applied.

    Falls back to UTC when the organization has no timezone set, which the column does not
    allow but a fixture might.
    """
    timezone_name = await session.scalar(
        select(Organization.timezone).where(Organization.id == organization_id)
    )
    return local_date(dt.datetime.now(dt.UTC), timezone_name or "UTC")


async def organization_timezone(session: AsyncSession, organization_id: uuid.UUID) -> str:
    """The organization's IANA timezone name, or ``"UTC"`` when it has none.

    Exposed because a screen that lets somebody choose a sealing *hour* has to say
    which clock that hour is on. "Seal at 01:00" is not an instruction until the
    zone is named, and naming the server's would be the wrong one.
    """
    timezone_name = await session.scalar(
        select(Organization.timezone).where(Organization.id == organization_id)
    )
    return timezone_name or "UTC"


async def organization_now(session: AsyncSession, organization_id: uuid.UUID) -> dt.datetime:
    """The current instant in the organization's own timezone, still aware.

    :func:`organization_today` is this with the time discarded, and most callers want
    that. The seal worker wants the hour: an owner who sets sealing to 01:00 means
    01:00 where they are, so the comparison has to happen in their zone rather than
    the server's.
    """
    timezone_name = await session.scalar(
        select(Organization.timezone).where(Organization.id == organization_id)
    )
    return local_datetime(dt.datetime.now(dt.UTC), timezone_name or "UTC")
