import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class TgPost(Base):
    __tablename__ = "tg_posts"
    __table_args__ = (
        UniqueConstraint("migration_id", "tg_message_id", name="uq_tg_post_msg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    migration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("migrations.id", ondelete="CASCADE")
    )
    tg_message_id: Mapped[int] = mapped_column(BigInteger)
    album_key: Mapped[str | None] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    views: Mapped[int] = mapped_column(Integer, default=0)
    reactions_total: Mapped[int] = mapped_column(Integer, default=0)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=True)
