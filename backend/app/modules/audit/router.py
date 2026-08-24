"""Audit trail endpoints. Read-only by construction - there is no write route."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.pagination import CursorPage, CursorParams
from app.core.schemas import IpAddress, ResponseSchema
from app.modules.audit.models import AuditAction, AuditLog, AuditSeverity
from app.modules.audit.service import AuditService
from app.modules.auth.dependencies import (
    ActiveOrganizationId,
    DbSession,
    require_permission,
)
from app.modules.rbac.permissions import Permission

router = APIRouter(prefix="/audit", tags=["Audit"])


class AuditActor(ResponseSchema):
    """Actor identity, read from the denormalised columns.

    Uses the copies stored on the audit row rather than joining to the user table,
    so entries stay readable after the actor's account is deleted.
    """

    id: uuid.UUID | None = None
    email: str | None = None
    name: str | None = None

    @classmethod
    def from_row(cls, row: AuditLog) -> AuditActor:
        return cls(id=row.actor_user_id, email=row.actor_email, name=row.actor_label)


class AuditLogRead(ResponseSchema):
    """One audit entry.

    Built explicitly via :meth:`from_row` rather than by validating the ORM
    object: the ``actor`` field here is a nested summary assembled from
    denormalised columns, and there is a same-named ``AuditLog.actor``
    relationship declared ``lazy="raise"`` (an N+1 guard). Letting
    ``from_attributes`` read it would trip that guard on every response.
    """

    id: uuid.UUID
    action: AuditAction
    severity: AuditSeverity
    summary: str | None = None
    actor: AuditActor
    resource_type: str | None = None
    resource_id: str | None = None
    ip_address: IpAddress | None = None
    request_id: str | None = None
    changes: dict[str, object]
    context: dict[str, object]
    created_at: dt.datetime

    @classmethod
    def from_row(cls, row: AuditLog) -> AuditLogRead:
        return cls(
            id=row.id,
            action=row.action,
            severity=row.severity,
            summary=row.summary,
            actor=AuditActor.from_row(row),
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            ip_address=str(row.ip_address) if row.ip_address is not None else None,
            request_id=row.request_id,
            changes=row.changes,
            context=row.context,
            created_at=row.created_at,
        )


def get_audit_service(session: DbSession) -> AuditService:
    return AuditService(session)


AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]


@router.get(
    "",
    response_model=CursorPage[AuditLogRead],
    summary="The organization's audit trail",
)
async def list_audit_log(
    organization_id: ActiveOrganizationId,
    service: AuditServiceDep,
    params: Annotated[CursorParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.AUDIT_READ))],
    action: Annotated[AuditAction | None, Query(description="Filter by action")] = None,
    actor_user_id: Annotated[uuid.UUID | None, Query(description="Filter by actor")] = None,
    severity: Annotated[AuditSeverity | None, Query(description="Filter by severity")] = None,
    resource_type: Annotated[str | None, Query(max_length=60)] = None,
) -> CursorPage[AuditLogRead]:
    """Newest first, cursor-paginated.

    Cursor rather than page numbers: this is an append-heavy feed, where offsets
    both degrade with depth and shift rows under the reader as new events land.
    """
    rows = await service.list_for_organization(
        organization_id,
        params,
        action=action,
        actor_user_id=actor_user_id,
        severity=severity,
        resource_type=resource_type,
    )

    items = [AuditLogRead.from_row(row) for row in rows]

    # The last row of the over-fetched set carries the cursor for the next page.
    last_id = str(rows[params.limit - 1].id) if len(rows) > params.limit else None
    return CursorPage[AuditLogRead].create(items, limit=params.limit, cursor_of=last_id)


@router.get(
    "/actions",
    response_model=list[str],
    summary="Available audit action types",
)
async def list_audit_actions(
    _: Annotated[None, Depends(require_permission(Permission.AUDIT_READ))],
) -> list[str]:
    """Populates the filter dropdown from the server's own vocabulary."""
    return sorted(action.value for action in AuditAction)
