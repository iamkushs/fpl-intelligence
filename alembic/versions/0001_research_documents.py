"""create research documents

Revision ID: 0001_research_documents
Revises:
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_research_documents"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("research_job_id", sa.String(length=36), nullable=True),
        sa.Column("research_run_id", sa.String(length=36), nullable=True),
        sa.Column("research_section_id", sa.String(length=36), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("research_cutoff", sa.DateTime(timezone=True), nullable=True),
        sa.Column("season_id", sa.String(length=64), nullable=True),
        sa.Column("gameweek_id", sa.Integer(), nullable=True),
        sa.Column("codex_thread_id", sa.String(length=255), nullable=False),
        sa.Column("codex_turn_id", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CURRENT"),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("usage_metadata", sa.JSON(), nullable=True),
        sa.Column("execution_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["supersedes_id"], ["research_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "research_job_id",
        "research_run_id",
        "research_section_id",
        "season_id",
        "gameweek_id",
        "codex_thread_id",
        "status",
        "supersedes_id",
    ):
        op.create_index(f"ix_research_documents_{column}", "research_documents", [column], unique=False)


def downgrade() -> None:
    op.drop_table("research_documents")
