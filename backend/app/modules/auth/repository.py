"""Session data access - refresh-token lookup, rotation, and revocation."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import ClassVar

from sqlalchemy import select, update

from app.core.security import hash_token
from app.db.repository import BaseRepository, rows_affected
from app.modules.auth.models import (
    LoginMethod,
    SessionRevocationReason,
    UserSession,
)


class SessionRepository(BaseRepository[UserSession]):
    model = UserSession
    sortable_fields: ClassVar[frozenset[str]] = frozenset({"created_at", "last_used_at"})

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        refresh_token: str,
        expires_at: dt.datetime,
        organization_id: uuid.UUID | None = None,
        login_method: LoginMethod = LoginMethod.PASSWORD,
        ip_address: str | None = None,
        user_agent: str | None = None,
        device_label: str | None = None,
        device_type: str | None = None,
        generation: int = 0,
    ) -> UserSession:
        session = UserSession(
            user_id=user_id,
            # Only the digest is persisted; the plaintext goes to the client.
            refresh_token_hash=hash_token(refresh_token),
            expires_at=expires_at,
            organization_id=organization_id,
            login_method=login_method,
            ip_address=ip_address,
            user_agent=user_agent,
            device_label=device_label,
            device_type=device_type,
            generation=generation,
            last_used_at=dt.datetime.now(dt.UTC),
        )
        return await self.add(session)

    async def get_by_refresh_token(self, refresh_token: str) -> UserSession | None:
        """Look a session up by its token's digest, revoked or not.

        Revoked rows are intentionally returned: reuse detection needs to see
        that a presented token *was* valid once.

        **Locked for update**, because rotation is read-then-write and the read decides
        whether the write is a legitimate rotation or a token being replayed. Two refreshes
        arriving together on the same token both read it as valid, both mint a successor,
        and both revoke the original - leaving two live sessions descended from one login,
        which is how a single sign-in came to show six devices. Sole caller is
        :meth:`AuthService.refresh`, so the lock costs nothing elsewhere.
        """
        query = (
            select(UserSession)
            .where(UserSession.refresh_token_hash == hash_token(refresh_token))
            .with_for_update()
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list_active(self, user_id: uuid.UUID) -> Sequence[UserSession]:
        query = (
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > dt.datetime.now(dt.UTC),
            )
            .order_by(UserSession.last_used_at.desc().nullslast())
        )
        return (await self.session.execute(query)).scalars().all()

    async def revoke(
        self,
        session: UserSession,
        reason: SessionRevocationReason,
        *,
        rotated_to: uuid.UUID | None = None,
    ) -> UserSession:
        session.revoked_at = dt.datetime.now(dt.UTC)
        session.revocation_reason = reason
        if rotated_to is not None:
            session.rotated_to_id = rotated_to
        await self.session.flush()
        return session

    async def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        reason: SessionRevocationReason,
        *,
        except_session_id: uuid.UUID | None = None,
    ) -> int:
        """Bulk-revoke a user's sessions, returning how many were affected.

        A single ``UPDATE`` rather than a load-and-loop: this runs on password
        change and "sign out everywhere", where the row count is unbounded.
        Callers must also bump the user's token epoch, otherwise already-issued
        access tokens stay valid until they expire.
        """
        statement = (
            update(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=dt.datetime.now(dt.UTC), revocation_reason=reason)
        )
        if except_session_id is not None:
            statement = statement.where(UserSession.id != except_session_id)

        result = await self.session.execute(statement)
        await self.session.flush()
        return rows_affected(result)

    async def revoke_lineage(self, session: UserSession, reason: SessionRevocationReason) -> int:
        """Revoke every session descended from a compromised one.

        Called on refresh-token reuse: the whole chain is suspect, because we
        cannot tell which party holds the current token - the legitimate user or
        the attacker. Revoking the lineage forces a fresh, verified sign-in.
        """
        revoked = 0
        current: UserSession | None = session

        while current is not None:
            if current.revoked_at is None:
                current.revoked_at = dt.datetime.now(dt.UTC)
                current.revocation_reason = reason
                revoked += 1
            if current.rotated_to_id is None:
                break
            current = await self.get(current.rotated_to_id)

        await self.session.flush()
        return revoked

    async def touch(self, session: UserSession, *, ip_address: str | None = None) -> UserSession:
        session.last_used_at = dt.datetime.now(dt.UTC)
        if ip_address:
            session.ip_address = ip_address
        await self.session.flush()
        return session

    async def set_organization(
        self, session: UserSession, organization_id: uuid.UUID
    ) -> UserSession:
        session.organization_id = organization_id
        await self.session.flush()
        return session

    async def purge_expired(self, *, older_than_days: int = 30) -> int:
        """Delete long-dead sessions.

        The only place :meth:`hard_delete` semantics are correct - an expired
        session has no evidentiary value, and the audit log already records the
        logins. Intended for a scheduled job in Stage 7.
        """
        from sqlalchemy import delete

        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=older_than_days)
        result = await self.session.execute(
            delete(UserSession).where(UserSession.expires_at < cutoff)
        )
        await self.session.flush()
        return rows_affected(result)
