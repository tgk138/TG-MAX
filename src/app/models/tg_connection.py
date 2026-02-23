import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class TgConnectionStatus(str, enum.Enum):
    new = "new"
    code_sent = "code_sent"
    authed = "authed"
    revoked = "revoked"


class TgConnection(Base, TimestampMixin):
    __tablename__ = "tg_connections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    phone: Mapped[str] = mapped_column(Text)
    status: Mapped[TgConnectionStatus] = mapped_column(
        Enum(TgConnectionStatus, name="tg_conn_status"),
        default=TgConnectionStatus.new,
    )
    session_encrypted: Mapped[str | None] = mapped_column(Text)
