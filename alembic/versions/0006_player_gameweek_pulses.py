"""add player Gameweek pulses

Revision ID: 0006_player_gameweek_pulses
Revises: 0005_research_watchlist_discovery
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_player_gameweek_pulses"
down_revision = "0005_research_watchlist_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_gameweek_pulses",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("gameweek", sa.Integer(), nullable=False),
        *[sa.Column(name, sa.Integer(), nullable=True) for name in (
            "minutes", "starts", "total_points", "goals_scored", "assists", "clean_sheets",
            "goals_conceded", "own_goals", "penalties_saved", "penalties_missed", "yellow_cards",
            "red_cards", "saves", "bonus", "bps",
        )],
        *[sa.Column(name, sa.Float(), nullable=True) for name in (
            "expected_goals", "expected_assists", "expected_goal_involvements", "expected_goals_conceded",
        )],
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "gameweek", name="uq_player_gameweek_pulses_player_gameweek"),
    )
    op.create_index("ix_player_gameweek_pulses_gameweek", "player_gameweek_pulses", ["gameweek"])
    op.create_index("ix_player_gameweek_pulses_player_gameweek", "player_gameweek_pulses", ["player_id", "gameweek"])


def downgrade() -> None:
    op.drop_index("ix_player_gameweek_pulses_player_gameweek", table_name="player_gameweek_pulses")
    op.drop_index("ix_player_gameweek_pulses_gameweek", table_name="player_gameweek_pulses")
    op.drop_table("player_gameweek_pulses")
