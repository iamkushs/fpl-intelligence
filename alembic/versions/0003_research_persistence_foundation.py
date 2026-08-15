"""add research persistence foundation

Revision ID: 0003_research_persistence_foundation
Revises: 0002_research_orchestration
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_research_persistence_foundation"
down_revision = "0002_research_orchestration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("players", sa.Column("id", sa.Integer(), nullable=False), sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "research_threads",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("title", sa.String(255), nullable=False),
        sa.Column("thread_type", sa.String(32), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("gameweek_id", sa.Integer(), nullable=True), sa.Column("question", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("thread_type", "status", "gameweek_id"):
        op.create_index(f"ix_research_threads_{column}", "research_threads", [column])
    op.create_table(
        "research_links",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("research_thread_id", sa.String(36), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False), sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(512), nullable=True), sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=True), sa.Column("relevance_reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["research_thread_id"], ["research_threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("research_thread_id", "canonical_url", name="uq_research_links_thread_canonical_url"),
    )
    for column in ("research_thread_id", "domain", "status"):
        op.create_index(f"ix_research_links_{column}", "research_links", [column])
    op.create_table(
        "research_results",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("research_thread_id", sa.String(36), nullable=False),
        sa.Column("research_link_id", sa.String(36), nullable=False), sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("findings", sa.Text(), nullable=False), sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("uncertainty", sa.Text(), nullable=True), sa.Column("researched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["research_thread_id"], ["research_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_link_id"], ["research_links.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_results_research_thread_id", "research_results", ["research_thread_id"])
    op.create_index("ix_research_results_research_link_id", "research_results", ["research_link_id"])
    op.create_table("research_link_players", sa.Column("research_link_id", sa.String(36), nullable=False), sa.Column("player_id", sa.Integer(), nullable=False), sa.ForeignKeyConstraint(["research_link_id"], ["research_links.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("research_link_id", "player_id"))
    op.create_table("research_result_players", sa.Column("research_result_id", sa.String(36), nullable=False), sa.Column("player_id", sa.Integer(), nullable=False), sa.ForeignKeyConstraint(["research_result_id"], ["research_results.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("research_result_id", "player_id"))


def downgrade() -> None:
    op.drop_table("research_result_players")
    op.drop_table("research_link_players")
    op.drop_table("research_results")
    op.drop_table("research_links")
    op.drop_table("research_threads")
    op.drop_table("players")
