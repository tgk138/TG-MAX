import uuid
from datetime import datetime
import asyncio

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


def reset_db_engine() -> None:
    """Recreate async engine/sessionmaker (needed for Celery prefork workers)."""
    global engine
    old_engine = engine
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        poolclass=NullPool,
    )
    async_session.configure(bind=engine)
    try:
        asyncio.run(old_engine.dispose())
    except RuntimeError:
        # If called under active loop, schedule best-effort disposal.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(old_engine.dispose())
        except RuntimeError:
            pass
