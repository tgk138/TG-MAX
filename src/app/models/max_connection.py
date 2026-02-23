import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class MaxConnection(Base, TimestampMixin):
    __tablename__ = "max_connections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    bot_token_encrypted: Mapped[str] = mapped_column(Text)
    bot_name: Mapped[str | None] = mapped_column(Text)
