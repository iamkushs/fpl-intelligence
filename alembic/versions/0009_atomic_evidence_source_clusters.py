"""add atomic evidence and narrative source clusters

Revision ID: 0009_atomic_evidence_source_clusters
Revises: 0008_research_situations
Create Date: 2026-08-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_atomic_evidence_source_clusters"
down_revision = "0008_research_situations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_source_clusters",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("research_thread_id", sa.String(36), nullable=False),
        sa.Column("research_situation_id", sa.String(36), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("likely_original_research_link_id", sa.String(36), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["research_thread_id"], ["research_threads.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["research_situation_id"], ["research_situations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["likely_original_research_link_id"], ["research_links.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_source_clusters_research_thread_id", "research_source_clusters", ["research_thread_id"])
    op.create_index("ix_research_source_clusters_research_situation_id", "research_source_clusters", ["research_situation_id"])
    op.create_index("ix_research_source_clusters_likely_original_research_link_id", "research_source_clusters", ["likely_original_research_link_id"])
    op.create_index("ix_research_source_clusters_thread_situation", "research_source_clusters", ["research_thread_id", "research_situation_id"])

    op.create_table(
        "research_evidence",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("research_thread_id", sa.String(36), nullable=False),
        sa.Column("research_situation_id", sa.String(36), nullable=True),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(64), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("research_link_id", sa.String(36), nullable=True),
        sa.Column("research_result_id", sa.String(36), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("season", sa.String(64), nullable=True),
        sa.Column("reliability", sa.String(16), nullable=False),
        sa.Column("relevance", sa.String(16), nullable=False),
        sa.Column("is_volatile", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("source_cluster_id", sa.String(36), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("evidence_type IN ('fact', 'statistic', 'report', 'supporter_observation', 'speculation', 'inference')", name="ck_research_evidence_evidence_type"),
        sa.CheckConstraint("reliability IN ('high', 'medium', 'low')", name="ck_research_evidence_reliability"),
        sa.CheckConstraint("relevance IN ('high', 'medium', 'low')", name="ck_research_evidence_relevance"),
        sa.ForeignKeyConstraint(["research_thread_id"], ["research_threads.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["research_situation_id"], ["research_situations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["research_link_id"], ["research_links.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["research_result_id"], ["research_results.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_cluster_id"], ["research_source_clusters.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_research_evidence_research_thread_id", ["research_thread_id"]),
        ("ix_research_evidence_research_situation_id", ["research_situation_id"]),
        ("ix_research_evidence_claim_type", ["claim_type"]),
        ("ix_research_evidence_research_link_id", ["research_link_id"]),
        ("ix_research_evidence_research_result_id", ["research_result_id"]),
        ("ix_research_evidence_season", ["season"]),
        ("ix_research_evidence_is_volatile", ["is_volatile"]),
        ("ix_research_evidence_source_cluster_id", ["source_cluster_id"]),
        ("ix_research_evidence_thread_situation", ["research_thread_id", "research_situation_id"]),
    ):
        op.create_index(name, "research_evidence", columns)

    op.create_table(
        "research_evidence_players",
        sa.Column("evidence_id", sa.String(36), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["research_evidence.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evidence_id", "player_id"),
    )
    op.create_index("ix_research_evidence_players_player_id", "research_evidence_players", ["player_id"])

    op.create_table(
        "evidence_hypothesis_relations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("evidence_id", sa.String(36), nullable=False),
        sa.Column("hypothesis_id", sa.String(36), nullable=False),
        sa.Column("relationship_type", sa.String(16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("relationship_type IN ('supports', 'contradicts')", name="ck_evidence_hypothesis_relations_type"),
        sa.ForeignKeyConstraint(["evidence_id"], ["research_evidence.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["situation_hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id", "hypothesis_id", "relationship_type", name="uq_evidence_hypothesis_relations_identity"),
    )
    op.create_index("ix_evidence_hypothesis_relations_evidence_id", "evidence_hypothesis_relations", ["evidence_id"])
    op.create_index("ix_evidence_hypothesis_relations_hypothesis_id", "evidence_hypothesis_relations", ["hypothesis_id"])

    op.create_table(
        "evidence_relations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("from_evidence_id", sa.String(36), nullable=False),
        sa.Column("to_evidence_id", sa.String(36), nullable=False),
        sa.Column("relation_type", sa.String(16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("from_evidence_id <> to_evidence_id", name="ck_evidence_relations_not_self"),
        sa.CheckConstraint("relation_type IN ('supports', 'contradicts', 'supersedes')", name="ck_evidence_relations_type"),
        sa.ForeignKeyConstraint(["from_evidence_id"], ["research_evidence.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_evidence_id"], ["research_evidence.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_evidence_id", "to_evidence_id", "relation_type", name="uq_evidence_relations_identity"),
    )
    op.create_index("ix_evidence_relations_from_evidence_id", "evidence_relations", ["from_evidence_id"])
    op.create_index("ix_evidence_relations_to_evidence_id", "evidence_relations", ["to_evidence_id"])

    op.create_table(
        "research_source_cluster_memberships",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_cluster_id", sa.String(36), nullable=False),
        sa.Column("research_link_id", sa.String(36), nullable=False),
        sa.Column("lineage_type", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("lineage_type IN ('original', 'independent', 'derivative', 'unclear')", name="ck_source_cluster_memberships_lineage_type"),
        sa.ForeignKeyConstraint(["source_cluster_id"], ["research_source_clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_link_id"], ["research_links.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_cluster_id", "research_link_id", name="uq_source_cluster_memberships_cluster_link"),
    )
    op.create_index("ix_research_source_cluster_memberships_source_cluster_id", "research_source_cluster_memberships", ["source_cluster_id"])
    op.create_index("ix_source_cluster_memberships_link_id", "research_source_cluster_memberships", ["research_link_id"])


def downgrade() -> None:
    op.drop_index("ix_source_cluster_memberships_link_id", table_name="research_source_cluster_memberships")
    op.drop_index("ix_research_source_cluster_memberships_source_cluster_id", table_name="research_source_cluster_memberships")
    op.drop_table("research_source_cluster_memberships")
    op.drop_index("ix_evidence_relations_to_evidence_id", table_name="evidence_relations")
    op.drop_index("ix_evidence_relations_from_evidence_id", table_name="evidence_relations")
    op.drop_table("evidence_relations")
    op.drop_index("ix_evidence_hypothesis_relations_hypothesis_id", table_name="evidence_hypothesis_relations")
    op.drop_index("ix_evidence_hypothesis_relations_evidence_id", table_name="evidence_hypothesis_relations")
    op.drop_table("evidence_hypothesis_relations")
    op.drop_index("ix_research_evidence_players_player_id", table_name="research_evidence_players")
    op.drop_table("research_evidence_players")
    for name in ("ix_research_evidence_thread_situation", "ix_research_evidence_source_cluster_id", "ix_research_evidence_is_volatile", "ix_research_evidence_season", "ix_research_evidence_research_result_id", "ix_research_evidence_research_link_id", "ix_research_evidence_claim_type", "ix_research_evidence_research_situation_id", "ix_research_evidence_research_thread_id"):
        op.drop_index(name, table_name="research_evidence")
    op.drop_table("research_evidence")
    for name in ("ix_research_source_clusters_thread_situation", "ix_research_source_clusters_likely_original_research_link_id", "ix_research_source_clusters_research_situation_id", "ix_research_source_clusters_research_thread_id"):
        op.drop_index(name, table_name="research_source_clusters")
    op.drop_table("research_source_clusters")
