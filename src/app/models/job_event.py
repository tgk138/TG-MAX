import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class EventType(str, enum.Enum):
    info = "info"
    warning = "warning"
    error = "error"
    progress = "progress"


class EventPhase(str, enum.Enum):
    import_ = "import"
    publish = "publish"


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    migration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("migrations.id", ondelete="CASCADE")
    )
    event_type: Mapped[EventType] = mapped_column(Enum(EventType, name="event_type"))
    phase: Mapped[EventPhase] = mapped_column(Enum(EventPhase, name="event_phase"))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
