"""Telegram service using Telethon for MTProto auth and channel export."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import settings
from app.models.job_event import EventPhase, EventType, JobEvent
from app.models.migration import Migration, MigrationStatus
from app.models.tg_media import MediaType, TgMedia
from app.models.tg_post import TgPost
from app.services import secrets
from app.services.storage import StorageBackend
from app.services.text_format import normalize_tg_text

logger = logging.getLogger("uvicorn.error")


@dataclass
class ChannelInfo:
    peer_id: int
    title: str
    username: str | None
    is_admin: bool


def _media_type_from_tg(message) -> MediaType:
    """Determine MediaType from a Telethon message."""
    if getattr(message, "video_note", None):
        return MediaType.video_note
    if message.photo:
        return MediaType.photo
    if message.video:
        return MediaType.video
    if message.audio or message.voice:
        return MediaType.audio
    if message.document:
        return MediaType.document
    return MediaType.other


def _mime_from_message(message) -> str | None:
    if message.document and hasattr(message.document, "mime_type"):
        return message.document.mime_type
    return None


def _reactions_total_from_message(message) -> int:
    reactions = getattr(message, "reactions", None)
    if not reactions:
        return 0
    results = getattr(reactions, "results", None)
    if not results:
        return 0
    total = 0
    for item in results:
        total += int(getattr(item, "count", 0) or 0)
    return total


def _to_naive_utc(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        return dt
    return dt.replace(tzinfo=None)


class TelegramService:
    """Manages Telethon client lifecycle and channel export."""

    def __init__(self, session_string: str | None = None):
        self._session = StringSession(session_string or "")
        self.client = TelegramClient(
            self._session,
            settings.TELEGRAM_API_ID,
            settings.TELEGRAM_API_HASH,
            device_model=settings.TELEGRAM_DEVICE_MODEL,
            system_version=settings.TELEGRAM_SYSTEM_VERSION,
            app_version=settings.TELEGRAM_APP_VERSION,
            lang_code=settings.TELEGRAM_LANG_CODE,
            system_lang_code=settings.TELEGRAM_SYSTEM_LANG_CODE,
        )

    async def connect(self):
        await self.client.connect()

    async def disconnect(self):
        await self.client.disconnect()

    def get_session_string(self) -> str:
        return self._session.save()

    # --- Auth flow ---

    async def send_code(self, phone: str):
        """Send auth code to phone."""
        await self.connect()
        sent_code = await self.client.send_code_request(phone)

        if settings.TELEGRAM_LOG_FULL_SEND_CODE_RESPONSE:
            payload = self._serialize_tl_object(sent_code)
            logger.info(
                "Telegram send_code_request response phone=%s payload=%s",
                self._mask_phone(phone),
                json.dumps(payload, ensure_ascii=False, default=str),
            )
        else:
            logger.info("Telegram send_code_request success phone=%s", self._mask_phone(phone))

        return sent_code

    async def sign_in_code(self, phone: str, code: str):
        """Sign in with code. Returns user or raises SessionPasswordNeededError."""
        return await self.client.sign_in(phone, code)

    async def sign_in_password(self, password: str):
        """Sign in with 2FA password."""
        return await self.client.sign_in(password=password)

    async def start_qr_login(self) -> str:
        """Start QR login; returns URL to show as QR. Caller must later await wait_qr_complete()."""
        await self.connect()
        qr = await self.client.qr_login()
        # Store qr on instance so API can wait on it
        self._qr = qr  # type: ignore[attr-defined]
        return qr.url

    async def wait_qr_complete(self, timeout: float = 120.0) -> None:
        """Wait until user scans QR (or timeout). Call after start_qr_login()."""
        qr = getattr(self, "_qr", None)
        if not qr:
            raise RuntimeError("No QR login started")
        await qr.wait(timeout=timeout)

    async def get_me(self):
        """Return current Telegram user for active session."""
        return await self.client.get_me()

    async def get_profile_photo_bytes(self) -> bytes | None:
        """Return current user's profile photo bytes, if available."""
        photo = await self.client.download_profile_photo("me", file=bytes)
        if isinstance(photo, bytes):
            return photo
        return None

    # --- Channel operations ---

    async def list_channels(self) -> list[ChannelInfo]:
        """List only broadcast channels where user is admin/creator."""
        channels = []
        async for dialog in self.client.iter_dialogs():
            if not dialog.is_channel:
                continue

            entity = dialog.entity
            is_broadcast = bool(getattr(entity, "broadcast", False))
            is_admin = bool(getattr(dialog, "is_admin", False) or getattr(entity, "creator", False))
            if not is_broadcast or not is_admin:
                continue

            channels.append(
                ChannelInfo(
                    peer_id=dialog.id,
                    title=dialog.title or "",
                    username=getattr(entity, "username", None),
                    is_admin=is_admin,
                )
            )
        return channels

    async def preview_channel_posts(self, peer_id: int, limit: int = 20) -> list[dict]:
        """Fetch latest posts text/media metadata without downloading files."""
        entity = await self.client.get_entity(peer_id)
        items: list[dict] = []
        async for msg in self.client.iter_messages(entity, limit=limit):
            text = normalize_tg_text(msg.text or msg.raw_text, getattr(msg, 'entities', None))
            items.append(
                {
                    "message_id": msg.id,
                    "text": text,
                    "has_media": bool(msg.media),
                    "views": int(getattr(msg, "views", 0) or 0),
                    "reactions_total": _reactions_total_from_message(msg),
                    "date": msg.date.isoformat() if getattr(msg, "date", None) else None,
                }
            )
        # iter_messages returns newest first; reverse for chronological preview.
        items.reverse()
        return items

    async def export_channel(
        self,
        db: AsyncSession,
        storage: StorageBackend,
        migration_id: uuid.UUID,
        peer_id: int,
    ) -> None:
        """Export entire channel: messages + media -> DB + storage.

        Streaming approach: processes messages as they arrive instead of
        buffering all first. Albums are grouped within a small window.
        Media previews are downloaded concurrently (up to 4 at a time).
        """
        batch_size = settings.TG_IMPORT_BATCH_SIZE
        delay_sec = max(0.05, settings.TG_IMPORT_DELAY_MS / 1000.0)
        counter_update_interval = min(batch_size, 25)

        entity = await self.client.get_entity(peer_id)

        # Phase 1: fast metadata scan (no media download) to get totals
        from telethon.tl.types import MessageService

        total_posts_est = 0
        total_media_est = 0
        message_buffer: list = []

        async for message in self.client.iter_messages(entity, limit=None):
            if isinstance(message, MessageService):
                continue
            if not message.text and not message.raw_text and not message.media:
                continue
            message_buffer.append(message)
            if message.media:
                total_media_est += 1
            if not message.grouped_id:
                total_posts_est += 1

        # Count albums as single posts
        grouped_ids = {m.grouped_id for m in message_buffer if m.grouped_id}
        total_posts_est += len(grouped_ids)

        await db.execute(
            update(Migration)
            .where(Migration.id == migration_id)
            .values(
                total_posts=total_posts_est,
                total_media=total_media_est,
                selected_posts_count=0,
                prepared_media_total=0,
                downloaded_media=0,
                preparing_media=False,
            )
        )
        await db.commit()

        await self._log_event(
            db, migration_id, EventType.info,
            f"Found ~{total_posts_est} posts, {total_media_est} media. Starting import...",
        )

        # Phase 2: group albums and process in chronological order
        album_buffer: dict[int, list] = {}
        solo_messages: list = []

        for message in message_buffer:
            if message.grouped_id:
                album_buffer.setdefault(message.grouped_id, []).append(message)
            else:
                solo_messages.append(message)
        del message_buffer  # free memory

        solo_messages.reverse()
        albums = []
        for gid, msgs in album_buffer.items():
            msgs.sort(key=lambda m: m.id)
            albums.append((gid, msgs))
        albums.sort(key=lambda x: x[1][0].id)
        del album_buffer

        # Merge into chronological order
        all_posts = []
        solo_iter = iter(solo_messages)
        album_iter = iter(albums)
        next_solo = next(solo_iter, None)
        next_album = next(album_iter, None)

        while next_solo is not None or next_album is not None:
            solo_id = next_solo.id if next_solo else float("inf")
            album_first_id = next_album[1][0].id if next_album else float("inf")
            if solo_id <= album_first_id:
                all_posts.append(("solo", next_solo))
                next_solo = next(solo_iter, None)
            else:
                all_posts.append(("album", next_album))
                next_album = next(album_iter, None)

        # Phase 3: persist posts + download previews concurrently
        order_index = 0
        imported_posts = 0
        previewed_media = 0
        media_sem = asyncio.Semaphore(4)

        async def _download_one_preview(msg, tg_post_id, album_idx):
            async with media_sem:
                return await self._download_media(
                    db, storage, migration_id, tg_post_id, [msg], album_idx,
                )

        for ptype, pdata in all_posts:
            if ptype == "solo":
                msg = pdata
                tg_post = TgPost(
                    migration_id=migration_id,
                    tg_message_id=msg.id,
                    album_key=None,
                    order_index=order_index,
                    text=normalize_tg_text(msg.text or msg.raw_text, getattr(msg, 'entities', None)),
                    has_media=msg.media is not None,
                    posted_at=_to_naive_utc(getattr(msg, "date", None)),
                    views=int(getattr(msg, "views", 0) or 0),
                    reactions_total=_reactions_total_from_message(msg),
                    is_selected=True,
                )
                db.add(tg_post)
                await db.flush()

                if msg.media:
                    previewed_media += await _download_one_preview(msg, tg_post.id, 0)

            else:
                gid, msgs = pdata
                text_msg = next((m for m in msgs if m.text or m.raw_text), None)
                text = normalize_tg_text(
                    text_msg.text or text_msg.raw_text if text_msg else None,
                    getattr(text_msg, 'entities', None) if text_msg else None,
                )
                tg_post = TgPost(
                    migration_id=migration_id,
                    tg_message_id=msgs[0].id,
                    album_key=f"tg:{peer_id}:{gid}",
                    order_index=order_index,
                    text=text,
                    has_media=True,
                    posted_at=_to_naive_utc(getattr(msgs[0], "date", None)),
                    views=int(getattr(msgs[0], "views", 0) or 0),
                    reactions_total=_reactions_total_from_message(msgs[0]),
                    is_selected=True,
                )
                db.add(tg_post)
                await db.flush()

                previewed_media += await self._download_media(
                    db, storage, migration_id, tg_post.id, msgs, 0,
                )

            order_index += 1
            imported_posts += 1

            if imported_posts % counter_update_interval == 0:
                await db.execute(
                    update(Migration)
                    .where(Migration.id == migration_id)
                    .values(
                        imported_posts=imported_posts,
                        selected_posts_count=imported_posts,
                        total_posts=max(total_posts_est, imported_posts),
                        downloaded_media=0,
                    )
                )
                await db.commit()
                await asyncio.sleep(delay_sec)

        await db.execute(
            update(Migration)
            .where(Migration.id == migration_id)
            .values(
                imported_posts=imported_posts,
                total_posts=imported_posts,
                selected_posts_count=imported_posts,
                downloaded_media=0,
                prepared_media_total=0,
                preparing_media=False,
            )
        )
        await db.commit()

        await self._log_event(
            db, migration_id, EventType.info,
            f"Import complete: {imported_posts} posts, {previewed_media} preview media",
        )

    async def fetch_new_posts(
        self,
        peer_id: int,
        min_id: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        """Fetch posts newer than min_id for autopost forwarding.

        Groups albums (grouped_id) into single post entries with multiple media items.
        Distinguishes video_notes (circles) from regular videos.
        """
        from telethon.tl.types import MessageService

        entity = await self.client.get_entity(peer_id)
        raw_messages: list = []
        async for msg in self.client.iter_messages(entity, limit=limit, min_id=min_id):
            if msg.id <= min_id:
                continue
            if isinstance(msg, MessageService):
                continue
            if not msg.text and not msg.raw_text and not msg.media:
                continue
            raw_messages.append(msg)

        raw_messages.sort(key=lambda m: m.id)

        # Group albums
        album_buffer: dict[int, list] = {}
        solo_messages: list = []
        for msg in raw_messages:
            if msg.grouped_id:
                album_buffer.setdefault(msg.grouped_id, []).append(msg)
            else:
                solo_messages.append(msg)

        all_entries: list = []
        for msg in solo_messages:
            all_entries.append(("solo", msg))
        for gid, msgs in sorted(album_buffer.items(), key=lambda x: x[1][0].id):
            msgs.sort(key=lambda m: m.id)
            all_entries.append(("album", msgs))
        all_entries.sort(key=lambda e: e[1].id if e[0] == "solo" else e[1][0].id)

        items: list[dict] = []
        for entry_type, entry_data in all_entries:
            if entry_type == "solo":
                msg = entry_data
                text = normalize_tg_text(msg.text or msg.raw_text, getattr(msg, 'entities', None))
                media_list = []
                if msg.media:
                    media_item = await self._download_for_autopost(msg)
                    if media_item:
                        media_list.append(media_item)
                items.append({
                    "message_id": msg.id,
                    "text": text,
                    "media_list": media_list,
                })
            else:
                msgs = entry_data
                text_msg = next((m for m in msgs if m.text or m.raw_text), None)
                text = normalize_tg_text(
                    text_msg.text or text_msg.raw_text if text_msg else None,
                    getattr(text_msg, 'entities', None) if text_msg else None,
                )
                media_list = []
                for msg in msgs:
                    if msg.media:
                        media_item = await self._download_for_autopost(msg)
                        if media_item:
                            media_list.append(media_item)
                items.append({
                    "message_id": msgs[-1].id,
                    "text": text,
                    "media_list": media_list,
                })

        return items

    async def _download_for_autopost(self, msg) -> dict | None:
        """Download media bytes and determine type for autopost forwarding."""
        try:
            media_bytes = await self.client.download_media(msg, bytes)
            if not media_bytes:
                return None

            is_video_note = bool(getattr(msg, "video_note", None))
            media_type = _media_type_from_tg(msg)

            return {
                "bytes": media_bytes,
                "media_type": media_type,
                "is_video_note": is_video_note,
                "ext": self._guess_extension(msg),
            }
        except Exception:
            logger.warning("Failed to download media for autopost msg %d", msg.id)
            return None

    async def _download_media(
        self,
        db: AsyncSession,
        storage: StorageBackend,
        migration_id: uuid.UUID,
        tg_post_id: uuid.UUID,
        messages: list,
        start_index: int,
    ) -> int:
        """Download low-res media previews and persist metadata rows."""
        count = 0
        for idx, msg in enumerate(messages):
            if not msg.media:
                continue

            media_id = uuid.uuid4()
            ext = self._guess_extension(msg)
            preview_key = f"{migration_id}/preview/{media_id}{ext}"
            placeholder_key = f"{migration_id}/pending/{media_id}{ext}"
            preview_bytes: bytes | None = None

            try:
                preview_bytes = await self._download_preview_bytes(msg)
                if preview_bytes:
                    await storage.save(preview_key, preview_bytes)
            except Exception:
                logger.exception("Failed to download media for msg %d", msg.id)
                preview_bytes = None

            tg_media = TgMedia(
                tg_post_id=tg_post_id,
                album_index=start_index + idx,
                media_type=_media_type_from_tg(msg),
                file_path=preview_key if preview_bytes else placeholder_key,
                preview_path=preview_key if preview_bytes else None,
                full_path=None,
                source_message_id=msg.id,
                is_full_downloaded=False,
                file_size=len(preview_bytes) if preview_bytes else 0,
                mime_type=_mime_from_message(msg),
            )
            db.add(tg_media)
            count += 1

        await db.flush()
        return count

    async def _download_preview_bytes(self, message) -> bytes | None:
        """Download a reasonable-quality preview for the migration page.

        Strategy: try the largest thumbnail first (-1), then full file.
        Cap at 2MB to keep storage manageable but still look sharp.
        """
        max_preview_size = 2 * 1024 * 1024  # 2MB

        # Try largest available thumbnail first (often good enough quality)
        for thumb in (-1, 0):
            try:
                data = await self.client.download_media(message, bytes, thumb=thumb)
            except Exception:
                continue
            if isinstance(data, bytes) and data:
                if len(data) <= max_preview_size:
                    return data
                return data[:max_preview_size]

        # Fall back to full file (truncated to cap)
        try:
            data = await self.client.download_media(message, bytes)
            if isinstance(data, bytes) and data:
                return data[:max_preview_size]
        except Exception:
            return None
        return None

    async def prepare_selected_media_full(
        self,
        db: AsyncSession,
        storage: StorageBackend,
        migration_id: uuid.UUID,
        peer_id: int,
        commit_every: int = 5,
    ) -> tuple[int, int]:
        """Download full-resolution media for selected posts only."""
        selected_posts_result = await db.execute(
            select(TgPost.id)
            .where(
                TgPost.migration_id == migration_id,
                TgPost.is_selected.is_(True),
            )
            .order_by(TgPost.order_index.asc())
        )
        selected_post_ids = [row[0] for row in selected_posts_result.all()]
        if not selected_post_ids:
            return 0, 0

        media_result = await db.execute(
            select(TgMedia)
            .where(TgMedia.tg_post_id.in_(selected_post_ids))
            .order_by(TgMedia.tg_post_id.asc(), TgMedia.album_index.asc())
        )
        media_rows = media_result.scalars().all()
        total_media = len(media_rows)
        if total_media == 0:
            return 0, 0

        entity = await self.client.get_entity(peer_id)
        message_ids = sorted({m.source_message_id for m in media_rows if m.source_message_id})
        messages_by_id: dict[int, object] = {}

        chunk_size = 200
        for i in range(0, len(message_ids), chunk_size):
            chunk = message_ids[i : i + chunk_size]
            fetched = await self.client.get_messages(entity, ids=chunk)
            if fetched is None:
                continue
            if not isinstance(fetched, list):
                fetched = [fetched]
            for msg in fetched:
                if msg and getattr(msg, "id", None):
                    messages_by_id[msg.id] = msg

        prepared = 0
        for media in media_rows:
            if media.is_full_downloaded and media.full_path:
                exists = await storage.exists(media.full_path)
                if exists:
                    prepared += 1
                    continue

            src_msg_id = media.source_message_id
            if not src_msg_id:
                continue

            msg = messages_by_id.get(src_msg_id)
            if not msg:
                continue

            try:
                data = await self.client.download_media(msg, bytes)
                if not data:
                    continue
                ext = self._guess_extension(msg)
                full_key = f"{migration_id}/full/{media.id}{ext}"
                await storage.save(full_key, data)
                media.full_path = full_key
                media.file_path = full_key
                media.file_size = len(data)
                media.is_full_downloaded = True
                prepared += 1
                if prepared % commit_every == 0:
                    await db.execute(
                        update(Migration)
                        .where(Migration.id == migration_id)
                        .values(downloaded_media=prepared)
                    )
                    await db.flush()
                    await db.commit()
            except Exception:
                logger.exception("Failed to download full media source_msg=%s", src_msg_id)

        await db.execute(
            update(Migration)
            .where(Migration.id == migration_id)
            .values(downloaded_media=prepared)
        )
        await db.flush()
        await db.commit()
        return prepared, total_media

    @staticmethod
    def _guess_extension(message) -> str:
        if message.photo:
            return ".jpg"
        if message.video:
            return ".mp4"
        if message.audio:
            return ".mp3"
        if message.voice:
            return ".ogg"
        if message.document:
            attrs = message.document.attributes if message.document else []
            for attr in attrs:
                if hasattr(attr, "file_name") and attr.file_name:
                    name = attr.file_name
                    if "." in name:
                        return "." + name.rsplit(".", 1)[1]
            return ".bin"
        return ".bin"

    @staticmethod
    def _mask_phone(phone: str) -> str:
        digits = "".join(ch for ch in phone if ch.isdigit())
        if len(digits) <= 4:
            return "***"
        return f"+***{digits[-4:]}"

    @staticmethod
    def _serialize_tl_object(obj) -> dict:
        """Serialize Telethon TLObject for debugging logs."""
        payload: dict
        if hasattr(obj, "to_dict"):
            try:
                payload = obj.to_dict()
            except Exception:
                payload = {"repr": repr(obj)}
        else:
            payload = {"repr": repr(obj)}

        # Always include explicit class for easier troubleshooting.
        payload["_class"] = obj.__class__.__name__
        return payload

    @staticmethod
    async def _log_event(
        db: AsyncSession,
        migration_id: uuid.UUID,
        event_type: EventType,
        message: str,
        details: dict | None = None,
    ):
        db.add(
            JobEvent(
                migration_id=migration_id,
                event_type=event_type,
                phase=EventPhase.import_,
                message=message,
                details=details,
            )
        )
        await db.commit()
