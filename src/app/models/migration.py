import enum
import uuid

from sqlalchemy import BigInteger, Boolean, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class MigrationStatus(str, enum.Enum):
    draft = "draft"
    importing = "importing"
    imported = "imported"
    publishing = "publishing"
    done = "done"
    failed = "failed"


class Migration(Base, TimestampMixin):
    __tablename__ = "migrations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tg_connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tg_connections.id", ondelete="CASCADE")
    )
    tg_channel_peer: Mapped[str] = mapped_column(Text)
    tg_channel_title: Mapped[str] = mapped_column(Text)

    max_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("max_connections.id", ondelete="SET NULL")
    )
    max_target_chat_id: Mapped[int | None] = mapped_column(BigInteger)

    status: Mapped[MigrationStatus] = mapped_column(
        Enum(MigrationStatus, name="migration_status"),
        default=MigrationStatus.draft,
    )

    # Counters
    total_posts: Mapped[int] = mapped_column(Integer, default=0)
    imported_posts: Mapped[int] = mapped_column(Integer, default=0)
    total_media: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_media: Mapped[int] = mapped_column(Integer, default=0)
    published_units: Mapped[int] = mapped_column(Integer, default=0)
    failed_units: Mapped[int] = mapped_column(Integer, default=0)
    selected_posts_count: Mapped[int] = mapped_column(Integer, default=0)
    prepared_media_total: Mapped[int] = mapped_column(Integer, default=0)
    preparing_media: Mapped[bool] = mapped_column(Boolean, default=False)
