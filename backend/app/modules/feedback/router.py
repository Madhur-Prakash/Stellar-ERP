"""Feedback and usage endpoints.

``POST /feedback`` is **unauthenticated**, and that is the point of the feature:
the most useful message in any product comes from somebody who could not get in.
Requiring a session would silence exactly that person.

It is rate-limited, obviously. An open text endpoint on a public host is a spam
target, and the limit is per IP rather than per user because there is no user.

The read side is superuser-only. Feedback is about the *product*, so the person
who needs to read it is whoever maintains the install - not whoever is signed in,
and not scoped to one organization.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status

from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.core.schemas import MessageResponse
from app.modules.auth.dependencies import (
    DbSession,
    OptionalUser,
    RequestCtx,
    require_superuser,
)
from app.modules.feedback.models import FeedbackStatus
from app.modules.feedback.schemas import (
    FeedbackRead,
    FeedbackSummaryRead,
    SubmitFeedbackRequest,
    TrackEventRequest,
    TriageFeedbackRequest,
    UsageRollupRead,
)
from app.modules.feedback.service import PUBLIC_ACTIONS, FeedbackService, UsageService

log = get_logger(__name__)

router = APIRouter(prefix="/feedback", tags=["Feedback"])

#: Budget for the open submit endpoint. Tight, because it is unauthenticated and
#: writes a row; generous enough that a person reporting three related problems in
#: a row is not blocked mid-sentence.
SUBMIT_LIMIT = "10/hour"

#: Usage events are fired on navigation, so the budget is per-screen-change rather
#: than per-action-a-human-takes.
TRACK_LIMIT = "240/minute"


def get_feedback(session: DbSession) -> FeedbackService:
    return FeedbackService(session)


def get_usage(session: DbSession) -> UsageService:
    return UsageService(session)


FeedbackDep = Annotated[FeedbackService, Depends(get_feedback)]
UsageDep = Annotated[UsageService, Depends(get_usage)]


@router.post(
    "",
    response_model=FeedbackRead,
    status_code=status.HTTP_201_CREATED,
    summary="Send feedback",
)
@limiter.limit(SUBMIT_LIMIT)
async def submit(
    request: Request,
    payload: SubmitFeedbackRequest,
    service: FeedbackDep,
    user: OptionalUser,
    ctx: RequestCtx,
) -> FeedbackRead:
    """Accept a message from anybody, signed in or not.

    ``OptionalUser`` rather than ``CurrentUser``: a token is used when present, to
    attach the message to a person and skip asking for their email, and its absence
    is not an error.
    """
    entry = await service.submit(
        kind=payload.kind,
        message=payload.message,
        rating=payload.rating,
        screen=payload.screen,
        contact_email=payload.contact_email,
        user=user,
        # The organization comes from the token when there is one; a signed-in user
        # cannot claim a different one, and an anonymous one cannot claim any.
        organization_id=getattr(user, "last_organization_id", None) if user else None,
        ctx=ctx,
    )
    return FeedbackRead.model_validate(entry)


@router.post(
    "/track",
    response_model=MessageResponse,
    summary="Record a usage event",
)
@limiter.limit(TRACK_LIMIT)
async def track(
    request: Request,
    payload: TrackEventRequest,
    service: UsageDep,
    user: OptionalUser,
) -> MessageResponse:
    """Record one thing the caller did.

    ``OptionalUser`` rather than ``CurrentUser``, and the reason is the verifier.
    Most events need a session, because an event with no organization cannot answer
    "how many *businesses* are doing this". But a verification is performed by a
    bank or an auditor who has no account here and never will, and requiring a login
    to count those would guarantee the answer was always zero - so a small
    allow-list (``PUBLIC_ACTIONS``) is accepted anonymously.

    An authenticated caller's organization comes from their **token**, never from
    the request body. A client cannot attribute its activity to somebody else's
    organization, which is the same rule every other endpoint here follows.

    Always answers 200 - even when the event is dropped for being unknown, for
    needing a session it does not have, or for analytics being switched off.
    Telemetry must never be able to fail the thing it is measuring, and a client
    that got a 4xx here would either retry or surface an error to a user about a
    feature they never asked for.
    """
    if not settings.usage_analytics_enabled:
        return MessageResponse(message="Usage analytics is disabled on this server.")

    if user is None and payload.action not in PUBLIC_ACTIONS:
        return MessageResponse(message="Ignored: that event needs a signed-in user.")

    await service.record(
        payload.action,
        user=user,
        organization_id=getattr(user, "last_organization_id", None) if user else None,
        surface=payload.surface,
        context=payload.context,
    )
    return MessageResponse(message="Recorded.")


@router.get(
    "/summary",
    response_model=FeedbackSummaryRead,
    summary="Feedback counts",
)
async def summary(
    service: FeedbackDep,
    _: Annotated[None, Depends(require_superuser())],
) -> FeedbackSummaryRead:
    return FeedbackSummaryRead(**await service.summary())


@router.get("/inbox", response_model=list[FeedbackRead], summary="Read feedback")
async def inbox(
    service: FeedbackDep,
    _: Annotated[None, Depends(require_superuser())],
    state: Annotated[FeedbackStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[FeedbackRead]:
    entries = await service.inbox(status=state, limit=limit)
    return [FeedbackRead.model_validate(entry) for entry in entries]


@router.patch(
    "/{feedback_id}",
    response_model=FeedbackRead,
    summary="Triage one message",
)
async def triage(
    feedback_id: Annotated[uuid.UUID, Path()],
    payload: TriageFeedbackRequest,
    service: FeedbackDep,
    _: Annotated[None, Depends(require_superuser())],
) -> FeedbackRead:
    entry = await service.triage(feedback_id, status=payload.status, note=payload.note)
    return FeedbackRead.model_validate(entry)


@router.get(
    "/usage",
    response_model=UsageRollupRead,
    summary="What is being used",
)
async def usage(
    service: UsageDep,
    _: Annotated[None, Depends(require_superuser())],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> UsageRollupRead:
    """Actions and distinct organizations over a window.

    Deliberately one small rollup rather than a query builder. It answers what is
    being used and by how many businesses; anything richer belongs in whatever the
    operator already uses to query their own PostgreSQL.
    """
    return UsageRollupRead(**await service.rollup(days=days))
