"""API contracts for feedback and usage."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from pydantic import Field, StringConstraints

from app.core.schemas import BaseSchema, ResponseSchema
from app.modules.feedback.models import FeedbackKind, FeedbackStatus

#: The message. Long enough for a real description, short enough that the endpoint
#: is not a place to store a document.
MessageStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=4000)]


class SubmitFeedbackRequest(BaseSchema):
    """A message from a user, signed in or not."""

    kind: FeedbackKind = FeedbackKind.PROBLEM
    message: MessageStr

    #: Optional, because a rating alone is a signal and a comment alone is a story,
    #: and demanding both means getting neither.
    rating: Annotated[int | None, Field(ge=1, le=5)] = None

    #: Which screen they were on. Sent by the client rather than derived from the
    #: ``Referer``, which is stripped by enough browsers to be unreliable.
    screen: Annotated[str | None, StringConstraints(max_length=120)] = None

    #: Only used when nobody is signed in - otherwise the account's address wins,
    #: because a person reporting a bug should not have to get their own email
    #: right for us to be able to reply.
    contact_email: Annotated[str | None, StringConstraints(max_length=320)] = None


class TriageFeedbackRequest(BaseSchema):
    status: FeedbackStatus
    note: Annotated[str | None, StringConstraints(max_length=2000)] = None


class TrackEventRequest(BaseSchema):
    """One usage event.

    ``action`` is validated against a closed set server-side rather than by a
    Literal here: the client is not the authority on what the product counts, and a
    Literal would mean a schema change every time a screen is added.
    """

    action: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)]
    surface: Annotated[str | None, StringConstraints(max_length=20)] = None

    #: Allow-listed keys only, filtered server-side. Anything else is dropped
    #: silently - see ``ALLOWED_CONTEXT_KEYS``.
    context: dict[str, Any] = Field(default_factory=dict)


class FeedbackRead(ResponseSchema):
    id: uuid.UUID
    kind: FeedbackKind
    status: FeedbackStatus
    message: str
    rating: int | None
    screen: str | None
    contact_email: str | None
    app_version: str | None
    request_id: str | None
    triage_note: str | None
    organization_id: uuid.UUID | None
    created_at: dt.datetime


class FeedbackSummaryRead(ResponseSchema):
    total: int
    by_kind: dict[str, int]
    by_status: dict[str, int]

    #: Null when nobody has rated anything. Travels with :attr:`rated_count` so the
    #: UI can decline to present an average of three responses as a fact.
    average_rating: float | None
    rated_count: int


class UsageActionRead(ResponseSchema):
    action: str
    events: int
    organizations: int
    users: int


class UsageRollupRead(ResponseSchema):
    days: int
    since: str
    active_organizations: int
    active_users: int
    actions: list[UsageActionRead]
