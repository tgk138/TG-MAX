"""FastAPI dependencies."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import get_session
from app.models.user import User
from app.services.session_auth import resolve_user_from_request


async def get_db(session: AsyncSession = Depends(get_session)) -> AsyncSession:
    return session


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    return await resolve_user_from_request(db, request)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await resolve_user_from_request(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return user
