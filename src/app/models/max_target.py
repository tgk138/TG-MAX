import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class MaxTarget(Base):
    __tablename__ = "max_targets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    max_connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("max_connections.id", ondelete="CASCADE")
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None]
