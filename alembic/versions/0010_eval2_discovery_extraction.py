"""add eval2 discovery and extraction execution state

Revision ID: 0010_eval2_discovery_extraction
Revises: 0009_atomic_evidence_source_clusters
Create Date: 2026-08-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_eval2_discovery_extraction"
down_revision = "0009_atomic_evidence_source_clusters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("research_links", sa.Column("failure_reason", sa.Text(), nullable=True))
    op.add_column("research_links", sa.Column("discovery_metadata", sa.JSON(), nullable=True))

    op.add_column("research_results", sa.Column("prompt_version", sa.String(64), nullable=True))
    op.add_column("research_results", sa.Column("research_cutoff", sa.DateTime(timezone=True), nullable=True))
    op.add_column("research_results", sa.Column("source_metadata", sa.JSON(), nullable=True))
    op.create_index("ix_research_results_prompt_version", "research_results", ["prompt_version"])
    op.create_index(
        "uq_research_results_link_prompt_cutoff",
        "research_results",
        ["research_link_id", "prompt_version", "research_cutoff"],
        unique=True,
    )

    op.create_table(
        "research_page_research_attempts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("research_thread_id", sa.String(36), nullable=False),
        sa.Column("research_link_id", sa.String(36), nullable=False),
        sa.Column("research_result_id", sa.String(36), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("research_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("page_research_model_id", sa.String(128), nullable=True),
        sa.Column("model_metadata", sa.JSON(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('researched', 'failed')", name="ck_research_page_research_attempts_status"),
        sa.ForeignKeyConstraint(["research_thread_id"], ["research_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_link_id"], ["research_links.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_result_id"], ["research_results.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns, unique in (
        ("ix_research_page_research_attempts_research_thread_id", ["research_thread_id"], False),
        ("ix_research_page_research_attempts_research_link_id", ["research_link_id"], False),
        ("ix_research_page_research_attempts_research_result_id", ["research_result_id"], False),
        ("ix_research_page_research_attempts_prompt_version", ["prompt_version"], False),
        ("ix_research_page_research_attempts_research_cutoff", ["research_cutoff"], False),
        ("ix_research_page_research_attempts_status", ["status"], False),
        ("ix_research_page_attempts_thread_status", ["research_thread_id", "status"], False),
        ("uq_research_page_attempt_link_prompt_cutoff", ["research_link_id", "prompt_version", "research_cutoff"], True),
    ):
        op.create_index(name, "research_page_research_attempts", columns, unique=unique)

    op.add_column("research_evidence", sa.Column("extraction_prompt_version", sa.String(64), nullable=True))
    op.add_column("research_evidence", sa.Column("extraction_fingerprint", sa.String(64), nullable=True))
    op.create_index("ix_research_evidence_extraction_prompt_version", "research_evidence", ["extraction_prompt_version"])
    op.create_index("ix_research_evidence_extraction_fingerprint", "research_evidence", ["extraction_fingerprint"])
    op.create_index(
        "uq_research_evidence_result_extraction_fingerprint",
        "research_evidence",
        ["research_result_id", "extraction_prompt_version", "extraction_fingerprint"],
        unique=True,
    )

    op.create_table(
        "research_discovery_executions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("research_thread_id", sa.String(36), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("research_situation_id", sa.String(36), nullable=True),
        sa.Column("trigger_id", sa.String(36), nullable=True),
        sa.Column("gameweek_id", sa.Integer(), nullable=True),
        sa.Column("target_gameweek_id", sa.Integer(), nullable=True),
        sa.Column("research_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discovery_prompt_version", sa.String(64), nullable=False),
        sa.Column("page_research_prompt_version", sa.String(64), nullable=False),
        sa.Column("extraction_prompt_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("known_missing_dimensions", sa.JSON(), nullable=True),
        sa.Column("durable_context", sa.JSON(), nullable=True),
        sa.Column("model_metadata", sa.JSON(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('running', 'complete', 'partial', 'failed')", name="ck_research_discovery_executions_status"),
        sa.ForeignKeyConstraint(["research_thread_id"], ["research_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["research_situation_id"], ["research_situations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["trigger_id"], ["player_research_triggers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_research_discovery_executions_research_thread_id", ["research_thread_id"]),
        ("ix_research_discovery_executions_player_id", ["player_id"]),
        ("ix_research_discovery_executions_research_situation_id", ["research_situation_id"]),
        ("ix_research_discovery_executions_trigger_id", ["trigger_id"]),
        ("ix_research_discovery_executions_gameweek_id", ["gameweek_id"]),
        ("ix_research_discovery_executions_target_gameweek_id", ["target_gameweek_id"]),
        ("ix_research_discovery_executions_research_cutoff", ["research_cutoff"]),
        ("ix_research_discovery_executions_status", ["status"]),
        ("ix_research_discovery_executions_thread_status", ["research_thread_id", "status"]),
        ("ix_research_discovery_executions_player_status", ["player_id", "status"]),
    ):
        op.create_index(name, "research_discovery_executions", columns)

    op.create_table(
        "research_source_candidates",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("discovery_execution_id", sa.String(36), nullable=False),
        sa.Column("research_thread_id", sa.String(36), nullable=False),
        sa.Column("research_link_id", sa.String(36), nullable=True),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column("publisher", sa.String(255), nullable=True),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("target_dimensions", sa.JSON(), nullable=False),
        sa.Column("usefulness", sa.Text(), nullable=False),
        sa.Column("source_category", sa.String(64), nullable=False),
        sa.Column("expected_relevance", sa.String(16), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recency", sa.String(128), nullable=True),
        sa.Column("lineage_type", sa.String(32), nullable=False),
        sa.Column("lineage_notes", sa.Text(), nullable=True),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("discovery_phase", sa.String(32), nullable=False),
        sa.Column("discovery_prompt_version", sa.String(64), nullable=False),
        sa.Column("research_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("discovery_phase IN ('broad', 'targeted')", name="ck_research_source_candidates_phase"),
        sa.CheckConstraint("expected_relevance IN ('high', 'medium', 'low')", name="ck_research_source_candidates_relevance"),
        sa.CheckConstraint("lineage_type IN ('original', 'independent', 'derivative', 'unclear')", name="ck_research_source_candidates_lineage_type"),
        sa.CheckConstraint("status IN ('collected', 'duplicate', 'rejected', 'failed')", name="ck_research_source_candidates_status"),
        sa.ForeignKeyConstraint(["discovery_execution_id"], ["research_discovery_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_thread_id"], ["research_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_link_id"], ["research_links.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("discovery_execution_id", "canonical_url", name="uq_research_source_candidates_execution_url"),
    )
    for name, columns in (
        ("ix_research_source_candidates_discovery_execution_id", ["discovery_execution_id"]),
        ("ix_research_source_candidates_research_thread_id", ["research_thread_id"]),
        ("ix_research_source_candidates_research_link_id", ["research_link_id"]),
        ("ix_research_source_candidates_status", ["status"]),
        ("ix_research_source_candidates_thread_phase", ["research_thread_id", "discovery_phase"]),
        ("ix_research_source_candidates_link", ["research_link_id"]),
    ):
        op.create_index(name, "research_source_candidates", columns)


def downgrade() -> None:
    for name in (
        "ix_research_source_candidates_link",
        "ix_research_source_candidates_thread_phase",
        "ix_research_source_candidates_status",
        "ix_research_source_candidates_research_link_id",
        "ix_research_source_candidates_research_thread_id",
        "ix_research_source_candidates_discovery_execution_id",
    ):
        op.drop_index(name, table_name="research_source_candidates")
    op.drop_table("research_source_candidates")

    for name in (
        "ix_research_discovery_executions_player_status",
        "ix_research_discovery_executions_thread_status",
        "ix_research_discovery_executions_status",
        "ix_research_discovery_executions_research_cutoff",
        "ix_research_discovery_executions_target_gameweek_id",
        "ix_research_discovery_executions_gameweek_id",
        "ix_research_discovery_executions_trigger_id",
        "ix_research_discovery_executions_research_situation_id",
        "ix_research_discovery_executions_player_id",
        "ix_research_discovery_executions_research_thread_id",
    ):
        op.drop_index(name, table_name="research_discovery_executions")
    op.drop_table("research_discovery_executions")

    op.drop_index("uq_research_evidence_result_extraction_fingerprint", table_name="research_evidence")
    op.drop_index("ix_research_evidence_extraction_fingerprint", table_name="research_evidence")
    op.drop_index("ix_research_evidence_extraction_prompt_version", table_name="research_evidence")
    op.drop_column("research_evidence", "extraction_fingerprint")
    op.drop_column("research_evidence", "extraction_prompt_version")

    for name in (
        "uq_research_page_attempt_link_prompt_cutoff",
        "ix_research_page_attempts_thread_status",
        "ix_research_page_research_attempts_status",
        "ix_research_page_research_attempts_research_cutoff",
        "ix_research_page_research_attempts_prompt_version",
        "ix_research_page_research_attempts_research_result_id",
        "ix_research_page_research_attempts_research_link_id",
        "ix_research_page_research_attempts_research_thread_id",
    ):
        op.drop_index(name, table_name="research_page_research_attempts")
    op.drop_table("research_page_research_attempts")

    op.drop_index("uq_research_results_link_prompt_cutoff", table_name="research_results")
    op.drop_index("ix_research_results_prompt_version", table_name="research_results")
    op.drop_column("research_results", "source_metadata")
    op.drop_column("research_results", "research_cutoff")
    op.drop_column("research_results", "prompt_version")

    op.drop_column("research_links", "discovery_metadata")
    op.drop_column("research_links", "failure_reason")
