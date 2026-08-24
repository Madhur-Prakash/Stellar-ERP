"""User profile endpoints.

Scoped to the caller's *own* record. Administering other users happens through
the organization member endpoints, where the permission model applies.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.modules.auth.dependencies import CurrentUser, DbSession, RequestCtx
from app.modules.users.schemas import (
    UserPreferencesUpdate,
    UserRead,
    UserStats,
    UserUpdate,
)
from app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["Users"])


def get_user_service(session: DbSession) -> UserService:
    return UserService(session)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


@router.get("/me", response_model=UserRead, summary="Your profile")
async def get_my_profile(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch("/me", response_model=UserRead, summary="Update your profile")
async def update_my_profile(
    data: UserUpdate,
    user: CurrentUser,
    service: UserServiceDep,
    ctx: RequestCtx,
) -> UserRead:
    """Partial update. Omitted fields are left untouched."""
    return UserRead.model_validate(await service.update_profile(user, data, ctx))


@router.patch(
    "/me/preferences",
    response_model=UserRead,
    summary="Update display preferences",
)
async def update_my_preferences(
    data: UserPreferencesUpdate,
    user: CurrentUser,
    service: UserServiceDep,
) -> UserRead:
    """Theme, locale, and timezone. Persisted so the choice follows the user
    across devices instead of living in one browser's local storage."""
    return UserRead.model_validate(await service.update_preferences(user, data))


@router.get("/me/stats", response_model=UserStats, summary="Your account statistics")
async def get_my_stats(user: CurrentUser, service: UserServiceDep) -> UserStats:
    return await service.get_stats(user)
