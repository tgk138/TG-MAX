"""Migration management API endpoints."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.request_parsing import parse_payload
from app.models.job_event import JobEvent
from app.models.migration import Migration, MigrationStatus
from app.models.publish_unit import PublishUnit, PublishUnitStatus
from app.models.tg_connection import TgConnection, TgConnectionStatus
from app.models.tg_media import TgMedia
from app.models.tg_post import TgPost
from app.models.user import User
from app.services import secrets
from app.services.migration_sm import InvalidTransition, transition
from app.services.storage import get_storage
from app.services.telegram import TelegramService
from app.worker.tasks import import_channel, prepare_selected_for_publish, publish_to_max

router = APIRouter(prefix="/api/migrations", tags=["migrations"])
account_router = APIRouter(prefix="/api/account", tags=["account"])


class CreateMigrationRequest(BaseModel):
    tg_peer_id: str
    tg_title: str


class SetTargetRequest(BaseModel):
    max_chat_id: int


class SelectPostsRequest(BaseModel):
    selected: bool
    post_ids: list[uuid.UUID] | None = None
    min_views: int | None = None
    min_reactions: int | None = None
    date_from: date | None = None
    date_to: date | None = None


@router.get("")
async def list_migrations(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List user's migrations for cabinet."""
    result = await db.execute(
        select(Migration)
        .where(Migration.user_id == user.id)
        .order_by(Migration.created_at.desc())
        .limit(100)
    )
    migrations = result.scalars().all()
    return [
        {
            "id": str(m.id),
            "tg_channel_title": m.tg_channel_title,
            "max_target_chat_id": m.max_target_chat_id,
            "status": m.status.value,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in migrations
    ]


@router.post("")
async def create_migration(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new migration and start import immediately."""
    body = await parse_payload(request, CreateMigrationRequest)

    # Get user's TG connection
    result = await db.execute(
        select(TgConnection).where(
            TgConnection.user_id == user.id,
            TgConnection.status == TgConnectionStatus.authed,
        )
        .order_by(TgConnection.updated_at.desc(), TgConnection.created_at.desc())
        .limit(1)
    )
    tg_conn = result.scalars().first()
    if not tg_conn:
        raise HTTPException(400, "No authenticated Telegram connection.")

    migration = Migration(
        user_id=user.id,
        tg_connection_id=tg_conn.id,
        tg_channel_peer=body.tg_peer_id,
        tg_channel_title=body.tg_title,
    )
    db.add(migration)
    await db.commit()
    await db.refresh(migration)

    # Start import right away so channel content appears on migration page.
    try:
        await transition(db, migration.id, MigrationStatus.importing)
    except InvalidTransition:
        pass
    import_channel.delay(str(migration.id))

    return {"migration_id": str(migration.id), "status": "importing"}


@router.patch("/{migration_id}/set_target")
async def set_target(
    migration_id: uuid.UUID,
    body: SetTargetRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set MAX target chat for migration."""
    migration = await _get_user_migration(db, migration_id, user.id)

    from app.models.max_connection import MaxConnection

    result = await db.execute(
        select(MaxConnection)
        .where(MaxConnection.user_id == user.id)
        .order_by(MaxConnection.updated_at.desc(), MaxConnection.created_at.desc())
        .limit(1)
    )
    max_conn = result.scalars().first()
    if not max_conn:
        raise HTTPException(400, "No MAX connection.")

    migration.max_connection_id = max_conn.id
    migration.max_target_chat_id = body.max_chat_id
    await db.commit()

    return {"ok": True}


@router.post("/{migration_id}/start_import")
async def start_import(
    migration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Start importing from Telegram. Transition: draft -> importing."""
    migration = await _get_user_migration(db, migration_id, user.id)

    try:
        await transition(db, migration_id, MigrationStatus.importing)
    except InvalidTransition as e:
        raise HTTPException(409, str(e))

    import_channel.delay(str(migration_id))
    return {"ok": True}


@router.post("/{migration_id}/start_publish")
async def start_publish(
    migration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Start publishing to MAX. Transition: imported -> publishing."""
    migration = await _get_user_migration(db, migration_id, user.id)

    if not migration.max_target_chat_id:
        raise HTTPException(400, "No MAX target set.")
    if migration.preparing_media:
        raise HTTPException(409, "Full media preparation is still in progress.")
    if migration.selected_posts_count <= 0:
        raise HTTPException(400, "No selected posts to publish.")
    if migration.downloaded_media < migration.prepared_media_total:
        raise HTTPException(409, "Full media is not prepared yet. Run prepare step first.")

    try:
        await transition(db, migration_id, MigrationStatus.publishing)
    except InvalidTransition as e:
        raise HTTPException(409, str(e))

    publish_to_max.delay(str(migration_id))
    return {"ok": True}


@router.get("/{migration_id}")
async def get_migration(
    migration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get migration status, counters, and recent events."""
    migration = await _get_user_migration(db, migration_id, user.id)

    # Get recent events
    result = await db.execute(
        select(JobEvent)
        .where(JobEvent.migration_id == migration_id)
        .order_by(JobEvent.created_at.desc())
        .limit(20)
    )
    events = result.scalars().all()

    preview_done_result = await db.execute(
        select(func.count(TgMedia.id))
        .join(TgPost, TgMedia.tg_post_id == TgPost.id)
        .where(
            TgPost.migration_id == migration_id,
            TgMedia.preview_path.is_not(None),
        )
    )
    preview_done = int(preview_done_result.scalar_one() or 0)

    selected_posts_result = await db.execute(
        select(func.count(TgPost.id)).where(
            TgPost.migration_id == migration_id,
            TgPost.is_selected.is_(True),
        )
    )
    selected_posts_count = int(selected_posts_result.scalar_one() or 0)

    selected_media_result = await db.execute(
        select(func.count(TgMedia.id))
        .join(TgPost, TgMedia.tg_post_id == TgPost.id)
        .where(
            TgPost.migration_id == migration_id,
            TgPost.is_selected.is_(True),
        )
    )
    selected_media_total = int(selected_media_result.scalar_one() or 0)

    full_done_result = await db.execute(
        select(func.count(TgMedia.id))
        .join(TgPost, TgMedia.tg_post_id == TgPost.id)
        .where(
            TgPost.migration_id == migration_id,
            TgPost.is_selected.is_(True),
            TgMedia.is_full_downloaded.is_(True),
        )
    )
    full_done = int(full_done_result.scalar_one() or 0)

    publish_total_result = await db.execute(
        select(func.count(PublishUnit.id))
        .join(TgPost, PublishUnit.tg_post_id == TgPost.id)
        .where(TgPost.migration_id == migration_id)
    )
    publish_total = int(publish_total_result.scalar_one() or 0)

    return {
        "id": str(migration.id),
        "status": migration.status.value,
        "tg_channel_title": migration.tg_channel_title,
        "tg_channel_peer": migration.tg_channel_peer,
        "max_target_chat_id": migration.max_target_chat_id,
        "total_posts": migration.total_posts,
        "imported_posts": migration.imported_posts,
        "total_media": migration.total_media,
        "downloaded_media": migration.downloaded_media,
        "published_units": migration.published_units,
        "failed_units": migration.failed_units,
        "selected_posts_count": selected_posts_count,
        "prepared_media_total": selected_media_total,
        "preparing_media": migration.preparing_media,
        "pipeline": {
            "import": {"done": migration.imported_posts, "total": migration.total_posts},
            "preview": {"done": preview_done, "total": migration.total_media},
            "selection": {"done": selected_posts_count, "total": migration.total_posts},
            "prepare": {"done": full_done, "total": selected_media_total},
            "publish": {"done": migration.published_units, "total": publish_total},
        },
        "events": [
            {
                "type": e.event_type.value,
                "phase": e.phase.value,
                "message": e.message,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }


@router.get("/{migration_id}/source_preview")
async def get_source_preview(
    migration_id: uuid.UUID,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get quick live preview directly from Telegram source channel."""
    migration = await _get_user_migration(db, migration_id, user.id)
    safe_limit = max(1, min(limit, 50))

    tg_result = await db.execute(
        select(TgConnection)
        .where(
            TgConnection.user_id == user.id,
            TgConnection.status == TgConnectionStatus.authed,
        )
        .order_by(TgConnection.updated_at.desc(), TgConnection.created_at.desc())
        .limit(1)
    )
    tg_conn = tg_result.scalars().first()
    if not tg_conn or not tg_conn.session_encrypted:
        raise HTTPException(400, "No authenticated Telegram connection.")

    session_string = secrets.decrypt(tg_conn.session_encrypted)
    tg = TelegramService(session_string)
    try:
        await tg.connect()
        items = await tg.preview_channel_posts(int(migration.tg_channel_peer), limit=safe_limit)
        return {"items": items}
    finally:
        await tg.disconnect()


@router.get("/{migration_id}/posts_preview")
async def get_posts_preview(
    migration_id: uuid.UUID,
    limit: int = 50,
    min_views: int | None = None,
    min_reactions: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    selected_only: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get imported posts preview for migration page."""
    await _get_user_migration(db, migration_id, user.id)
    safe_limit = max(1, min(limit, 200))

    query = select(TgPost).where(TgPost.migration_id == migration_id)
    if min_views is not None:
        query = query.where(TgPost.views >= max(0, min_views))
    if min_reactions is not None:
        query = query.where(TgPost.reactions_total >= max(0, min_reactions))
    if date_from is not None:
        query = query.where(TgPost.posted_at >= date_from)
    if date_to is not None:
        query = query.where(TgPost.posted_at < date.fromordinal(date_to.toordinal() + 1))
    if selected_only:
        query = query.where(TgPost.is_selected.is_(True))

    result = await db.execute(
        query
        .order_by(TgPost.order_index.asc())
        .limit(safe_limit)
    )
    posts = result.scalars().all()
    if not posts:
        return {"items": []}

    post_ids = [p.id for p in posts]
    media_result = await db.execute(
        select(
            TgMedia.tg_post_id,
            TgMedia.id,
            TgMedia.preview_path,
            TgMedia.is_full_downloaded,
            TgMedia.media_type,
        ).where(TgMedia.tg_post_id.in_(post_ids))
    )
    media_count_by_post: dict[uuid.UUID, int] = {}
    preview_media_by_post: dict[uuid.UUID, list[dict]] = {}
    for post_id, media_id, preview_path, is_full_downloaded, media_type in media_result.all():
        media_count_by_post[post_id] = media_count_by_post.get(post_id, 0) + 1
        if preview_path:
            preview_media_by_post.setdefault(post_id, []).append(
                {
                    "id": str(media_id),
                    "preview_url": f"/media/{preview_path}",
                    "is_full_downloaded": bool(is_full_downloaded),
                    "media_type": media_type.value if media_type else "other",
                }
            )

    items = []
    for p in posts:
        items.append(
            {
                "id": str(p.id),
                "order_index": p.order_index,
                "message_id": p.tg_message_id,
                "text": p.text,
                "has_media": p.has_media,
                "media_count": media_count_by_post.get(p.id, 0),
                "views": p.views,
                "reactions_total": p.reactions_total,
                "posted_at": p.posted_at.isoformat() if p.posted_at else None,
                "is_selected": p.is_selected,
                "preview_media": preview_media_by_post.get(p.id, [])[:4],
                "album_key": p.album_key,
            }
        )
    return {"items": items}


@router.post("/{migration_id}/posts/select")
async def select_posts(
    migration_id: uuid.UUID,
    body: SelectPostsRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mark posts as selected/unselected for later full-res preparation/publish."""
    await _get_user_migration(db, migration_id, user.id)

    query = update(TgPost).where(TgPost.migration_id == migration_id)
    # Explicit empty list means "update none", while None means "use filters/all".
    if body.post_ids is not None:
        query = query.where(TgPost.id.in_(body.post_ids))
    else:
        if body.min_views is not None:
            query = query.where(TgPost.views >= max(0, body.min_views))
        if body.min_reactions is not None:
            query = query.where(TgPost.reactions_total >= max(0, body.min_reactions))
        if body.date_from is not None:
            query = query.where(TgPost.posted_at >= body.date_from)
        if body.date_to is not None:
            query = query.where(TgPost.posted_at < date.fromordinal(body.date_to.toordinal() + 1))
    result = await db.execute(query.values(is_selected=body.selected))
    updated = int(result.rowcount or 0)

    selected_result = await db.execute(
        select(func.count(TgPost.id)).where(
            TgPost.migration_id == migration_id,
            TgPost.is_selected.is_(True),
        )
    )
    selected_count = int(selected_result.scalar_one() or 0)
    await db.execute(
        update(Migration)
        .where(Migration.id == migration_id)
        .values(selected_posts_count=selected_count)
    )
    await db.commit()
    return {"ok": True, "updated": updated, "selected_posts_count": selected_count}


@router.post("/{migration_id}/prepare_publish")
async def prepare_publish(
    migration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Phase-2: download full-res media for selected posts."""
    migration = await _get_user_migration(db, migration_id, user.id)
    if migration.status != MigrationStatus.imported:
        raise HTTPException(409, "Prepare is available only for imported migrations.")
    if migration.preparing_media:
        return {"ok": True, "status": "already_running"}

    selected_result = await db.execute(
        select(func.count(TgPost.id)).where(
            TgPost.migration_id == migration_id,
            TgPost.is_selected.is_(True),
        )
    )
    selected_count = int(selected_result.scalar_one() or 0)
    if selected_count <= 0:
        raise HTTPException(400, "No selected posts.")

    selected_media_total_result = await db.execute(
        select(func.count(TgMedia.id))
        .join(TgPost, TgMedia.tg_post_id == TgPost.id)
        .where(
            TgPost.migration_id == migration_id,
            TgPost.is_selected.is_(True),
        )
    )
    selected_media_total = int(selected_media_total_result.scalar_one() or 0)

    migration.preparing_media = True
    migration.selected_posts_count = selected_count
    migration.prepared_media_total = selected_media_total
    migration.downloaded_media = 0
    await db.commit()

    prepare_selected_for_publish.delay(str(migration_id))
    return {"ok": True, "status": "preparing"}


@router.post("/{migration_id}/retry_failed")
async def retry_failed(
    migration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reset failed publish_units to pending and restart publishing."""
    migration = await _get_user_migration(db, migration_id, user.id)

    if migration.status not in (MigrationStatus.failed, MigrationStatus.done):
        raise HTTPException(409, "Can only retry from failed or done status.")

    # Reset failed units
    result = await db.execute(
        update(PublishUnit)
        .where(
            PublishUnit.tg_post_id.in_(
                select(TgPost.id).where(TgPost.migration_id == migration_id)
            ),
            PublishUnit.status == PublishUnitStatus.failed,
        )
        .values(status=PublishUnitStatus.pending, error_code=None, error_text=None)
    )
    retried = result.rowcount

    if retried == 0:
        return {"ok": True, "retried": 0}

    # Force status to publishing
    migration.status = MigrationStatus.publishing
    migration.failed_units = 0
    await db.commit()

    publish_to_max.delay(str(migration_id))
    return {"ok": True, "retried": retried}


@router.delete("/{migration_id}")
async def delete_migration(
    migration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete migration and all related data + media files."""
    migration = await _get_user_migration(db, migration_id, user.id)
    storage = get_storage()

    # Get all media paths to delete files
    result = await db.execute(
        select(TgMedia.file_path, TgMedia.preview_path, TgMedia.full_path)
        .join(TgPost)
        .where(TgPost.migration_id == migration_id)
    )
    paths: set[str] = set()
    for file_path, preview_path, full_path in result.all():
        for path in (file_path, preview_path, full_path):
            if path:
                paths.add(path)

    # Delete from storage
    for path in paths:
        try:
            await storage.delete(path)
        except Exception:
            pass

    # Cascade delete handles tg_posts, tg_media, publish_units, job_events
    await db.execute(delete(Migration).where(Migration.id == migration_id))
    await db.commit()

    return {"ok": True}


@account_router.delete("/purge")
async def purge_account(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete all user data (GDPR-style)."""
    from app.models.max_connection import MaxConnection
    from app.models.max_target import MaxTarget
    from app.models.tg_connection import TgConnection

    # Delete media files
    storage = get_storage()
    result = await db.execute(
        select(TgMedia.file_path, TgMedia.preview_path, TgMedia.full_path)
        .join(TgPost)
        .join(Migration)
        .where(Migration.user_id == user.id)
    )
    paths: set[str] = set()
    for file_path, preview_path, full_path in result.all():
        for path in (file_path, preview_path, full_path):
            if path:
                paths.add(path)
    for path in paths:
        try:
            await storage.delete(path)
        except Exception:
            pass

    # Cascade deletes
    await db.execute(delete(Migration).where(Migration.user_id == user.id))
    await db.execute(delete(MaxTarget).where(MaxTarget.user_id == user.id))
    await db.execute(delete(MaxConnection).where(MaxConnection.user_id == user.id))
    await db.execute(delete(TgConnection).where(TgConnection.user_id == user.id))
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()

    return {"ok": True}


async def _get_user_migration(
    db: AsyncSession, migration_id: uuid.UUID, user_id: uuid.UUID
) -> Migration:
    migration = await db.get(Migration, migration_id)
    if not migration or migration.user_id != user_id:
        raise HTTPException(404, "Migration not found.")
    return migration
