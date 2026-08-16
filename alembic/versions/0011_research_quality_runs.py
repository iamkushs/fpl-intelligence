"""add research quality runs

Revision ID: 0011_research_quality_runs
Revises: 0010_eval2_discovery_extraction
Create Date: 2026-08-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_research_quality_runs"
down_revision = "0010_eval2_discovery_extraction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_quality_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("thread_id", sa.String(36), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("situation_id", sa.String(36), nullable=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("target_evidence_id", sa.String(36), nullable=True),
        sa.Column("superseding_evidence_id", sa.String(36), nullable=True),
        sa.Column("research_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("challenged_claim", sa.Text(), nullable=True),
        sa.Column("questions", sa.JSON(), nullable=True),
        sa.Column("outcome", sa.String(64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], ["research_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["situation_id"], ["research_situations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_evidence_id"], ["research_evidence.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["superseding_evidence_id"], ["research_evidence.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_research_quality_runs_thread_id", ["thread_id"]),
        ("ix_research_quality_runs_player_id", ["player_id"]),
        ("ix_research_quality_runs_stage", ["stage"]),
        ("ix_research_quality_runs_status", ["status"]),
        ("ix_research_quality_runs_research_cutoff", ["research_cutoff"]),
    ):
        op.create_index(name, "research_quality_runs", columns)

    op.create_table(
        "research_quality_run_links",
        sa.Column("quality_run_id", sa.String(36), nullable=False),
        sa.Column("research_link_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["quality_run_id"], ["research_quality_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_link_id"], ["research_links.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("quality_run_id", "research_link_id"),
    )
    op.create_table(
        "research_quality_run_evidence",
        sa.Column("quality_run_id", sa.String(36), nullable=False),
        sa.Column("research_evidence_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["quality_run_id"], ["research_quality_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_evidence_id"], ["research_evidence.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("quality_run_id", "research_evidence_id"),
    )


def downgrade() -> None:
    op.drop_table("research_quality_run_evidence")
    op.drop_table("research_quality_run_links")
    for name in (
        "ix_research_quality_runs_research_cutoff",
        "ix_research_quality_runs_status",
        "ix_research_quality_runs_stage",
        "ix_research_quality_runs_player_id",
        "ix_research_quality_runs_thread_id",
    ):
        op.drop_index(name, table_name="research_quality_runs")
    op.drop_table("research_quality_runs")
