"""Generic repository base.

The repository layer is the *only* place that touches the SQLAlchemy session.
Services receive repositories, so their business rules can be unit-tested
against a fake without a database.

Two invariants this base enforces so no subclass has to remember them:

* **Soft-deleted rows are invisible by default.** Every query filters
  ``deleted_at IS NULL`` unless explicitly told otherwise. Forgetting this
  filter in one query is how "deleted" customers reappear on an invoice.
* **Sort fields are allow-listed.** ``sort_by`` arrives from a query string;
  interpolating it into ``ORDER BY`` is an injection vector, so it is resolved
  against the mapped columns a subclass opts in to.

No ``commit()`` calls here. The request-scoped transaction in
:func:`app.db.session.get_db` owns that boundary.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any, ClassVar, cast

from sqlalchemy import CursorResult, Result, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.pagination import CursorParams, PageParams, SortParams
from app.db.base import Base


def rows_affected(result: Result[Any]) -> int:
    """Row count from a bulk ``UPDATE``/``DELETE``.

    ``AsyncSession.execute`` is typed as returning ``Result``, which exposes no
    ``rowcount`` - but a DML statement actually returns a ``CursorResult``, which
    does. The cast records that fact in one place instead of at every bulk-write
    call site.

    Only valid for DML. Calling it on a ``SELECT`` result is meaningless.
    """
    return int(cast("CursorResult[Any]", result).rowcount or 0)


class BaseRepository[ModelT: Base]:
    """CRUD and query plumbing shared by every concrete repository."""

    model: type[ModelT]

    #: Columns permitted in ``ORDER BY``. Empty means sorting is not offered.
    sortable_fields: ClassVar[frozenset[str]] = frozenset()

    #: Default ordering when the caller supplies none. ``-`` prefix means DESC.
    default_sort: ClassVar[str] = "-created_at"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------------------------------------------------------------------------
    # Query construction
    # -------------------------------------------------------------------------
    @property
    def _supports_soft_delete(self) -> bool:
        return hasattr(self.model, "deleted_at")

    def _base_query(self, *, include_deleted: bool = False) -> Select[tuple[ModelT]]:
        query = select(self.model)
        if self._supports_soft_delete and not include_deleted:
            query = query.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return query

    def _apply_sort(
        self, query: Select[tuple[ModelT]], sort: SortParams | None
    ) -> Select[tuple[ModelT]]:
        """Apply an allow-listed sort, falling back to :attr:`default_sort`."""
        field, descending = self._resolve_sort(sort)
        column = getattr(self.model, field, None)
        if column is None:
            return query
        return query.order_by(column.desc() if descending else column.asc())

    def _resolve_sort(self, sort: SortParams | None) -> tuple[str, bool]:
        if sort and sort.sort_by and sort.sort_by in self.sortable_fields:
            return sort.sort_by, sort.sort_dir == "desc"

        default = self.default_sort
        if default.startswith("-"):
            return default[1:], True
        return default, False

    # -------------------------------------------------------------------------
    # Reads
    # -------------------------------------------------------------------------
    async def get(self, entity_id: uuid.UUID, *, include_deleted: bool = False) -> ModelT | None:
        """Fetch by primary key. ``None`` when absent - the service decides
        whether that is a :class:`~app.core.exceptions.NotFoundError`."""
        query = self._base_query(include_deleted=include_deleted).where(
            self.model.id == entity_id  # type: ignore[attr-defined]
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_by(self, **filters: Any) -> ModelT | None:
        """Fetch a single row by equality filters."""
        query = self._base_query()
        for field, value in filters.items():
            query = query.where(getattr(self.model, field) == value)
        return (await self.session.execute(query.limit(1))).scalar_one_or_none()

    async def exists(self, **filters: Any) -> bool:
        """Existence check that never materialises a row."""
        query = select(func.count()).select_from(self.model)
        if self._supports_soft_delete:
            query = query.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        for field, value in filters.items():
            query = query.where(getattr(self.model, field) == value)
        return bool((await self.session.execute(query.limit(1))).scalar_one())

    async def count(self, *where: ColumnElement[bool]) -> int:
        query = select(func.count()).select_from(self.model)
        if self._supports_soft_delete:
            query = query.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        for clause in where:
            query = query.where(clause)
        return int((await self.session.execute(query)).scalar_one())

    async def list_all(
        self,
        *where: ColumnElement[bool],
        sort: SortParams | None = None,
        limit: int | None = None,
    ) -> Sequence[ModelT]:
        query = self._apply_sort(self._base_query(), sort)
        for clause in where:
            query = query.where(clause)
        if limit is not None:
            query = query.limit(limit)
        return (await self.session.execute(query)).scalars().all()

    async def paginate(
        self,
        params: PageParams,
        *where: ColumnElement[bool],
        sort: SortParams | None = None,
    ) -> tuple[Sequence[ModelT], int]:
        """Offset-paginate, returning ``(rows, total)``.

        Two queries by necessity: the total is what makes "page 7 of 12"
        possible, and it cannot be derived from a limited result set.
        """
        query = self._apply_sort(self._base_query(), sort)
        count_query = select(func.count()).select_from(self.model)
        if self._supports_soft_delete:
            count_query = count_query.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]

        for clause in where:
            query = query.where(clause)
            count_query = count_query.where(clause)

        total = int((await self.session.execute(count_query)).scalar_one())
        rows = (
            (await self.session.execute(query.offset(params.offset).limit(params.limit)))
            .scalars()
            .all()
        )
        return rows, total

    async def paginate_cursor(
        self,
        params: CursorParams,
        *where: ColumnElement[bool],
    ) -> Sequence[ModelT]:
        """Cursor-paginate newest-first, over-fetching one row.

        Relies on UUIDv7 ids being time-ordered, so ``id < cursor`` descending is
        both a stable chronological sort and a primary-key seek. The extra row
        tells :meth:`app.core.pagination.CursorPage.create` whether more exist.
        """
        query = self._base_query().order_by(self.model.id.desc())  # type: ignore[attr-defined]
        for clause in where:
            query = query.where(clause)

        if (cursor := params.decoded_cursor) is not None:
            # Malformed cursor -> first page, consistent with CursorParams.
            with contextlib.suppress(ValueError):
                query = query.where(self.model.id < uuid.UUID(cursor))  # type: ignore[attr-defined]

        return (await self.session.execute(query.limit(params.limit + 1))).scalars().all()

    # -------------------------------------------------------------------------
    # Writes
    # -------------------------------------------------------------------------
    async def add(self, entity: ModelT) -> ModelT:
        """Stage an insert and flush it.

        Flush, not commit: it assigns the primary key and surfaces constraint
        violations here - where the service can translate them into a domain
        error - rather than at the end of the request.
        """
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def add_all(self, entities: Sequence[ModelT]) -> Sequence[ModelT]:
        self.session.add_all(list(entities))
        await self.session.flush()
        return entities

    async def update(self, entity: ModelT, **values: Any) -> ModelT:
        """Assign attributes and flush. Unknown attribute names are ignored so a
        caller cannot inject arbitrary column writes from request data."""
        for field, value in values.items():
            if hasattr(entity, field):
                setattr(entity, field, value)
        await self.session.flush()
        return entity

    async def soft_delete(self, entity: ModelT) -> ModelT:
        """Mark as deleted, preserving the row."""
        if not self._supports_soft_delete:
            raise TypeError(f"{self.model.__name__} does not support soft deletion")
        entity.deleted_at = dt.datetime.now(dt.UTC)  # type: ignore[attr-defined]
        await self.session.flush()
        return entity

    async def hard_delete(self, entity: ModelT) -> None:
        """Physically remove the row. Reserved for genuinely transient data
        (expired tokens, stale sessions) - never for business records."""
        await self.session.delete(entity)
        await self.session.flush()
