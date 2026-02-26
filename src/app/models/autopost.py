import enum
import uuid

from sqlalchemy import BigInteger, Boolean, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class AutopostStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    failed = "failed"


class AutopostLink(Base, TimestampMixin):
    __tablename__ = "autopost_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tg_connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tg_connections.id", ondelete="CASCADE")
    )
    max_connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("max_connections.id", ondelete="CASCADE")
    )

    tg_channel_peer: Mapped[str] = mapped_column(Text)
    tg_channel_title: Mapped[str] = mapped_column(Text)
    max_chat_id: Mapped[int] = mapped_column(BigInteger)
    max_chat_title: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[AutopostStatus] = mapped_column(
        Enum(AutopostStatus, name="autopost_status"),
        default=AutopostStatus.active,
    )

    last_tg_message_id: Mapped[int] = mapped_column(Integer, default=0)
    forwarded_count: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
