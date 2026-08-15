"""add trigger queue and monitoring triggers

Revision ID: 0007_trigger_queue_foundation
Revises: 0006_player_gameweek_pulses
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_trigger_queue_foundation"
down_revision = "0006_player_gameweek_pulses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitoring_triggers",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("research_result_id", sa.String(36), nullable=True),
        sa.Column("research_thread_id", sa.String(36), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("condition", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("satisfied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('appearance', 'minutes', 'attacking_return', 'set_piece', 'availability', "
            "'team_selection', 'transfer', 'tactical_role', 'fixture', 'manager_comment', 'other')",
            name="ck_monitoring_triggers_category",
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_result_id"], ["research_results.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["research_thread_id"], ["research_threads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitoring_triggers_player_active", "monitoring_triggers", ["player_id", "active"])
    op.create_index("ix_monitoring_triggers_active", "monitoring_triggers", ["active"])
    op.create_index("ix_monitoring_triggers_category", "monitoring_triggers", ["category"])
    op.create_index("ix_monitoring_triggers_research_result_id", "monitoring_triggers", ["research_result_id"])
    op.create_index("ix_monitoring_triggers_research_thread_id", "monitoring_triggers", ["research_thread_id"])

    op.create_table(
        "player_research_triggers",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(64), nullable=False),
        sa.Column("episode_key", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("gameweek", sa.Integer(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("monitoring_trigger_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'queued', 'resolved', 'dismissed')", name="ck_player_research_triggers_status"),
        sa.CheckConstraint("source IN ('pulse', 'research', 'system', 'user')", name="ck_player_research_triggers_source"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["monitoring_trigger_id"], ["monitoring_triggers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_player_research_triggers_player_status", "player_research_triggers", ["player_id", "status"])
    op.create_index("ix_player_research_triggers_status", "player_research_triggers", ["status"])
    op.create_index("ix_player_research_triggers_trigger_type", "player_research_triggers", ["trigger_type"])
    op.create_index("ix_player_research_triggers_gameweek", "player_research_triggers", ["gameweek"])
    op.create_index("ix_player_research_triggers_monitoring_trigger_id", "player_research_triggers", ["monitoring_trigger_id"])
    op.create_index(
        "uq_player_research_triggers_active_episode", "player_research_triggers",
        ["player_id", "trigger_type", "episode_key"], unique=True,
        sqlite_where=sa.text("status IN ('open', 'queued')"),
        postgresql_where=sa.text("status IN ('open', 'queued')"),
    )


def downgrade() -> None:
    op.drop_index("uq_player_research_triggers_active_episode", table_name="player_research_triggers")
    op.drop_index("ix_player_research_triggers_monitoring_trigger_id", table_name="player_research_triggers")
    op.drop_index("ix_player_research_triggers_gameweek", table_name="player_research_triggers")
    op.drop_index("ix_player_research_triggers_trigger_type", table_name="player_research_triggers")
    op.drop_index("ix_player_research_triggers_status", table_name="player_research_triggers")
    op.drop_index("ix_player_research_triggers_player_status", table_name="player_research_triggers")
    op.drop_table("player_research_triggers")
    op.drop_index("ix_monitoring_triggers_research_thread_id", table_name="monitoring_triggers")
    op.drop_index("ix_monitoring_triggers_research_result_id", table_name="monitoring_triggers")
    op.drop_index("ix_monitoring_triggers_category", table_name="monitoring_triggers")
    op.drop_index("ix_monitoring_triggers_active", table_name="monitoring_triggers")
    op.drop_index("ix_monitoring_triggers_player_active", table_name="monitoring_triggers")
    op.drop_table("monitoring_triggers")
