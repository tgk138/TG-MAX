"""UI page routes (Jinja2 templates)."""

from __future__ import annotations

import base64
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_optional_user
from app.models.job_event import JobEvent
from app.models.max_connection import MaxConnection
from app.models.autopost import AutopostLink
from app.models.migration import Migration, MigrationStatus
from app.models.tg_connection import TgConnection, TgConnectionStatus
from app.models.user import User
from app.services import secrets
from app.services.telegram import TelegramService

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def _redirect_home() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=302)


async def _get_latest_tg_connection(
    db: AsyncSession,
    user_id,
) -> TgConnection | None:
    result = await db.execute(
        select(TgConnection)
        .where(
            TgConnection.user_id == user_id,
            TgConnection.status == TgConnectionStatus.authed,
        )
        .order_by(TgConnection.updated_at.desc(), TgConnection.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _get_latest_max_connection(
    db: AsyncSession,
    user_id,
) -> MaxConnection | None:
    result = await db.execute(
        select(MaxConnection)
        .where(MaxConnection.user_id == user_id)
        .order_by(MaxConnection.updated_at.desc(), MaxConnection.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _get_tg_profile(
    conn: TgConnection | None,
) -> tuple[bool, dict | None, str | None]:
    if not conn or not conn.session_encrypted:
        return False, None, None

    session_str = secrets.decrypt(conn.session_encrypted)
    tg = TelegramService(session_str)
    try:
        await tg.connect()
        me = await tg.get_me()
        photo = await tg.get_profile_photo_bytes()
        avatar_data_url = None
        if photo:
            encoded = base64.b64encode(photo).decode("ascii")
            avatar_data_url = f"data:image/jpeg;base64,{encoded}"
        profile = {
            "id": me.id,
            "first_name": me.first_name or "",
            "last_name": me.last_name or "",
            "username": me.username or "",
            "phone": me.phone or "",
            "full_name": " ".join(p for p in [me.first_name, me.last_name] if p) or "Без имени",
        }
        return True, profile, avatar_data_url
    except Exception:
        return False, None, None
    finally:
        await tg.disconnect()


async def _setup_flags(
    db: AsyncSession,
    user: User | None,
) -> dict[str, bool]:
    if not user:
        return {
            "authed": False,
            "tg_connected": False,
            "max_connected": False,
            "has_draft_migration": False,
        }

    tg_conn = await _get_latest_tg_connection(db, user.id)
    max_conn = await _get_latest_max_connection(db, user.id)
    draft_result = await db.execute(
        select(Migration.id)
        .where(
            Migration.user_id == user.id,
            Migration.status == MigrationStatus.draft,
        )
        .limit(1)
    )
    return {
        "authed": True,
        "tg_connected": bool(tg_conn and tg_conn.session_encrypted),
        "max_connected": bool(max_conn),
        "has_draft_migration": draft_result.scalar_one_or_none() is not None,
    }


@router.get("/")
async def home(
    request: Request,
    user: User | None = Depends(get_optional_user),
):
    if user:
        return RedirectResponse(url="/cabinet", status_code=302)
    return templates.TemplateResponse("home.html", {"request": request, "current_user": user})


@router.get("/setup")
async def setup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    flags = await _setup_flags(db, user)
    context = {
        "request": request,
        "current_user": user,
        "setup_flags": flags,
    }
    return templates.TemplateResponse("setup.html", context)


@router.get("/api/setup/state")
async def setup_state(
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    return await _setup_flags(db, user)


@router.get("/terms")
async def terms(
    request: Request,
    user: User | None = Depends(get_optional_user),
):
    return templates.TemplateResponse("terms.html", {"request": request, "current_user": user})


@router.get("/privacy")
async def privacy(
    request: Request,
    user: User | None = Depends(get_optional_user),
):
    return templates.TemplateResponse("privacy.html", {"request": request, "current_user": user})


@router.get("/support")
async def support(
    request: Request,
    user: User | None = Depends(get_optional_user),
):
    return templates.TemplateResponse("support.html", {"request": request, "current_user": user})


@router.get("/cabinet")
async def cabinet(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    if not user:
        return _redirect_home()

    tg_conn = await _get_latest_tg_connection(db, user.id)
    tg_connected, profile, avatar_data_url = await _get_tg_profile(tg_conn)
    max_conn = await _get_latest_max_connection(db, user.id)

    migrations_result = await db.execute(
        select(Migration)
        .where(Migration.user_id == user.id)
        .order_by(Migration.created_at.desc())
        .limit(50)
    )
    migrations = migrations_result.scalars().all()

    return templates.TemplateResponse(
        "cabinet.html",
        {
            "request": request,
            "current_user": user,
            "tg_connected": tg_connected,
            "profile": profile,
            "avatar_data_url": avatar_data_url,
            "max_connected": bool(max_conn),
            "max_bot_name": max_conn.bot_name if max_conn else None,
            "migrations": migrations,
        },
    )


@router.get("/migration")
async def migration_page(
    request: Request,
    mid: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    if not user:
        return _redirect_home()

    migration = None
    data = {}

    if mid:
        try:
            migration_id = uuid.UUID(mid)
            migration = await db.get(Migration, migration_id)
            if migration and migration.user_id == user.id:
                result = await db.execute(
                    select(JobEvent)
                    .where(JobEvent.migration_id == migration_id)
                    .order_by(JobEvent.created_at.desc())
                    .limit(20)
                )
                events = result.scalars().all()

                data = {
                    "status": migration.status.value,
                    "max_target_chat_id": migration.max_target_chat_id,
                    "total_posts": migration.total_posts,
                    "imported_posts": migration.imported_posts,
                    "total_media": migration.total_media,
                    "downloaded_media": migration.downloaded_media,
                    "published_units": migration.published_units,
                    "failed_units": migration.failed_units,
                    "events": [
                        {
                            "type": e.event_type.value,
                            "phase": e.phase.value,
                            "message": e.message,
                            "created_at": e.created_at.isoformat() if e.created_at else "",
                        }
                        for e in events
                    ],
                }
            else:
                migration = None
        except ValueError:
            pass
    else:
        result = await db.execute(
            select(Migration)
            .where(Migration.user_id == user.id)
            .order_by(Migration.created_at.desc())
            .limit(1)
        )
        migration = result.scalar_one_or_none()
        if migration:
            data = {
                "status": migration.status.value,
                "max_target_chat_id": migration.max_target_chat_id,
                "total_posts": migration.total_posts,
                "imported_posts": migration.imported_posts,
                "total_media": migration.total_media,
                "downloaded_media": migration.downloaded_media,
                "published_units": migration.published_units,
                "failed_units": migration.failed_units,
                "events": [],
            }

    return templates.TemplateResponse(
        "migration.html",
        {
            "request": request,
            "current_user": user,
            "migration": migration,
            "data": data,
        },
    )


@router.get("/autopost")
async def autopost_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    if not user:
        return _redirect_home()

    flags = await _setup_flags(db, user)
    links_result = await db.execute(
        select(AutopostLink)
        .where(AutopostLink.user_id == user.id)
        .order_by(AutopostLink.created_at.desc())
        .limit(50)
    )
    links = links_result.scalars().all()

    return templates.TemplateResponse(
        "autopost.html",
        {
            "request": request,
            "current_user": user,
            "setup_flags": flags,
            "links": links,
        },
    )
