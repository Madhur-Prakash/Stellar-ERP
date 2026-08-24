"""Observers for the two accounting events other modules care about.

This exists to answer one question without breaking the layering: how does the
proof ledger learn that an entry was posted?

The obvious answer is for ``PostingService`` to call ``SealService``, and it is
wrong. Dependencies in this codebase point inward - accounting is the contract
that sales, purchasing and billing are built against, and it must not acquire a
dependency on a module that sits above it. An ``import`` from accounting into
attestation would mean the ledger could no longer be tested, reasoned about, or
deployed without the blockchain subsystem, which is the opposite of what
"modular" was supposed to buy.

So accounting *announces*, and interested modules *subscribe*. Accounting
imports nothing new; attestation registers itself at startup
(:func:`app.modules.attestation.hooks.install_attestation_hooks`, called from the
composition root in ``main.py``). Turning the proof ledger off removes the
subscriber and the ledger never notices.

Two properties this seam guarantees, both of which matter more than the
indirection costs:

**A hook runs inside the caller's transaction.** It is handed the same session,
so anything it writes commits or rolls back with the posting it describes. A leaf
committed for an entry that was rolled back would be a commitment to something
that never happened.

**A hook can never fail its caller.** Every exception is caught and logged here.
The posting is the statutory act; an observer is a commentary on it. A bug in the
proof ledger must not be able to stop a business issuing an invoice - and without
this guarantee written down in one place, it eventually would.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.core.context import RequestContext
    from app.modules.accounting.models import AccountingPeriod, JournalEntry
    from app.modules.users.models import User

log = get_logger(__name__)

#: Called after an entry has been posted and numbered, in the same transaction.
EntryPostedHook = Callable[[AsyncSession, "JournalEntry"], Awaitable[None]]

#: Called after a period has been closed or locked, in the same transaction.
PeriodClosedHook = Callable[
    [AsyncSession, "AccountingPeriod", "User", "RequestContext | None"],
    Awaitable[None],
]

_entry_posted: Final[list[EntryPostedHook]] = []
_period_closed: Final[list[PeriodClosedHook]] = []


def on_entry_posted(hook: EntryPostedHook) -> None:
    """Subscribe to postings. Idempotent, so a double install is harmless."""
    if hook not in _entry_posted:
        _entry_posted.append(hook)


def on_period_closed(hook: PeriodClosedHook) -> None:
    """Subscribe to period closes. Idempotent."""
    if hook not in _period_closed:
        _period_closed.append(hook)


def clear_hooks() -> None:
    """Remove every subscriber.

    For tests. Without it, a test that installs the attestation hooks leaks them
    into every subsequent test in the session, and an accounting test would start
    silently writing leaves - which is the kind of cross-test coupling that makes
    one failure look like ten.
    """
    _entry_posted.clear()
    _period_closed.clear()


def installed_hooks() -> dict[str, int]:
    """How many subscribers are attached, for the startup log and ``/health``.

    Reported because "sealing is not working" and "the hook was never installed"
    look identical from the outside, and this is the cheapest way to tell them
    apart.
    """
    return {"entry_posted": len(_entry_posted), "period_closed": len(_period_closed)}


async def notify_entry_posted(session: AsyncSession, entry: JournalEntry) -> None:
    """Announce a posted entry to every subscriber.

    Exceptions are swallowed by design - see the module docstring. They are logged
    at ``error`` with the entry's id, so a subscriber that is failing is loud in
    the log without being fatal to the posting.
    """
    for hook in _entry_posted:
        try:
            await hook(session, entry)
        except Exception as exc:
            log.error(
                "an entry-posted hook failed; the posting itself is unaffected",
                extra={
                    "hook": getattr(hook, "__qualname__", repr(hook)),
                    "journal_entry_id": str(entry.id),
                    "error": str(exc),
                },
            )


async def notify_period_closed(
    session: AsyncSession,
    period: AccountingPeriod,
    actor: User,
    ctx: RequestContext | None = None,
) -> None:
    """Announce a closed period to every subscriber."""
    for hook in _period_closed:
        try:
            await hook(session, period, actor, ctx)
        except Exception as exc:
            log.error(
                "a period-closed hook failed; the close itself is unaffected",
                extra={
                    "hook": getattr(hook, "__qualname__", repr(hook)),
                    "period_id": str(period.id),
                    "error": str(exc),
                },
            )


# ---------------------------------------------------------------------------
# Introspection used by the health endpoint
# ---------------------------------------------------------------------------
def describe() -> dict[str, Any]:
    """A small report for diagnostics."""
    return {
        "entry_posted": [getattr(h, "__qualname__", repr(h)) for h in _entry_posted],
        "period_closed": [getattr(h, "__qualname__", repr(h)) for h in _period_closed],
        "as_of": dt.datetime.now(dt.UTC).isoformat(),
    }


__all__ = [
    "EntryPostedHook",
    "PeriodClosedHook",
    "clear_hooks",
    "describe",
    "installed_hooks",
    "notify_entry_posted",
    "notify_period_closed",
    "on_entry_posted",
    "on_period_closed",
]
