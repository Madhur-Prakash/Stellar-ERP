"""User profile business logic."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.logging import get_logger
from app.modules.audit.models import AuditAction
from app.modules.audit.service import AuditService, diff
from app.modules.auth.repository import SessionRepository
from app.modules.users.models import User
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserPreferencesUpdate, UserStats, UserUpdate

log = get_logger(__name__)


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.sessions = SessionRepository(session)
        self.audit = AuditService(session)

    async def update_profile(self, user: User, data: UserUpdate, ctx: RequestContext) -> User:
        """Apply a partial profile update.

        ``exclude_unset`` distinguishes "field omitted" from "field set to null":
        without it, a client sending only ``theme`` would blank the user's phone
        number and timezone.
        """
        changes = data.model_dump(exclude_unset=True, exclude_none=True)

        # An emptied optional field means "remove this", so store nothing rather than an
        # empty string. `exclude_none` above means a client cannot clear a field by sending
        # null - deliberate, so a partial update never blanks what it omitted - which leaves
        # an empty string as the only way to express clearing. Left as "" the column is not
        # null, escapes every `IS NULL` check, and still renders blank on screen.
        for field in ("phone", "avatar_url"):
            if changes.get(field) == "":
                changes[field] = None

        if not changes:
            return user

        before = {field: getattr(user, field) for field in changes}
        await self.users.update(user, **changes)

        await self.audit.record(
            AuditAction.USER_PROFILE_UPDATED,
            actor=user,
            resource_type="user",
            resource_id=user.id,
            summary="Updated their profile",
            changes=diff(before, changes),
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        log.info(
            "profile updated",
            extra={"user_id": str(user.id), "fields": sorted(changes)},
        )
        return user

    async def update_preferences(self, user: User, data: UserPreferencesUpdate) -> User:
        """Update display preferences.

        Not audited: a theme toggle is not a security event, and recording it
        would flood the trail that admins actually need to read.
        """
        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        if changes:
            await self.users.update(user, **changes)
        return user

    async def get_stats(self, user: User) -> UserStats:
        sessions = await self.sessions.list_active(user.id)
        memberships = await self.users.active_memberships(user.id)

        return UserStats(
            active_sessions=len(sessions),
            organizations=len(memberships),
            recovery_codes_remaining=(
                len(user.recovery_code_hashes) if user.is_two_factor_enabled else 0
            ),
        )
