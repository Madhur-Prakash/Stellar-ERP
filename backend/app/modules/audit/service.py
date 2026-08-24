"""Audit trail writer and reader.

Every service that changes state records what happened here. The write joins the
caller's transaction deliberately: if the action rolls back, its audit row must
roll back too, or the trail describes events that never occurred.

The audit row is *not* a substitute for the logifyx log, and vice versa. The log
is operational and ephemeral (debugging, latency, stack traces); the audit trail
is a durable, queryable business record - "who changed this customer's credit
limit, and when". They share a ``request_id`` so one can be pivoted to the other.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import current_log_context, get_logger
from app.core.pagination import CursorParams
from app.modules.audit.models import AuditAction, AuditLog, AuditSeverity
from app.modules.users.models import User

log = get_logger(__name__)

#: Never persisted into ``changes``, even if a caller passes them in. A diff is
#: written from request payloads, so this is the backstop against a password or
#: token landing in a table designed to be read by auditors.
_REDACTED_FIELDS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "password_hash",
        "token",
        "refresh_token",
        "access_token",
        "token_hash",
        "totp_secret",
        "secret",
        "recovery_codes",
        "recovery_code_hashes",
        "api_key",
        "client_secret",
        "encryption_key",
    }
)

#: Actions a security dashboard should surface. Anything not listed is INFO.
_SEVERITY_OVERRIDES: dict[AuditAction, AuditSeverity] = {
    AuditAction.USER_LOGIN_FAILED: AuditSeverity.WARNING,
    AuditAction.USER_LOCKED_OUT: AuditSeverity.CRITICAL,
    AuditAction.TWO_FACTOR_CHALLENGE_FAILED: AuditSeverity.WARNING,
    AuditAction.TWO_FACTOR_DISABLED: AuditSeverity.WARNING,
    AuditAction.TWO_FACTOR_RECOVERY_CODE_USED: AuditSeverity.WARNING,
    AuditAction.SESSION_REUSE_DETECTED: AuditSeverity.CRITICAL,
    AuditAction.SESSION_ALL_REVOKED: AuditSeverity.WARNING,
    AuditAction.ORG_DELETED: AuditSeverity.CRITICAL,
    AuditAction.MEMBER_REMOVED: AuditSeverity.WARNING,
    AuditAction.MEMBER_ROLE_CHANGED: AuditSeverity.WARNING,
    AuditAction.ROLE_DELETED: AuditSeverity.WARNING,
    AuditAction.USER_PASSWORD_CHANGED: AuditSeverity.WARNING,
    # The moment a machine-read figure becomes money owed to a supplier.
    AuditAction.DOCUMENT_CONFIRMED: AuditSeverity.WARNING,
}


def redact(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip secret-bearing keys from a payload."""
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in _REDACTED_FIELDS:
            cleaned[key] = "[redacted]"
        elif isinstance(value, dict):
            cleaned[key] = redact(value)
        else:
            cleaned[key] = value
    return cleaned


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Build a field-level diff of only what actually changed.

    Recording unchanged fields would bury the one edit that matters under thirty
    that did not.
    """
    changes: dict[str, Any] = {}
    for key, new_value in after.items():
        old_value = before.get(key)
        if old_value != new_value:
            changes[key] = {"before": old_value, "after": new_value}
    return redact(changes)


class AuditService:
    """Records and queries audit events."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        action: AuditAction,
        *,
        actor: User | None = None,
        organization_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | uuid.UUID | None = None,
        summary: str | None = None,
        changes: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        severity: AuditSeverity | None = None,
    ) -> AuditLog:
        """Append an event to the trail.

        Actor identity is denormalised onto the row (email and display name) so
        the entry stays readable after the user is deleted - a foreign key alone
        would leave "someone did this".
        """
        entry = AuditLog(
            action=action,
            severity=severity or _SEVERITY_OVERRIDES.get(action, AuditSeverity.INFO),
            summary=summary,
            actor_user_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
            actor_label=actor.full_name if actor else None,
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            ip_address=ip_address,
            user_agent=user_agent,
            # Ties this row to the operational log line for the same request.
            request_id=current_log_context().get("request_id"),
            changes=redact(changes or {}),
            context=redact(context or {}),
        )
        self.session.add(entry)
        # Flush, not commit: the row lives or dies with the caller's transaction.
        await self.session.flush()

        log.info(
            "audit event recorded",
            extra={
                "action": action.value,
                "actor_id": str(actor.id) if actor else None,
                "organization_id": str(organization_id) if organization_id else None,
                "resource": f"{resource_type}:{resource_id}" if resource_type else None,
            },
        )
        return entry

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        params: CursorParams,
        *,
        action: AuditAction | None = None,
        actor_user_id: uuid.UUID | None = None,
        severity: AuditSeverity | None = None,
        resource_type: str | None = None,
    ) -> Sequence[AuditLog]:
        """Cursor-paginated trail for one organization, newest first.

        Cursor rather than offset: the audit view is an append-heavy feed, where
        ``OFFSET`` degrades with depth and shifts rows under the reader as new
        events arrive.
        """
        query = (
            select(AuditLog)
            .where(AuditLog.organization_id == organization_id)
            .order_by(AuditLog.id.desc())
        )

        if action is not None:
            query = query.where(AuditLog.action == action)
        if actor_user_id is not None:
            query = query.where(AuditLog.actor_user_id == actor_user_id)
        if severity is not None:
            query = query.where(AuditLog.severity == severity)
        if resource_type is not None:
            query = query.where(AuditLog.resource_type == resource_type)

        if (cursor := params.decoded_cursor) is not None:
            # Malformed cursor degrades to the first page.
            with contextlib.suppress(ValueError):
                query = query.where(AuditLog.id < uuid.UUID(cursor))

        result = await self.session.execute(query.limit(params.limit + 1))
        return result.scalars().all()
