"""MAX API endpoints (bot connection + targets)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.request_parsing import parse_payload
from app.models.max_connection import MaxConnection
from app.models.max_target import MaxTarget
from app.models.user import User
from app.services import secrets
from app.services.max_client import MaxClient, MaxApiError

router = APIRouter(prefix="/api/max", tags=["max"])


class ConnectRequest(BaseModel):
    bot_token: str


@router.post("/connect")
async def connect_bot(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Connect MAX bot by token. Validates via GET /me."""
    body = await parse_payload(request, ConnectRequest)

    client = MaxClient(body.bot_token)
    try:
        me = await client.get_me()
    except MaxApiError as e:
        raise HTTPException(400, f"Invalid token: {e.message}")
    finally:
        await client.close()

    bot_name = me.get("name", me.get("username", "bot"))
    encrypted = secrets.encrypt(body.bot_token)

    conn = MaxConnection(
        user_id=user.id,
        bot_token_encrypted=encrypted,
        bot_name=bot_name,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)

    return {"ok": True, "bot_name": bot_name, "connection_id": str(conn.id)}


@router.post("/sync_targets")
async def sync_targets(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Fetch chats from MAX API and save as targets."""
    result = await db.execute(
        select(MaxConnection)
        .where(MaxConnection.user_id == user.id)
        .order_by(MaxConnection.updated_at.desc(), MaxConnection.created_at.desc())
        .limit(1)
    )
    conn = result.scalars().first()
    if not conn:
        raise HTTPException(400, "No MAX connection. Connect a bot first.")

    token = secrets.decrypt(conn.bot_token_encrypted)
    client = MaxClient(token)

    try:
        data = await client.get_chats()
    except MaxApiError as e:
        raise HTTPException(400, f"MAX API error: {e.message}")
    finally:
        await client.close()

    chats = data.get("chats", [])
    count = 0

    for chat in chats:
        chat_id = chat.get("chat_id")
        if not chat_id:
            continue

        # Upsert: check if already exists
        existing = await db.execute(
            select(MaxTarget).where(
                MaxTarget.max_connection_id == conn.id,
                MaxTarget.chat_id == chat_id,
            )
        )
        if existing.scalars().first():
            continue

        target = MaxTarget(
            user_id=user.id,
            max_connection_id=conn.id,
            chat_id=chat_id,
            title=chat.get("title", "Untitled"),
            type=chat.get("type"),
        )
        db.add(target)
        count += 1

    await db.commit()
    return {"ok": True, "found": count}


@router.get("/targets")
async def list_targets(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List saved MAX target chats (deduplicated by chat_id)."""
    result = await db.execute(
        select(MaxTarget)
        .where(MaxTarget.user_id == user.id)
        .order_by(MaxTarget.chat_id)
    )
    targets = result.scalars().all()
    seen: set[int] = set()
    unique: list[dict] = []
    for t in targets:
        if t.chat_id in seen:
            continue
        seen.add(t.chat_id)
        unique.append({
            "id": str(t.id),
            "chat_id": t.chat_id,
            "title": t.title,
            "type": t.type,
        })
    return unique
