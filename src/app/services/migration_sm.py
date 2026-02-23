"""Migration state machine with transactional transitions.

State transitions:
  draft -> importing -> imported -> publishing -> done
  importing -> failed
  publishing -> failed
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.migration import Migration, MigrationStatus

logger = logging.getLogger(__name__)

# Valid transitions: {current_status: [allowed_next_statuses]}
TRANSITIONS: dict[MigrationStatus, list[MigrationStatus]] = {
    MigrationStatus.draft: [MigrationStatus.importing],
    MigrationStatus.importing: [MigrationStatus.imported, MigrationStatus.failed],
    MigrationStatus.imported: [MigrationStatus.publishing],
    MigrationStatus.publishing: [MigrationStatus.done, MigrationStatus.failed],
    MigrationStatus.failed: [],  # terminal (retry resets publish_units, not migration status)
    MigrationStatus.done: [],  # terminal
}


class InvalidTransition(Exception):
    def __init__(self, current: MigrationStatus, target: MigrationStatus):
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from {current.value} to {target.value}")


async def transition(
    db: AsyncSession,
    migration_id: uuid.UUID,
    target_status: MigrationStatus,
) -> Migration:
    """Atomically transition a migration to a new status.

    Uses SELECT ... FOR UPDATE to lock the row, validates the transition,
    then updates. Raises InvalidTransition if the transition is not allowed.
    """
    result = await db.execute(
        select(Migration)
        .where(Migration.id == migration_id)
        .with_for_update()
    )
    migration = result.scalar_one_or_none()

    if migration is None:
        raise ValueError(f"Migration {migration_id} not found")

    allowed = TRANSITIONS.get(migration.status, [])
    if target_status not in allowed:
        raise InvalidTransition(migration.status, target_status)

    previous_status = migration.status
    migration.status = target_status
    await db.flush()
    await db.commit()

    logger.info(
        "Migration %s: %s -> %s",
        migration_id,
        previous_status.value,
        target_status.value,
    )
    return migration
