"""Database bootstrap helpers.

Ensures all SQLAlchemy models are materialized in Postgres before the app
serves requests or workers start consuming jobs.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.models import Base, engine

logger = logging.getLogger(__name__)
_SCHEMA_LOCK_KEY = 87024155


async def ensure_db_schema(
    *,
    max_attempts: int = 30,
    delay_seconds: float = 1.0,
) -> None:
    """Create database tables with retries for cold starts."""
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.begin() as conn:
                # Avoid concurrent create_all from API + worker startup.
                await conn.execute(
                    text("SELECT pg_advisory_lock(:lock_key)"),
                    {"lock_key": _SCHEMA_LOCK_KEY},
                )
                try:
                    await conn.run_sync(Base.metadata.create_all)
                finally:
                    await conn.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": _SCHEMA_LOCK_KEY},
                    )
            logger.info("Database schema is ready.")
            return
        except SQLAlchemyError as exc:
            last_error = exc
            logger.warning(
                "DB schema bootstrap failed (attempt %s/%s): %s",
                attempt,
                max_attempts,
                exc,
            )
            if attempt < max_attempts:
                await asyncio.sleep(delay_seconds)

    assert last_error is not None
    raise RuntimeError("Unable to initialize database schema.") from last_error


def main() -> None:
    """CLI entrypoint for container startup scripts."""
    try:
        asyncio.run(ensure_db_schema())
        print("DB schema bootstrap: ok", flush=True)
    except Exception as exc:
        print(f"DB schema bootstrap failed: {exc}", file=sys.stderr, flush=True)
        raise


if __name__ == "__main__":
    main()
