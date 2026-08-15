"""add durable research run, section, job and dependency state

Revision ID: 0002_research_orchestration
Revises: 0001_research_documents
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_research_orchestration"
down_revision = "0001_research_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("season_id", sa.String(length=64), nullable=True),
        sa.Column("gameweek_id", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("research_cutoff", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_runs_season_id", "research_runs", ["season_id"], unique=False)
    op.create_index("ix_research_runs_gameweek_id", "research_runs", ["gameweek_id"], unique=False)
    op.create_index("ix_research_runs_status", "research_runs", ["status"], unique=False)

    op.create_table(
        "research_sections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("research_run_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("ordering", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["research_run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_sections_research_run_id", "research_sections", ["research_run_id"], unique=False)
    op.create_index("ix_research_sections_status", "research_sections", ["status"], unique=False)

    op.create_table(
        "research_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("research_run_id", sa.String(length=36), nullable=False),
        sa.Column("research_section_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("ordering", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("codex_thread_id", sa.String(length=255), nullable=True),
        sa.Column("codex_turn_id", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_research_jobs_attempt_count_nonnegative"),
        sa.ForeignKeyConstraint(["research_run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_section_id"], ["research_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "research_run_id",
        "research_section_id",
        "status",
        "codex_thread_id",
    ):
        op.create_index(f"ix_research_jobs_{column}", "research_jobs", [column], unique=False)

    op.create_table(
        "research_job_dependencies",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("depends_on_job_id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("job_id <> depends_on_job_id", name="ck_research_job_dependencies_not_self"),
        sa.ForeignKeyConstraint(["job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "depends_on_job_id"),
    )
    op.create_index(
        "ix_research_job_dependencies_depends_on_job_id",
        "research_job_dependencies",
        ["depends_on_job_id"],
        unique=False,
    )

    with op.batch_alter_table("research_documents", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_research_documents_research_job_id",
            "research_jobs",
            ["research_job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_research_documents_research_run_id",
            "research_runs",
            ["research_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_research_documents_research_section_id",
            "research_sections",
            ["research_section_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("research_documents", schema=None) as batch_op:
        batch_op.drop_constraint("fk_research_documents_research_section_id", type_="foreignkey")
        batch_op.drop_constraint("fk_research_documents_research_run_id", type_="foreignkey")
        batch_op.drop_constraint("fk_research_documents_research_job_id", type_="foreignkey")
    op.drop_index("ix_research_job_dependencies_depends_on_job_id", table_name="research_job_dependencies")
    op.drop_table("research_job_dependencies")
    for column in ("research_run_id", "research_section_id", "status", "codex_thread_id"):
        op.drop_index(f"ix_research_jobs_{column}", table_name="research_jobs")
    op.drop_table("research_jobs")
    op.drop_index("ix_research_sections_status", table_name="research_sections")
    op.drop_index("ix_research_sections_research_run_id", table_name="research_sections")
    op.drop_table("research_sections")
    op.drop_index("ix_research_runs_status", table_name="research_runs")
    op.drop_index("ix_research_runs_gameweek_id", table_name="research_runs")
    op.drop_index("ix_research_runs_season_id", table_name="research_runs")
    op.drop_table("research_runs")
