"""What users tell us - deliberately, and by what they do.

Two tables, and they belong together because they answer one question from two
directions: **is this product working for the people using it?**

* :class:`Feedback` - what somebody typed into the feedback box. Sparse, biased
  towards the annoyed and the delighted, and the only source of *why*.
* :class:`UsageEvent` - which screens get opened and which actions get taken.
  Dense, unbiased, and silent about why.

Neither is worth much alone. "Sealing is confusing" is one person's opinion until
you can see that forty of them opened the Trust screen and never pressed the
button.

Why this is first-party and lives in PostgreSQL
----------------------------------------------
The obvious answer is a hosted analytics script in the browser. It is the wrong
answer for *this* product specifically: the entire pitch is that a business's
books stay on its own server, and shipping every user's navigation to a third
party would contradict that on the same page that promises it. An operator who
self-hosted this to get away from a vendor would be entitled to be angry.

So events go in the operator's own database, in a table they can read, truncate,
or switch off with one setting. The cost is that there is no funnel-analysis UI -
which is a real cost, and cheaper than the alternative.

What is never recorded
----------------------
:class:`UsageEvent` carries an action name, an organization, and a user. It does
**not** carry a customer name, an amount, an entry number, an account, or a URL
with an id in it. The rule is enforced by shape rather than by discipline: there
is no free-text payload column an eager future caller could put an invoice total
into. :attr:`UsageEvent.context` is JSONB, but the service that writes it
allow-lists the keys - see :mod:`app.modules.feedback.service`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.modules.organizations.models import Organization
    from app.modules.users.models import User


class FeedbackKind(StrEnum):
    """What sort of message this is.

    A closed vocabulary, so the inbox can be triaged rather than read end to end.
    Deliberately short: a longer list makes people think about which box to tick
    instead of what they wanted to say, and the free text is the valuable part.
    """

    PROBLEM = "problem"
    IDEA = "idea"
    PRAISE = "praise"
    QUESTION = "question"


class FeedbackStatus(StrEnum):
    """Triage state. ``NEW`` until somebody has actually read it."""

    NEW = "new"
    READ = "read"
    ACTIONED = "actioned"
    DECLINED = "declined"


class Feedback(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One message from one person.

    Organization and user are both nullable, and for a reason that matters: the
    most useful feedback often comes from somebody who could not get *in*. A
    person stuck on registration has no session and no organization, and refusing
    their message because of a foreign key would silence exactly the report worth
    hearing.
    """

    kind: Mapped[FeedbackKind] = mapped_column(
        enum_column(FeedbackKind, length=20), nullable=False, index=True
    )
    status: Mapped[FeedbackStatus] = mapped_column(
        enum_column(FeedbackStatus, length=20),
        nullable=False,
        default=FeedbackStatus.NEW,
        index=True,
    )

    #: The message. Required, and the only required field.
    message: Mapped[str] = mapped_column(Text, nullable=False)

    #: 1-5, optional. A rating with no comment is a signal; a comment with no
    #: rating is a story. Both are accepted alone.
    rating: Mapped[int | None] = mapped_column(Integer)

    #: Which screen they were on. Recorded because "the numbers are wrong" means
    #: something different on the dashboard than on the trial balance, and asking
    #: costs a round trip the person will not make.
    screen: Mapped[str | None] = mapped_column(String(120))

    # --- Who, if known ---
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    #: Denormalised so the message stays answerable after the account is gone -
    #: and so somebody who was never signed in can leave a way to be replied to.
    contact_email: Mapped[str | None] = mapped_column(String(320))

    # --- Context ---
    user_agent: Mapped[str | None] = mapped_column(String(500))
    app_version: Mapped[str | None] = mapped_column(String(40))
    #: Correlates with the logifyx stream, so a "it crashed" report can be joined
    #: to the stack trace that caused it.
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)

    #: What a maintainer wrote back to themselves. Not shown to the reporter.
    triage_note: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User | None] = relationship(lazy="raise")
    organization: Mapped[Organization | None] = relationship(lazy="raise")

    __table_args__ = (
        # The inbox: newest unread first.
        Index("ix_feedback_status_created", "status", "created_at"),
    )


class UsageEvent(Base, UUIDPrimaryKeyMixin):
    """One thing somebody did.

    No :class:`~app.db.base.TimestampMixin`: an ``updated_at`` on an event would
    imply it can change, and an event is a fact about a moment. :attr:`created_at`
    is declared explicitly below.

    Deliberately **not** a general-purpose event log. There is no ``payload``
    column, and :attr:`context` is written only through
    :meth:`~app.modules.feedback.service.UsageService.record`, which allow-lists
    its keys. An events table with an open payload becomes a place where somebody
    eventually logs an invoice total "just for debugging", and then the analytics
    table is inside the compliance boundary.
    """

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    #: A dotted action name from a closed set the service validates -
    #: ``screen.trust``, ``seal.now``, ``proof.export``. Not a free string: a typo
    #: creates a metric nobody will ever search for, which is the same reason the
    #: audit trail uses an enum.
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )

    #: ``web`` or ``desktop``. Worth knowing which client an action came from,
    #: because a screen that works in a browser and not in a native window is a
    #: bug class of its own.
    surface: Mapped[str | None] = mapped_column(String(20))

    #: Allow-listed keys only. See the class docstring.
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    __table_args__ = (
        # "What has this organization been doing?" - the only query that runs.
        Index("ix_usage_event_org_created", "organization_id", "created_at"),
        Index("ix_usage_event_action_created", "action", "created_at"),
    )
