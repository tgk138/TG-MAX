"""Autopost link management API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.autopost import AutopostLink, AutopostStatus
from app.models.max_connection import MaxConnection
from app.models.tg_connection import TgConnection, TgConnectionStatus
from app.models.user import User

router = APIRouter(prefix="/api/autopost", tags=["autopost"])


class CreateAutopostRequest(BaseModel):
    tg_peer_id: str
    tg_title: str
    max_chat_id: int
    max_chat_title: str = ""


@router.get("")
async def list_links(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(AutopostLink)
        .where(AutopostLink.user_id == user.id)
        .order_by(AutopostLink.created_at.desc())
        .limit(50)
    )
    links = result.scalars().all()
    return [
        {
            "id": str(lnk.id),
            "tg_channel_peer": lnk.tg_channel_peer,
            "tg_channel_title": lnk.tg_channel_title,
            "max_chat_id": lnk.max_chat_id,
            "max_chat_title": lnk.max_chat_title,
            "status": lnk.status.value,
            "forwarded_count": lnk.forwarded_count,
            "last_tg_message_id": lnk.last_tg_message_id,
            "error_text": lnk.error_text,
            "created_at": lnk.created_at.isoformat() if lnk.created_at else None,
        }
        for lnk in links
    ]


@router.post("")
async def create_link(
    body: CreateAutopostRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tg_result = await db.execute(
        select(TgConnection)
        .where(
            TgConnection.user_id == user.id,
            TgConnection.status == TgConnectionStatus.authed,
        )
        .order_by(TgConnection.updated_at.desc())
        .limit(1)
    )
    tg_conn = tg_result.scalars().first()
    if not tg_conn:
        raise HTTPException(400, "No authenticated Telegram connection.")

    max_result = await db.execute(
        select(MaxConnection)
        .where(MaxConnection.user_id == user.id)
        .order_by(MaxConnection.updated_at.desc())
        .limit(1)
    )
    max_conn = max_result.scalars().first()
    if not max_conn:
        raise HTTPException(400, "No MAX connection.")

    # Get current latest message ID so we only forward truly NEW posts
    from app.services import secrets as sec
    from app.services.telegram import TelegramService

    session_string = sec.decrypt(tg_conn.session_encrypted)
    tg = TelegramService(session_string)
    last_msg_id = 0
    try:
        await tg.connect()
        entity = await tg.client.get_entity(int(body.tg_peer_id))
        async for msg in tg.client.iter_messages(entity, limit=1):
            last_msg_id = msg.id
    except Exception:
        pass
    finally:
        await tg.disconnect()

    link = AutopostLink(
        user_id=user.id,
        tg_connection_id=tg_conn.id,
        max_connection_id=max_conn.id,
        tg_channel_peer=body.tg_peer_id,
        tg_channel_title=body.tg_title,
        max_chat_id=body.max_chat_id,
        max_chat_title=body.max_chat_title,
        last_tg_message_id=last_msg_id,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)

    return {"id": str(link.id), "status": "active", "last_tg_message_id": last_msg_id}


@router.patch("/{link_id}/pause")
async def pause_link(
    link_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    link = await _get_user_link(db, link_id, user.id)
    link.status = AutopostStatus.paused
    link.is_active = False
    await db.commit()
    return {"ok": True, "status": "paused"}


@router.patch("/{link_id}/resume")
async def resume_link(
    link_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    link = await _get_user_link(db, link_id, user.id)
    link.status = AutopostStatus.active
    link.is_active = True
    link.error_text = None
    await db.commit()
    return {"ok": True, "status": "active"}


@router.delete("/{link_id}")
async def delete_link(
    link_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    link = await _get_user_link(db, link_id, user.id)
    await db.delete(link)
    await db.commit()
    return {"ok": True}


async def _get_user_link(
    db: AsyncSession, link_id: uuid.UUID, user_id: uuid.UUID
) -> AutopostLink:
    link = await db.get(AutopostLink, link_id)
    if not link or link.user_id != user_id:
        raise HTTPException(404, "Autopost link not found.")
    return link
