"""Celery tasks for import and publish workflows."""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery
from app.config import settings
from app.models.base import async_session
from app.models.job_event import EventPhase, EventType, JobEvent
from app.models.migration import Migration, MigrationStatus
from app.models.publish_unit import PublishUnit, PublishUnitStatus
from app.models.tg_media import TgMedia
from app.models.tg_post import TgPost
from app.services import secrets
from app.services.max_client import MaxClient
from app.services.migration_sm import transition
from app.services.storage import get_storage
from app.services.telegram import TelegramService
from app.services.text_format import to_max_text_payload

logger = logging.getLogger(__name__)

_worker_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    """Run async coroutine from sync Celery task using one persistent loop per worker process."""
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coro)


@celery.task(bind=True, max_retries=0)
def import_channel(self, migration_id: str):
    """Import channel content from Telegram to DB + storage."""
    _run_async(_import_channel(uuid.UUID(migration_id)))


@celery.task(bind=True, max_retries=0)
def fail_stale_importing(self):
    """Periodic watchdog: fail migrations stuck in importing longer than TTL."""
    _run_async(_fail_stale_importing())


@celery.task(bind=True, max_retries=0)
def prepare_selected_for_publish(self, migration_id: str):
    """Phase-2: download full-res media for selected posts and build publish units."""
    _run_async(_prepare_selected_for_publish(uuid.UUID(migration_id)))


async def _import_channel(migration_id: uuid.UUID):
    storage = get_storage()

    async with async_session() as db:
        # Get migration + TG connection
        migration = await db.get(Migration, migration_id)
        if not migration:
            logger.error("Migration %s not found", migration_id)
            return

        from app.models.tg_connection import TgConnection

        tg_conn = await db.get(TgConnection, migration.tg_connection_id)
        if not tg_conn or not tg_conn.session_encrypted:
            logger.error("TG connection not found for migration %s", migration_id)
            await _fail_migration(db, migration_id, "TG connection not found")
            return

        session_string = secrets.decrypt(tg_conn.session_encrypted)

    # Run export outside the main DB session to avoid long transactions
    tg_service = TelegramService(session_string)
    try:
        await tg_service.connect()

        async with async_session() as db:
            await tg_service.export_channel(
                db=db,
                storage=storage,
                migration_id=migration_id,
                peer_id=int(migration.tg_channel_peer),
            )

            # Transition to imported
            await transition(db, migration_id, MigrationStatus.imported)

    except Exception as e:
        logger.exception("Import failed for migration %s", migration_id)
        async with async_session() as db:
            await _fail_migration(db, migration_id, str(e))
    finally:
        await tg_service.disconnect()


async def _fail_stale_importing():
    ttl_minutes = max(1, int(settings.IMPORT_STALE_TTL_MINUTES))
    cutoff = datetime.utcnow() - timedelta(minutes=ttl_minutes)
    async with async_session() as db:
        result = await db.execute(
            select(Migration.id)
            .where(
                Migration.status == MigrationStatus.importing,
                Migration.updated_at < cutoff,
            )
            .order_by(Migration.updated_at.asc())
            .limit(100)
        )
        stale_ids = [row[0] for row in result.all()]
        if not stale_ids:
            return

        for migration_id in stale_ids:
            try:
                await transition(db, migration_id, MigrationStatus.failed)
            except Exception:
                await db.execute(
                    update(Migration)
                    .where(Migration.id == migration_id)
                    .values(status=MigrationStatus.failed)
                )
                await db.commit()
            await _log_event(
                db,
                migration_id,
                EventPhase.import_,
                EventType.warning,
                (
                    "Auto-failed stale importing migration "
                    f"(ttl={ttl_minutes}m, updated_at<{cutoff.isoformat()})"
                ),
            )


async def _prepare_selected_for_publish(migration_id: uuid.UUID):
    storage = get_storage()
    async with async_session() as db:
        migration = await db.get(Migration, migration_id)
        if not migration:
            logger.error("Migration %s not found", migration_id)
            return
        if migration.status != MigrationStatus.imported:
            logger.info(
                "Skip prepare_selected_for_publish for migration %s in status %s",
                migration_id,
                migration.status.value,
            )
            return

        from app.models.tg_connection import TgConnection

        tg_conn = await db.get(TgConnection, migration.tg_connection_id)
        if not tg_conn or not tg_conn.session_encrypted:
            await _fail_migration(db, migration_id, "TG connection not found")
            return

        selected_count_result = await db.execute(
            select(func.count(TgPost.id))
            .where(
                TgPost.migration_id == migration_id,
                TgPost.is_selected.is_(True),
            )
        )
        selected_posts_count = int(selected_count_result.scalar_one() or 0)
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
        migration.selected_posts_count = selected_posts_count
        migration.prepared_media_total = selected_media_total
        migration.downloaded_media = 0
        migration.published_units = 0
        migration.failed_units = 0
        await db.commit()

        session_string = secrets.decrypt(tg_conn.session_encrypted)
        tg_peer_id = int(migration.tg_channel_peer)

    tg_service = TelegramService(session_string)
    prepared_media = 0
    try:
        await tg_service.connect()
        async with async_session() as db:
            prepared_media, selected_media_total = await tg_service.prepare_selected_media_full(
                db=db,
                storage=storage,
                migration_id=migration_id,
                peer_id=tg_peer_id,
            )

            await db.execute(
                update(Migration)
                .where(Migration.id == migration_id)
                .values(
                    downloaded_media=prepared_media,
                    prepared_media_total=selected_media_total,
                    preparing_media=False,
                )
            )
            await db.commit()

            await db.execute(
                delete(PublishUnit).where(
                    PublishUnit.tg_post_id.in_(
                        select(TgPost.id).where(TgPost.migration_id == migration_id)
                    )
                )
            )
            await db.commit()
            await _generate_publish_units(db, migration_id, selected_only=True)
            await _log_event(
                db,
                migration_id,
                EventPhase.import_,
                EventType.info,
                f"Full media prepared for selected posts: {prepared_media}/{selected_media_total}",
            )
    except Exception as e:
        logger.exception("Prepare selected media failed for migration %s", migration_id)
        async with async_session() as db:
            await db.execute(
                update(Migration)
                .where(Migration.id == migration_id)
                .values(preparing_media=False)
            )
            await db.commit()
            await _log_event(
                db,
                migration_id,
                EventPhase.import_,
                EventType.error,
                f"Prepare selected media failed: {str(e)[:400]}",
            )
    finally:
        await tg_service.disconnect()


async def _generate_publish_units(db: AsyncSession, migration_id: uuid.UUID, selected_only: bool = False):
    """Create publish_units from tg_posts + tg_media."""
    max_attach = settings.MAX_ATTACHMENTS_PER_MESSAGE

    query = select(TgPost).where(TgPost.migration_id == migration_id)
    if selected_only:
        query = query.where(TgPost.is_selected.is_(True))
    result = await db.execute(query.order_by(TgPost.order_index))
    posts = result.scalars().all()

    for post in posts:
        media_result = await db.execute(
            select(TgMedia)
            .where(TgMedia.tg_post_id == post.id)
            .order_by(TgMedia.album_index)
        )
        media_items = media_result.scalars().all()
        media_count = len(media_items)

        if media_count == 0:
            # Text-only post: single unit
            db.add(
                PublishUnit(
                    tg_post_id=post.id,
                    unit_index=0,
                    attachments_from=0,
                    attachments_to=0,
                )
            )
        elif media_count <= max_attach:
            # Fits in one message
            db.add(
                PublishUnit(
                    tg_post_id=post.id,
                    unit_index=0,
                    attachments_from=0,
                    attachments_to=media_count,
                )
            )
        else:
            # Split into multiple units
            unit_idx = 0
            for start in range(0, media_count, max_attach):
                end = min(start + max_attach, media_count)
                db.add(
                    PublishUnit(
                        tg_post_id=post.id,
                        unit_index=unit_idx,
                        attachments_from=start,
                        attachments_to=end,
                    )
                )
                unit_idx += 1

    await db.flush()
    await db.commit()


@celery.task(bind=True, max_retries=0)
def publish_to_max(self, migration_id: str):
    """Publish imported content to MAX messenger."""
    _run_async(_publish_to_max(uuid.UUID(migration_id)))


async def _publish_to_max(migration_id: uuid.UUID):
    async with async_session() as db:
        migration = await db.get(Migration, migration_id)
        if not migration:
            logger.error("Migration %s not found", migration_id)
            return

        from app.models.max_connection import MaxConnection

        max_conn = await db.get(MaxConnection, migration.max_connection_id)
        if not max_conn or not max_conn.bot_token_encrypted:
            await _fail_migration(db, migration_id, "MAX connection not found")
            return

        bot_token = secrets.decrypt(max_conn.bot_token_encrypted)
        chat_id = migration.max_target_chat_id

    if not chat_id:
        async with async_session() as db:
            await _fail_migration(db, migration_id, "No target chat set")
        return

    max_client = MaxClient(bot_token)
    storage = get_storage()

    try:
        async with async_session() as db:
            # Get pending units ordered by post.order_index, unit.unit_index
            result = await db.execute(
                select(PublishUnit, TgPost)
                .join(TgPost, PublishUnit.tg_post_id == TgPost.id)
                .where(
                    TgPost.migration_id == migration_id,
                    PublishUnit.status == PublishUnitStatus.pending,
                )
                .order_by(TgPost.order_index, PublishUnit.unit_index)
            )
            units_with_posts = result.all()

        for unit, post in units_with_posts:
            try:
                await _publish_unit(db, max_client, storage, chat_id, unit, post)
            except Exception as e:
                logger.exception("Failed to publish unit %s", unit.id)
                async with async_session() as db:
                    await db.execute(
                        update(PublishUnit)
                        .where(PublishUnit.id == unit.id)
                        .values(
                            status=PublishUnitStatus.failed,
                            error_text=str(e)[:500],
                            retry_count=PublishUnit.retry_count + 1,
                        )
                    )
                    await db.commit()

            # Rate limit delay
            delay = random.uniform(
                settings.PUBLISH_DELAY_MS_MIN / 1000.0,
                settings.PUBLISH_DELAY_MS_MAX / 1000.0,
            )
            await asyncio.sleep(delay)

        # Update migration counters and status
        async with async_session() as db:
            counts_result = await db.execute(
                select(PublishUnit.status, func.count(PublishUnit.id))
                .join(TgPost, PublishUnit.tg_post_id == TgPost.id)
                .where(TgPost.migration_id == migration_id)
                .group_by(PublishUnit.status)
            )
            status_counts = {status: count for status, count in counts_result.all()}
            sent_total = int(status_counts.get(PublishUnitStatus.sent, 0))
            failed_total = int(status_counts.get(PublishUnitStatus.failed, 0))
            unfinished_total = (
                int(status_counts.get(PublishUnitStatus.pending, 0))
                + int(status_counts.get(PublishUnitStatus.uploading, 0))
                + int(status_counts.get(PublishUnitStatus.sending, 0))
            )

            await db.execute(
                update(Migration)
                .where(Migration.id == migration_id)
                .values(published_units=sent_total, failed_units=failed_total)
            )
            await db.commit()

            if failed_total == 0 and unfinished_total == 0:
                await transition(db, migration_id, MigrationStatus.done)
                await _log_event(
                    db, migration_id, EventPhase.publish, EventType.info,
                    f"Publish complete: {sent_total} units sent",
                )
            else:
                await _log_event(
                    db, migration_id, EventPhase.publish, EventType.warning,
                    (
                        "Publish finished with errors: "
                        f"{sent_total} sent, {failed_total} failed, {unfinished_total} unfinished"
                    ),
                )
                await transition(db, migration_id, MigrationStatus.failed)

    except Exception as e:
        logger.exception("Publish failed for migration %s", migration_id)
        async with async_session() as db:
            await _fail_migration(db, migration_id, str(e))
    finally:
        await max_client.close()


async def _publish_unit(
    db_unused: AsyncSession,
    max_client: MaxClient,
    storage: StorageBackend,
    chat_id: int,
    unit: PublishUnit,
    post: TgPost,
):
    """Upload media and send a single publish unit to MAX."""
    async with async_session() as db:
        # Mark uploading
        await db.execute(
            update(PublishUnit)
            .where(PublishUnit.id == unit.id)
            .values(status=PublishUnitStatus.uploading)
        )
        await db.commit()

        # Get media for this unit
        result = await db.execute(
            select(TgMedia)
            .where(TgMedia.tg_post_id == unit.tg_post_id)
            .order_by(TgMedia.album_index)
        )
        all_media = result.scalars().all()
        unit_media = all_media[unit.attachments_from : unit.attachments_to]

        # Upload media and build attachments
        attachments = []
        for media in unit_media:
            upload_type = _max_upload_type(media.media_type)
            upload = await max_client.request_upload(upload_type)

            # Read from storage and save to temp, then upload
            data = await storage.read(media.file_path)

            import tempfile
            import os

            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(media.file_path)[1]) as tmp:
                tmp.write(data)
                tmp_path = tmp.name

            try:
                uploaded_token = await max_client.upload_file(upload.url, tmp_path)
            finally:
                os.unlink(tmp_path)

            attachment_token = uploaded_token or upload.token
            if not attachment_token:
                raise RuntimeError(
                    f"Upload token missing for media={media.id} type={upload_type}"
                )

            attachments.append({
                "type": upload_type,
                "payload": {"token": attachment_token},
            })

        # Mark sending
        await db.execute(
            update(PublishUnit)
            .where(PublishUnit.id == unit.id)
            .values(status=PublishUnitStatus.sending)
        )
        await db.commit()

        # Send message
        raw_text = post.text if unit.unit_index == 0 else None
        text, format_ = to_max_text_payload(raw_text)
        if not text and not attachments:
            logger.info("Skipping empty publish unit %s", unit.id)
            await db.execute(
                update(PublishUnit)
                .where(PublishUnit.id == unit.id)
                .values(status=PublishUnitStatus.sent, max_message_id=None, error_text=None)
            )
            await db.commit()
            return

        result = await max_client.send_message_with_retry(
            chat_id=chat_id,
            text=text,
            attachments=attachments if attachments else None,
            format_=format_,
        )

        # Mark sent
        msg_id = str(result.get("message", {}).get("body", {}).get("mid", ""))
        await db.execute(
            update(PublishUnit)
            .where(PublishUnit.id == unit.id)
            .values(status=PublishUnitStatus.sent, max_message_id=msg_id)
        )
        await db.commit()


def _max_upload_type(media_type) -> str:
    from app.models.tg_media import MediaType

    mapping = {
        MediaType.photo: "image",
        MediaType.video: "video",
        MediaType.audio: "audio",
        MediaType.voice: "audio",
        MediaType.document: "file",
        MediaType.other: "file",
    }
    return mapping.get(media_type, "file")


async def _fail_migration(db: AsyncSession, migration_id: uuid.UUID, error: str):
    """Mark migration as failed with error log."""
    try:
        await transition(db, migration_id, MigrationStatus.failed)
    except Exception:
        # Force update if transition fails (e.g. already failed)
        await db.execute(
            update(Migration)
            .where(Migration.id == migration_id)
            .values(status=MigrationStatus.failed)
        )
        await db.commit()

    await _log_event(db, migration_id, EventPhase.import_, EventType.error, error)


async def _log_event(
    db: AsyncSession,
    migration_id: uuid.UUID,
    phase: EventPhase,
    event_type: EventType,
    message: str,
):
    db.add(
        JobEvent(
            migration_id=migration_id,
            event_type=event_type,
            phase=phase,
            message=message[:1000],
        )
    )
    await db.commit()
