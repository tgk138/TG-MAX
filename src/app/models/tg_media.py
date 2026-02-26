import enum
import uuid

from sqlalchemy import BigInteger, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class MediaType(str, enum.Enum):
    photo = "photo"
    video = "video"
    video_note = "video_note"
    document = "document"
    audio = "audio"
    voice = "voice"
    other = "other"


class TgMedia(Base):
    __tablename__ = "tg_media"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tg_post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tg_posts.id", ondelete="CASCADE")
    )
    album_index: Mapped[int] = mapped_column(Integer, default=0)
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType, name="media_type"))
    file_path: Mapped[str] = mapped_column(Text)
    preview_path: Mapped[str | None] = mapped_column(Text)
    full_path: Mapped[str | None] = mapped_column(Text)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    is_full_downloaded: Mapped[bool] = mapped_column(default=False)
    preview_width: Mapped[int | None] = mapped_column(Integer)
    preview_height: Mapped[int | None] = mapped_column(Integer)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    mime_type: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
