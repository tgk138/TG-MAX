"""Authentication endpoints."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.services.session_auth import clear_session_cookie, delete_session_by_key

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    session_key = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if session_key:
        await delete_session_by_key(db, session_key)
    clear_session_cookie(response)
    return {"ok": True}
