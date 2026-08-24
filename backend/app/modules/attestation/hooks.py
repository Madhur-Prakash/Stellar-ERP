"""The proof ledger's subscriptions to accounting events.

Two hooks, installed once from the composition root:

* **An entry was posted** -> hash it into a leaf, in the same transaction.
* **A period was closed** -> prepare a seal over everything not yet sealed.

Both are deliberately thin. They translate an accounting event into a call on
:class:`~app.modules.attestation.service.SealService` and do nothing else, so the
rules live in the service where they can be tested without a posting engine.

Neither touches the network. That is the property that makes this safe to run
inside a posting transaction: the entry-posted hook writes one row, and the
period-closed hook writes one row. The chain is reached later, by the worker,
outside anybody's request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.accounting.hooks import (
    installed_hooks,
    on_entry_posted,
    on_period_closed,
)
from app.modules.attestation.models import SealCadence, SealTrigger
from app.modules.attestation.repository import AttestationSettingRepository
from app.modules.attestation.service import SealService

if TYPE_CHECKING:
    from app.core.context import RequestContext
    from app.modules.accounting.models import AccountingPeriod, JournalEntry
    from app.modules.users.models import User

log = get_logger(__name__)


async def record_entry_leaf(session: AsyncSession, entry: JournalEntry) -> None:
    """Commit to a freshly posted entry.

    One row, no network, and it cannot raise past the seam - see
    :func:`app.modules.accounting.hooks.notify_entry_posted`.
    """
    await SealService(session).record_leaf(entry)


async def seal_on_period_close(
    session: AsyncSession,
    period: AccountingPeriod,
    actor: User,
    ctx: RequestContext | None = None,
) -> None:
    """Prepare a seal when a period is closed.

    Only writes the intent. The worker submits it, which is why closing a period
    stays a millisecond database transaction rather than a five-second wait on
    consensus.

    Skipped for the ``MANUAL`` cadence, because a business that has asked to seal
    only when it presses the button has said something specific and closing a
    period is not pressing the button.
    """
    setting = await AttestationSettingRepository(session).for_organization(period.organization_id)
    if setting is None or not setting.enabled:
        return
    if setting.cadence is SealCadence.MANUAL:
        return

    seal = await SealService(session).create_seal(
        period.organization_id,
        trigger=SealTrigger.PERIOD_CLOSE,
        actor=actor,
        accounting_period_id=period.id,
        ctx=ctx,
    )
    if seal is not None:
        log.info(
            "period close prepared a seal",
            extra={
                "period": period.name,
                "seq": seal.seq,
                "entries": seal.entry_count,
                "organization_id": str(period.organization_id),
            },
        )


def install_attestation_hooks() -> dict[str, int]:
    """Subscribe the proof ledger to the accounting module's events.

    Called from ``create_app``. Returns the resulting hook counts so startup can
    log them - "sealing is not working" and "the hook was never installed" look
    identical from the outside, and this is the cheapest way to tell them apart.

    A no-op when the server-wide switch is off, which is what makes
    ``ATTESTATION_ENABLED=false`` a genuine removal rather than a flag checked in
    forty places.
    """
    if not settings.attestation_enabled:
        log.info("proof ledger disabled; accounting hooks not installed")
        return installed_hooks()

    on_entry_posted(record_entry_leaf)
    on_period_closed(seal_on_period_close)

    counts = installed_hooks()
    log.info(
        "proof ledger hooks installed",
        extra={
            "network": settings.stellar_network,
            "contract": settings.soroban_contract_id,
            "ready": settings.attestation_ready,
            **counts,
        },
    )
    return counts
