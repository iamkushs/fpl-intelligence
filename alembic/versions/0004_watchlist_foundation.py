"""add watchlist foundation

Revision ID: 0004_watchlist_foundation
Revises: 0003_research_persistence_foundation
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_watchlist_foundation"
down_revision = "0003_research_persistence_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlist_entries",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("pinned", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("added_source", sa.String(32), nullable=False),
        sa.Column("addition_reason", sa.Text(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removal_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("added_source IN ('user', 'research', 'system')", name="ck_watchlist_entries_added_source"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_watchlist_entries_player_id", "watchlist_entries", ["player_id"], unique=True)
    op.create_index("ix_watchlist_entries_active", "watchlist_entries", ["active"])


def downgrade() -> None:
    op.drop_index("ix_watchlist_entries_active", table_name="watchlist_entries")
    op.drop_index("ix_watchlist_entries_player_id", table_name="watchlist_entries")
    op.drop_table("watchlist_entries")
