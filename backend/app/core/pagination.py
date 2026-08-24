"""Pagination contracts.

Two strategies, because they solve different problems:

* **Offset pagination** (:class:`PageParams`) - supports "jump to page 7", which
  data tables need. Cost: ``OFFSET 10000`` still walks 10 000 rows, and a row
  inserted mid-browse shifts everything down a page.
* **Cursor pagination** (:class:`CursorParams`) - constant cost at any depth and
  stable under concurrent inserts, at the price of no random page access. This
  is the right default for activity feeds, audit trails, and infinite scroll.

UUIDv7 primary keys make cursors cheap: keys are time-ordered, so ``id > cursor``
is both a stable sort and a seek on the primary key index. No composite
``(created_at, id)`` cursor needed.
"""

from __future__ import annotations

import base64
import binascii
import math
from collections.abc import Sequence
from typing import Annotated, Literal, Self

from fastapi import Query
from pydantic import BaseModel, Field, computed_field

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 25


class PageParams(BaseModel):
    """Offset pagination query parameters."""

    page: Annotated[int, Query(ge=1, description="1-indexed page number")] = 1
    page_size: Annotated[
        int,
        Query(ge=1, le=MAX_PAGE_SIZE, description=f"Items per page (max {MAX_PAGE_SIZE})"),
    ] = DEFAULT_PAGE_SIZE

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


class SortParams(BaseModel):
    """Sorting query parameters.

    ``sort_by`` is validated against an allow-list at the repository, never
    interpolated into SQL - otherwise it is an injection point.
    """

    sort_by: Annotated[str | None, Query(description="Field to sort by")] = None
    sort_dir: Annotated[Literal["asc", "desc"], Query(description="Sort direction")] = "desc"


class PageMeta(BaseModel):
    """Envelope metadata for an offset-paginated response."""

    page: int
    page_size: int
    total_items: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(self.total_items / self.page_size))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_previous(self) -> bool:
        return self.page > 1


class Page[T](BaseModel):
    """Offset-paginated response envelope.

    Generic so routes declare ``Page[UserRead]`` and OpenAPI documents the real
    item schema instead of a bare object.
    """

    items: list[T]
    meta: PageMeta

    @classmethod
    def create(cls, items: Sequence[T], *, total: int, params: PageParams) -> Self:
        return cls(
            items=list(items),
            meta=PageMeta(page=params.page, page_size=params.page_size, total_items=total),
        )


# =============================================================================
# Cursor pagination
# =============================================================================
class CursorParams(BaseModel):
    """Cursor pagination query parameters."""

    cursor: Annotated[str | None, Query(description="Opaque cursor from the previous page")] = None
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE

    @property
    def decoded_cursor(self) -> str | None:
        """The underlying id, or ``None`` if absent or malformed.

        A bad cursor degrades to "first page" rather than raising: cursors are
        opaque to clients, so a 400 here is unactionable noise, and callers
        commonly truncate them in URLs.
        """
        if not self.cursor:
            return None
        try:
            return base64.urlsafe_b64decode(self.cursor.encode()).decode()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None


def encode_cursor(value: str) -> str:
    """Base64url-encode a cursor value.

    Encoded purely to signal opacity - clients that see a raw UUID inevitably
    start constructing cursors by hand, which locks in the implementation.
    """
    return base64.urlsafe_b64encode(value.encode()).decode()


class CursorPage[T](BaseModel):
    """Cursor-paginated response envelope."""

    items: list[T]
    next_cursor: str | None = Field(
        default=None, description="Pass as `cursor` for the next page; null when exhausted"
    )
    has_more: bool = False

    @classmethod
    def create(cls, items: Sequence[T], *, limit: int, cursor_of: str | None = None) -> Self:
        """Build the envelope from an over-fetched result set.

        The repository fetches ``limit + 1`` rows; the extra row is the signal
        that another page exists and is dropped from the response. One query, no
        separate ``COUNT``.
        """
        rows = list(items)
        has_more = len(rows) > limit
        page = rows[:limit] if has_more else rows

        next_cursor = encode_cursor(cursor_of) if (has_more and cursor_of) else None
        return cls(items=page, next_cursor=next_cursor, has_more=has_more)
