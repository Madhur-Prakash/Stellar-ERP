"""The seal worker - the only thing in the backend that waits on consensus.

Its job is small and its constraints are not:

1. **Reconcile on start.** Ask the chain what it holds and make the database
   agree, before doing anything that assumes the database is right.
2. **Drain the outbox.** Advance every seal still owed an outcome.
3. **Seal on cadence.** For organizations set to ``DAILY``, prepare a batch once
   the local day has turned.

Why it is a loop and not a scheduler
------------------------------------
The obvious tool is Celery, or APScheduler, or a platform cron. All three were
rejected for the same reason the rest of this codebase rejects them: the target
deployment is one ``docker compose up`` that one person operates, and a broker is
a second thing to run, monitor and restart. A ``while True`` with a sleep and an
idempotent body is the whole scheduler this needs, and it has no state of its own
to lose.

The trade-off is real and worth naming: this design gives *at-least-once* firing
with no distributed lock, so two API replicas both running the worker will both
try. That is safe rather than merely tolerable, and not by luck - it is what the
contract's sequencing guarantees. Two workers preparing a batch collide on the
``exists_open`` check or on the partial unique index; two workers submitting the
same seal collide on ``seq``, and the loser is refused by consensus. **Nothing in
this file depends on being the only one running.**

Running it separately
---------------------
``SEAL_WORKER_ENABLED=false`` in the API, then::

    python -m app.modules.attestation.worker

Same code, same function, no parallel implementation - which is the point.

That "same code" claim is what made the ``app.db.registry`` import below
necessary rather than decorative. Relationships are declared with string targets
(``"Organization"``) so modules need not import each other, and those strings
resolve against SQLAlchemy's class registry on first use. Under the API every
class has been imported by the time anything queries, because the routers pull in
every module. Under ``python -m`` nothing has: only this module's own imports are
loaded, so the first query failed with

    expression 'Organization' failed to locate a name

and the loop then swallowed it once per pass - a worker that started cleanly,
logged an error every sixty seconds, and sealed nothing. Registering the mappers
explicitly is what makes the standalone entry point actually equivalent to the
in-process one, rather than merely look it.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import signal
import uuid
from typing import Any

import app.db.registry  # noqa: F401  - registers every mapper; see below
from app.core.config import settings
from app.core.logging import configure_logging, flush_logs, get_logger
from app.db.session import session_scope
from app.modules.attestation.models import SealCadence, SealTrigger
from app.modules.attestation.repository import (
    AttestationSettingRepository,
    SealRepository,
)
from app.modules.attestation.service import SealService
from app.modules.attestation.stellar import SorobanUnavailable

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# One pass
# ---------------------------------------------------------------------------
async def run_once() -> dict[str, Any]:
    """One full pass: drain what is in flight, then seal what is due.

    Order matters. Draining first means a seal that is already in flight gets
    resolved before the cadence check asks whether a new one is needed - and
    ``create_seal`` refuses to build a second batch while one is open, so the
    other order would simply never seal anything after the first hiccup.

    Every pass gets its own transaction. A pass that fails half-way leaves the
    database exactly as it was, and the chain is unaffected either way because the
    chain is the authority on what has been sealed.
    """
    report: dict[str, Any] = {
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "drained": {},
        "sealed": [],
        "errors": [],
    }

    async with session_scope() as session:
        sealer = SealService(session)
        try:
            report["drained"] = await sealer.drain(limit=25)
        except Exception as exc:
            log.error("drain pass failed", extra={"error": str(exc)})
            report["errors"].append(f"drain: {exc}")

    async with session_scope() as session:
        try:
            report["sealed"] = await _seal_due_organizations(session)
        except Exception as exc:
            log.error("cadence pass failed", extra={"error": str(exc)})
            report["errors"].append(f"cadence: {exc}")

    return report


async def _seal_due_organizations(session: Any) -> list[dict[str, Any]]:
    """Prepare a seal for every organization whose daily window has arrived."""
    settings_repo = AttestationSettingRepository(session)
    seal_repo = SealRepository(session)
    sealer = SealService(session)
    prepared: list[dict[str, Any]] = []

    for setting in await settings_repo.enabled_organizations():
        if setting.cadence is not SealCadence.DAILY:
            continue
        if not setting.registered_at:
            continue
        if await seal_repo.exists_open(setting.organization_id):
            continue

        if not await _daily_window_open(session, setting.organization_id, seal_repo):
            continue

        try:
            seal = await sealer.create_seal(
                setting.organization_id,
                trigger=SealTrigger.SCHEDULE,
            )
        except Exception as exc:
            log.error(
                "failed to prepare a scheduled seal",
                extra={"organization_id": str(setting.organization_id), "error": str(exc)},
            )
            continue

        if seal is not None:
            prepared.append(
                {
                    "organization_id": str(setting.organization_id),
                    "seq": seal.seq,
                    "entries": seal.entry_count,
                }
            )

    return prepared


async def _daily_window_open(
    session: Any, organization_id: uuid.UUID, seal_repo: SealRepository
) -> bool:
    """Whether this organization is due a daily seal.

    Due means: the organization's own local date has advanced past the date of its
    last confirmed seal, and its configured hour has passed.

    **The organization's clock, not the server's.** A business in Asia/Kolkata on a
    server in UTC would otherwise have its "daily" seal fire at 06:30 local, and
    its 23:58 entries would land in the following day's batch - which is not
    wrong, exactly, but it makes "sealed up to yesterday" mean something different
    from what the owner reads on the screen.
    """
    from app.modules.organizations.clock import organization_today

    local_today = await organization_today(session, organization_id)
    now_utc = dt.datetime.now(dt.UTC)

    last = await seal_repo.latest_confirmed(organization_id)
    if last is None:
        # Nothing sealed yet: the backlog is due as soon as there is one, with no
        # waiting for a window. A business that has just switched sealing on should
        # not have to wait until tomorrow to see its first seal.
        return True

    last_local_date = (last.sealed_at or last.covered_to).date()
    if local_today <= last_local_date:
        return False

    # Past the configured hour, measured against the organization's own day. Using
    # the UTC hour here would make SEAL_DAILY_HOUR mean a different local time for
    # every tenant.
    return now_utc.hour >= settings.seal_daily_hour or local_today > last_local_date + dt.timedelta(
        days=1
    )


async def reconcile_all() -> dict[str, Any]:
    """Ask the chain about every enabled organization and correct local state.

    Run once at startup. This is the step that makes the ambiguous failure
    survivable across a restart: a seal submitted moments before the process died
    is found on chain and recorded, rather than being retried into a
    ``sequence_out_of_order`` rejection.
    """
    summary: dict[str, Any] = {"organizations": 0, "adjusted": 0, "disagreements": []}

    async with session_scope() as session:
        settings_repo = AttestationSettingRepository(session)
        sealer = SealService(session)

        for setting in await settings_repo.enabled_organizations():
            summary["organizations"] += 1
            try:
                result = await sealer.reconcile(setting.organization_id)
            except SorobanUnavailable as exc:
                log.warning(
                    "could not reconcile: chain unreachable",
                    extra={
                        "organization_id": str(setting.organization_id),
                        "error": str(exc.message),
                    },
                )
                continue
            except Exception as exc:
                log.error(
                    "reconciliation failed",
                    extra={
                        "organization_id": str(setting.organization_id),
                        "error": str(exc),
                    },
                )
                continue

            summary["adjusted"] += int(result.get("adjusted") or 0)
            if result.get("reconciled") and result.get("agrees") is False:
                summary["disagreements"].append(
                    {
                        "organization_id": str(setting.organization_id),
                        "chain_head": result.get("chain_head"),
                        "local_head": result.get("local_head"),
                    }
                )

    if summary["disagreements"]:
        # Loud, because this is the condition the whole subsystem exists to detect,
        # and it must not scroll past in a startup log as an INFO line.
        log.error(
            "the chain and the local database disagree for one or more organizations",
            extra={"disagreements": summary["disagreements"]},
        )

    return summary


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
async def run_forever(stop: asyncio.Event | None = None) -> None:
    """Reconcile once, then pass every ``SEAL_WORKER_INTERVAL_SECONDS``.

    ``stop`` lets the API's lifespan cancel it cleanly on shutdown. Waiting on an
    event rather than sleeping means a shutdown is immediate instead of taking up
    to a full interval, which on a 60-second interval is the difference between a
    container stopping and a container being killed.
    """
    stop = stop or asyncio.Event()

    log.info(
        "seal worker starting",
        extra={
            "interval_seconds": settings.seal_worker_interval_seconds,
            "network": settings.stellar_network,
            "contract": settings.soroban_contract_id,
        },
    )

    try:
        await reconcile_all()
    except Exception as exc:
        # A failed startup reconciliation must not stop the worker: the same
        # correction happens on the next drain, and refusing to run would turn a
        # transient RPC outage into sealing being off until somebody noticed.
        log.error("startup reconciliation failed", extra={"error": str(exc)})

    while not stop.is_set():
        try:
            report = await run_once()
            drained = report.get("drained") or {}
            if drained.get("processed") or report.get("sealed"):
                log.info(
                    "seal worker pass complete",
                    extra={
                        "processed": drained.get("processed", 0),
                        "confirmed": drained.get("confirmed", 0),
                        "failed": drained.get("failed", 0),
                        "waiting": drained.get("waiting", 0),
                        "prepared": len(report.get("sealed") or []),
                    },
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The loop is the thing that must not die. Any escape here would stop
            # sealing silently, which looks exactly like sealing being switched off.
            log.error("seal worker pass raised", extra={"error": str(exc)})

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.seal_worker_interval_seconds)

    log.info("seal worker stopped")


def main() -> None:
    """Entry point for ``python -m app.modules.attestation.worker``.

    Handles SIGINT and SIGTERM so a container stop drains the current pass instead
    of being killed part-way through one - which for this worker means a seal left
    in ``SUBMITTED`` that the next start has to reconcile. Correct either way, but
    needlessly noisy.
    """
    configure_logging()
    stop = asyncio.Event()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, AttributeError):
                # Windows implements neither for the proactor loop; the fallback is
                # KeyboardInterrupt, which the outer suppress below handles.
                loop.add_signal_handler(sig, stop.set)
        await run_forever(stop)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("seal worker interrupted")
    finally:
        flush_logs(timeout=3.0)


if __name__ == "__main__":
    main()
