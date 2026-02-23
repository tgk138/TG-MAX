import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class PublishUnitStatus(str, enum.Enum):
    pending = "pending"
    uploading = "uploading"
    sending = "sending"
    sent = "sent"
    failed = "failed"


class PublishUnit(Base):
    __tablename__ = "publish_units"
    __table_args__ = (
        UniqueConstraint("tg_post_id", "unit_index", name="uq_publish_unit_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tg_post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tg_posts.id", ondelete="CASCADE")
    )
    unit_index: Mapped[int] = mapped_column(Integer, default=0)
    attachments_from: Mapped[int] = mapped_column(Integer, default=0)
    attachments_to: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[PublishUnitStatus] = mapped_column(
        Enum(PublishUnitStatus, name="publish_unit_status"),
        default=PublishUnitStatus.pending,
    )
    max_message_id: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_text: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
