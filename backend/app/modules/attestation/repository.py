"""Data access for the proof ledger.

The only layer that touches the session. Nothing here raises an HTTP error and
nothing here knows what a Soroban RPC is.

Two queries in this file carry the whole subsystem's correctness, and both are
about ordering under concurrency:

* :meth:`SealLeafRepository.next_leaf_seq` allocates the gap-free posting
  sequence a batch is defined against.
* :meth:`SealRepository.claim_batch` selects the unsealed backlog and must not
  hand the same leaf to two seals.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import Select, func, select, update

from app.core.pagination import CursorParams
from app.db.repository import BaseRepository
from app.modules.attestation.models import (
    AttestationSetting,
    Seal,
    SealLeaf,
    SealStatus,
)


class AttestationSettingRepository(BaseRepository[AttestationSetting]):
    model = AttestationSetting

    async def for_organization(self, organization_id: uuid.UUID) -> AttestationSetting | None:
        return await self.get_by(organization_id=organization_id)

    async def by_namespace(self, namespace: str) -> AttestationSetting | None:
        """Resolve a namespace back to its organization.

        The public verifier's only lookup, and the reason the namespace is unique
        across the install. Note what it does *not* do: it never returns the
        organization's name or anything else identifying. The verifier is given a
        namespace by the business, and all this resolves is which contract and
        network to read - the identity stays with whoever was handed the bundle.
        """
        return await self.get_by(org_namespace=namespace)

    async def enabled_organizations(self) -> Sequence[AttestationSetting]:
        """Every organization with sealing switched on, for the scheduler."""
        return await self.list_all(AttestationSetting.enabled.is_(True))


class SealLeafRepository(BaseRepository[SealLeaf]):
    model = SealLeaf

    async def next_leaf_seq(self, organization_id: uuid.UUID) -> int:
        """The next posting sequence for this organization.

        ``MAX(leaf_seq) + 1`` under a row lock taken on the organization, and the
        lock is the point. Two concurrent postings that both read the same maximum
        would produce two leaves with the same sequence - and because a batch is
        defined as a half-open *range* of sequences, a duplicate does not merely
        collide on a unique index, it makes the range ambiguous about how many
        leaves it contains.

        The lock is on ``organization`` rather than on ``seal_leaf`` because there
        is nothing to lock on the first insert - ``SELECT ... FOR UPDATE`` over an
        empty result set locks nothing at all, which is exactly the case a new
        organization is in. This is the same reasoning, and the same shape, as the
        accounting module's statutory numbering.

        A PostgreSQL ``SEQUENCE`` was rejected for the same reason it was rejected
        there: sequences deliberately do not roll back, so a posting that failed
        after allocating would burn a number permanently, and a gap here is not
        cosmetic - it would silently exclude a leaf from every batch.
        """
        # Import here rather than at module scope: the accounting and organization
        # modules must not become import-time dependencies of this one, so that
        # the proof ledger stays removable.
        from app.modules.organizations.models import Organization

        await self.session.execute(
            select(Organization.id).where(Organization.id == organization_id).with_for_update()
        )

        highest = (
            await self.session.execute(
                select(func.max(SealLeaf.leaf_seq)).where(
                    SealLeaf.organization_id == organization_id
                )
            )
        ).scalar_one_or_none()

        return int(highest or 0) + 1

    async def for_entry(self, journal_entry_id: uuid.UUID) -> SealLeaf | None:
        return await self.get_by(journal_entry_id=journal_entry_id)

    async def unsealed(
        self, organization_id: uuid.UUID, *, limit: int | None = None
    ) -> Sequence[SealLeaf]:
        """The backlog, in posting order.

        Ordered by ``leaf_seq`` and not by anything else. The tree's shape depends
        on this order, so it must be total, deterministic, and independent of
        insertion timing - which ``created_at`` is not, since two entries posted in
        the same millisecond would tie and the tie would be broken differently on
        different runs.
        """
        query = (
            select(SealLeaf)
            .where(
                SealLeaf.organization_id == organization_id,
                SealLeaf.seal_id.is_(None),
            )
            .order_by(SealLeaf.leaf_seq.asc())
        )
        if limit is not None:
            query = query.limit(limit)
        return (await self.session.execute(query)).scalars().all()

    async def unsealed_count(self, organization_id: uuid.UUID) -> int:
        return await self.count(
            SealLeaf.organization_id == organization_id,
            SealLeaf.seal_id.is_(None),
        )

    async def oldest_unsealed_at(self, organization_id: uuid.UUID) -> dt.datetime | None:
        """When the oldest unsealed entry was posted.

        Drives the "unsealed for 6 days" warning on the Trust screen. A backlog
        that is growing is the failure mode that matters here: sealing silently
        stopping looks identical to sealing being switched off, and only the age of
        the backlog distinguishes them.
        """
        return (
            await self.session.execute(
                select(func.min(SealLeaf.created_at)).where(
                    SealLeaf.organization_id == organization_id,
                    SealLeaf.seal_id.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def for_seal(self, seal_id: uuid.UUID) -> Sequence[SealLeaf]:
        """Every leaf a seal covers, in tree order.

        ``leaf_index`` and not ``leaf_seq``: they agree today, and the stored index
        is what the tree was actually built from. Ordering by the sequence instead
        would re-derive the shape rather than reproduce it - see the note on
        :attr:`SealLeaf.leaf_index`.
        """
        return (
            (
                await self.session.execute(
                    select(SealLeaf)
                    .where(SealLeaf.seal_id == seal_id)
                    .order_by(SealLeaf.leaf_index.asc())
                )
            )
            .scalars()
            .all()
        )

    async def assign_to_seal(self, leaves: Sequence[SealLeaf], seal_id: uuid.UUID) -> None:
        """Stamp a batch's leaves with their seal and their position in its tree.

        Assigned in the order given, which the caller has already fixed by
        ``leaf_seq``. Done as ORM assignment rather than a bulk ``UPDATE`` so the
        partial index and the ``sealed_leaf_has_index`` constraint see both columns
        set in the same statement.
        """
        for index, leaf in enumerate(leaves):
            leaf.seal_id = seal_id
            leaf.leaf_index = index
        await self.session.flush()

    async def release_from_seal(self, seal_id: uuid.UUID) -> None:
        """Detach a failed seal's leaves so a replacement can cover them.

        Both columns are cleared together, because the check constraint requires
        them to agree. A leaf released here goes straight back to the head of the
        backlog with its original ``leaf_seq``, so the replacement batch covers
        exactly the same range - which is what lets the retry reuse the sequence
        number the chain is still expecting.
        """
        await self.session.execute(
            update(SealLeaf)
            .where(SealLeaf.seal_id == seal_id)
            .values(seal_id=None, leaf_index=None)
        )
        await self.session.flush()


class SealRepository(BaseRepository[Seal]):
    model = Seal
    sortable_fields = frozenset({"seq", "created_at", "sealed_at"})
    default_sort = "-seq"

    async def latest_confirmed(self, organization_id: uuid.UUID) -> Seal | None:
        """The newest confirmed seal - the local view of the chain's head.

        Confirmed only. A pending or submitted seal has not moved the contract's
        ``head``, so chaining a new seal from its root would be refused, and
        chaining from the last *confirmed* root is what the contract will actually
        accept.
        """
        return (
            await self.session.execute(
                select(Seal)
                .where(
                    Seal.organization_id == organization_id,
                    Seal.status == SealStatus.CONFIRMED,
                )
                .order_by(Seal.seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def highest_live_seq(self, organization_id: uuid.UUID) -> int:
        """The highest sequence number not belonging to a failed seal.

        What the next seal's ``seq`` is derived from. Failed rows are excluded so a
        permanent failure at 7 lets the replacement reuse 7 - the contract's
        ``head`` never moved, so 7 remains the only number it will accept, and
        skipping to 8 would be refused forever.
        """
        highest = (
            await self.session.execute(
                select(func.max(Seal.seq)).where(
                    Seal.organization_id == organization_id,
                    Seal.status != SealStatus.FAILED,
                )
            )
        ).scalar_one_or_none()
        return int(highest or 0)

    async def by_seq(self, organization_id: uuid.UUID, seq: int) -> Seal | None:
        return (
            await self.session.execute(
                select(Seal)
                .where(
                    Seal.organization_id == organization_id,
                    Seal.seq == seq,
                    Seal.status != SealStatus.FAILED,
                )
                .limit(1)
            )
        ).scalar_one_or_none()

    async def open_work(self, *, limit: int = 50) -> Sequence[Seal]:
        """Every seal across every organization still owed an outcome.

        The worker's queue. Ordered by creation so the oldest backlog drains
        first, and by ``seq`` within an organization because a chain must be
        extended in order - submitting seal 9 before seal 8 has confirmed would be
        refused by the contract, wasting a fee to learn something the ordering
        already knew.
        """
        return (
            (
                await self.session.execute(
                    select(Seal)
                    .where(Seal.status.in_((SealStatus.PENDING, SealStatus.SUBMITTED)))
                    .order_by(Seal.organization_id.asc(), Seal.seq.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def has_open_work(self, organization_id: uuid.UUID) -> bool:
        """Whether this organization already has a seal in flight.

        Checked before creating another. Two pending seals would both be built
        against the same confirmed head, so they would carry the same ``prev`` and
        the same ``seq``, and the second would be refused - after the leaves had
        already been split between them.
        """
        return await self.exists_open(organization_id)

    async def exists_open(self, organization_id: uuid.UUID) -> bool:
        count = (
            await self.session.execute(
                select(func.count())
                .select_from(Seal)
                .where(
                    Seal.organization_id == organization_id,
                    Seal.status.in_((SealStatus.PENDING, SealStatus.SUBMITTED)),
                )
            )
        ).scalar_one()
        return bool(count)

    def _org_query(self, organization_id: uuid.UUID) -> Select[tuple[Seal]]:
        return select(Seal).where(Seal.organization_id == organization_id)

    async def page(self, organization_id: uuid.UUID, params: CursorParams) -> Sequence[Seal]:
        """Cursor-paginated seal history, newest first.

        Paged on ``seq`` rather than on the UUID cursor the base repository uses,
        because ``seq`` is the chain's own ordering and it is what the screen
        displays. A cursor over ids would be correct but would sort by *creation*,
        and a seal created out of order after a failure would appear in the wrong
        place in a list whose whole purpose is showing an unbroken sequence.
        """
        query = self._org_query(organization_id).order_by(Seal.seq.desc())
        if (cursor := params.decoded_cursor) is not None and cursor.isdigit():
            query = query.where(Seal.seq < int(cursor))
        return (await self.session.execute(query.limit(params.limit + 1))).scalars().all()

    async def confirmed_count(self, organization_id: uuid.UUID) -> int:
        return await self.count(
            Seal.organization_id == organization_id,
            Seal.status == SealStatus.CONFIRMED,
        )

    async def totals(self, organization_id: uuid.UUID) -> tuple[int, int]:
        """``(entries_sealed, seals_confirmed)`` for the Trust screen's headline."""
        row = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(Seal.entry_count), 0),
                    func.count(),
                ).where(
                    Seal.organization_id == organization_id,
                    Seal.status == SealStatus.CONFIRMED,
                )
            )
        ).one()
        return int(row[0]), int(row[1])
