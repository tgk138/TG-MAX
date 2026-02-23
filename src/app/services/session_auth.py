"""Session-based authentication helpers."""

from __future__ import annotations

import secrets as py_secrets
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.models.user_session import UserSession


def create_session_key() -> str:
    return py_secrets.token_hex(32)


async def create_user_session(db: AsyncSession, user_id) -> UserSession:
    session = UserSession(
        user_id=user_id,
        session_key=create_session_key(),
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=settings.SESSION_TTL_DAYS),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def resolve_user_from_request(db: AsyncSession, request: Request) -> User | None:
    session_key = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not session_key:
        return None

    now = datetime.now(UTC).replace(tzinfo=None)
    result = await db.execute(
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(
            UserSession.session_key == session_key,
            UserSession.expires_at > now,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def delete_session_by_key(db: AsyncSession, session_key: str) -> None:
    await db.execute(delete(UserSession).where(UserSession.session_key == session_key))
    await db.commit()


def set_session_cookie(response: Response, session_key: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_key,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        max_age=settings.SESSION_TTL_DAYS * 24 * 60 * 60,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
    )
