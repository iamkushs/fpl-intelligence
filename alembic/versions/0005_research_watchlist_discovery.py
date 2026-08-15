"""add research-driven watchlist discovery

Revision ID: 0005_research_watchlist_discovery
Revises: 0004_watchlist_foundation
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_research_watchlist_discovery"
down_revision = "0004_watchlist_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlist_suggestions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("research_thread_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="ck_watchlist_suggestions_status"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_thread_id"], ["research_threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "research_thread_id", name="uq_watchlist_suggestions_player_thread"),
    )
    op.create_index("ix_watchlist_suggestions_player_id", "watchlist_suggestions", ["player_id"])
    op.create_index("ix_watchlist_suggestions_research_thread_id", "watchlist_suggestions", ["research_thread_id"])
    op.create_index("ix_watchlist_suggestions_status", "watchlist_suggestions", ["status"])
    op.create_index(
        "uq_watchlist_suggestions_pending_player", "watchlist_suggestions", ["player_id"],
        unique=True, postgresql_where=sa.text("status = 'pending'"), sqlite_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "watchlist_suggestion_results",
        sa.Column("suggestion_id", sa.String(36), nullable=False),
        sa.Column("research_result_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["suggestion_id"], ["watchlist_suggestions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_result_id"], ["research_results.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("suggestion_id", "research_result_id"),
    )


def downgrade() -> None:
    op.drop_table("watchlist_suggestion_results")
    op.drop_index("uq_watchlist_suggestions_pending_player", table_name="watchlist_suggestions")
    op.drop_index("ix_watchlist_suggestions_status", table_name="watchlist_suggestions")
    op.drop_index("ix_watchlist_suggestions_research_thread_id", table_name="watchlist_suggestions")
    op.drop_index("ix_watchlist_suggestions_player_id", table_name="watchlist_suggestions")
    op.drop_table("watchlist_suggestions")
