"""add lightweight research situations

Revision ID: 0008_research_situations
Revises: 0007_trigger_queue_foundation
Create Date: 2026-08-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_research_situations"
down_revision = "0007_trigger_queue_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_situations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("club_id", sa.Integer(), nullable=True),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("fpl_relevance", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'leaning', 'resolved')", name="ck_research_situations_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_situations_status", "research_situations", ["status"])
    op.create_index("ix_research_situations_club_id", "research_situations", ["club_id"])
    op.create_index("ix_research_situations_club_status", "research_situations", ["club_id", "status"])

    op.create_table(
        "situation_players",
        sa.Column("situation_id", sa.String(36), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["situation_id"], ["research_situations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("situation_id", "player_id"),
    )
    op.create_index("ix_situation_players_player_id", "situation_players", ["player_id"])

    op.create_table(
        "situation_hypotheses",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("situation_id", sa.String(36), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["situation_id"], ["research_situations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_situation_hypotheses_situation_id", "situation_hypotheses", ["situation_id"])
    op.create_index("ix_situation_hypotheses_active", "situation_hypotheses", ["active"])
    op.create_index(
        "ix_situation_hypotheses_situation_active",
        "situation_hypotheses",
        ["situation_id", "active"],
    )

    with op.batch_alter_table("player_research_triggers") as batch_op:
        batch_op.add_column(sa.Column("situation_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key(
            "fk_player_research_triggers_situation_id",
            "research_situations",
            ["situation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_player_research_triggers_situation_id", ["situation_id"])

    with op.batch_alter_table("research_threads") as batch_op:
        batch_op.add_column(sa.Column("situation_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key(
            "fk_research_threads_situation_id",
            "research_situations",
            ["situation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_research_threads_situation_id", ["situation_id"])


def downgrade() -> None:
    with op.batch_alter_table("research_threads") as batch_op:
        batch_op.drop_index("ix_research_threads_situation_id")
        batch_op.drop_constraint("fk_research_threads_situation_id", type_="foreignkey")
        batch_op.drop_column("situation_id")

    with op.batch_alter_table("player_research_triggers") as batch_op:
        batch_op.drop_index("ix_player_research_triggers_situation_id")
        batch_op.drop_constraint("fk_player_research_triggers_situation_id", type_="foreignkey")
        batch_op.drop_column("situation_id")

    op.drop_index("ix_situation_hypotheses_situation_active", table_name="situation_hypotheses")
    op.drop_index("ix_situation_hypotheses_active", table_name="situation_hypotheses")
    op.drop_index("ix_situation_hypotheses_situation_id", table_name="situation_hypotheses")
    op.drop_table("situation_hypotheses")

    op.drop_index("ix_situation_players_player_id", table_name="situation_players")
    op.drop_table("situation_players")

    op.drop_index("ix_research_situations_club_status", table_name="research_situations")
    op.drop_index("ix_research_situations_club_id", table_name="research_situations")
    op.drop_index("ix_research_situations_status", table_name="research_situations")
    op.drop_table("research_situations")
