"""two-phase import, selection, and preview metadata

Revision ID: 20260223_0002
Revises: 20260223_0001
Create Date: 2026-02-23 19:25:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260223_0002"
down_revision: Union[str, None] = "20260223_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("migrations", sa.Column("selected_posts_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("migrations", sa.Column("prepared_media_total", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("migrations", sa.Column("preparing_media", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.add_column("tg_posts", sa.Column("posted_at", sa.DateTime(), nullable=True))
    op.add_column("tg_posts", sa.Column("views", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tg_posts", sa.Column("reactions_total", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tg_posts", sa.Column("is_selected", sa.Boolean(), nullable=False, server_default=sa.true()))

    op.create_index("ix_tg_posts_migration_posted_at", "tg_posts", ["migration_id", "posted_at"], unique=False)
    op.create_index("ix_tg_posts_migration_views", "tg_posts", ["migration_id", "views"], unique=False)
    op.create_index("ix_tg_posts_migration_reactions", "tg_posts", ["migration_id", "reactions_total"], unique=False)
    op.create_index("ix_tg_posts_migration_selected", "tg_posts", ["migration_id", "is_selected"], unique=False)

    op.add_column("tg_media", sa.Column("preview_path", sa.Text(), nullable=True))
    op.add_column("tg_media", sa.Column("full_path", sa.Text(), nullable=True))
    op.add_column("tg_media", sa.Column("source_message_id", sa.BigInteger(), nullable=True))
    op.add_column("tg_media", sa.Column("is_full_downloaded", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tg_media", sa.Column("preview_width", sa.Integer(), nullable=True))
    op.add_column("tg_media", sa.Column("preview_height", sa.Integer(), nullable=True))

    # Backfill historical media as already full-downloaded to avoid regressions.
    op.execute(
        """
        UPDATE tg_media
        SET full_path = file_path,
            preview_path = file_path,
            is_full_downloaded = TRUE
        WHERE file_path IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("tg_media", "preview_height")
    op.drop_column("tg_media", "preview_width")
    op.drop_column("tg_media", "is_full_downloaded")
    op.drop_column("tg_media", "source_message_id")
    op.drop_column("tg_media", "full_path")
    op.drop_column("tg_media", "preview_path")

    op.drop_index("ix_tg_posts_migration_selected", table_name="tg_posts")
    op.drop_index("ix_tg_posts_migration_reactions", table_name="tg_posts")
    op.drop_index("ix_tg_posts_migration_views", table_name="tg_posts")
    op.drop_index("ix_tg_posts_migration_posted_at", table_name="tg_posts")

    op.drop_column("tg_posts", "is_selected")
    op.drop_column("tg_posts", "reactions_total")
    op.drop_column("tg_posts", "views")
    op.drop_column("tg_posts", "posted_at")

    op.drop_column("migrations", "preparing_media")
    op.drop_column("migrations", "prepared_media_total")
    op.drop_column("migrations", "selected_posts_count")
