"""add evidence bundles and dimension confidence

Revision ID: 0012_evidence_bundles_dimension_confidence
Revises: 0011_research_quality_runs
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_evidence_bundles_dimension_confidence"
down_revision = "0011_research_quality_runs"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("research_evidence_bundles", sa.Column("id", sa.String(36), primary_key=True), sa.Column("thread_id", sa.String(36), sa.ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False), sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="RESTRICT"), nullable=False), sa.Column("situation_id", sa.String(36), sa.ForeignKey("research_situations.id", ondelete="SET NULL")), sa.Column("dimension", sa.String(64), nullable=False), sa.Column("research_cutoff", sa.DateTime(timezone=True), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.CheckConstraint("status IN ('draft', 'assessed')", name="ck_research_evidence_bundles_status"))
    op.create_index("ix_research_evidence_bundles_player_dimension_cutoff", "research_evidence_bundles", ["player_id", "dimension", "research_cutoff"]); op.create_index("ix_research_evidence_bundles_thread_situation", "research_evidence_bundles", ["thread_id", "situation_id"])
    op.create_table("research_evidence_bundle_members", sa.Column("id", sa.String(36), primary_key=True), sa.Column("bundle_id", sa.String(36), sa.ForeignKey("research_evidence_bundles.id", ondelete="CASCADE"), nullable=False), sa.Column("evidence_id", sa.String(36), sa.ForeignKey("research_evidence.id", ondelete="RESTRICT"), nullable=False), sa.Column("role", sa.String(16), nullable=False), sa.UniqueConstraint("bundle_id", "evidence_id", name="uq_research_evidence_bundle_member"), sa.CheckConstraint("role IN ('current', 'superseded', 'contextual')", name="ck_research_evidence_bundle_members_role"))
    op.create_index("ix_research_evidence_bundle_members_bundle_id", "research_evidence_bundle_members", ["bundle_id"]); op.create_index("ix_research_evidence_bundle_members_evidence_id", "research_evidence_bundle_members", ["evidence_id"])
    op.create_table("research_dimension_assessments", sa.Column("id", sa.String(36), primary_key=True), sa.Column("bundle_id", sa.String(36), sa.ForeignKey("research_evidence_bundles.id", ondelete="CASCADE"), nullable=False, unique=True), sa.Column("thread_id", sa.String(36), sa.ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False), sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="RESTRICT"), nullable=False), sa.Column("situation_id", sa.String(36), sa.ForeignKey("research_situations.id", ondelete="SET NULL")), sa.Column("dimension", sa.String(64), nullable=False), sa.Column("research_cutoff", sa.DateTime(timezone=True), nullable=False), sa.Column("bundle_strength", sa.String(16), nullable=False), sa.Column("confidence", sa.String(16), nullable=False), sa.Column("thesis", sa.Text(), nullable=False), sa.Column("rationale", sa.Text(), nullable=False), sa.Column("contradiction_summary", sa.Text()), sa.Column("missing_information", sa.JSON()), sa.Column("evidence_count", sa.Integer(), nullable=False), sa.Column("distinct_source_count", sa.Integer(), nullable=False), sa.Column("independent_source_count", sa.Integer(), nullable=False), sa.Column("contradiction_count", sa.Integer(), nullable=False), sa.Column("superseded_count", sa.Integer(), nullable=False), sa.Column("prompt_version", sa.String(64), nullable=False), sa.Column("model_metadata", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.CheckConstraint("bundle_strength IN ('strong', 'adequate', 'thin', 'unresolved')", name="ck_research_dimension_assessments_strength"), sa.CheckConstraint("confidence IN ('high', 'medium', 'low', 'unresolved')", name="ck_research_dimension_assessments_confidence"))
    op.create_index("ix_research_dimension_assessments_player_dimension_cutoff", "research_dimension_assessments", ["player_id", "dimension", "research_cutoff"])

def downgrade():
    op.drop_table("research_dimension_assessments"); op.drop_table("research_evidence_bundle_members"); op.drop_table("research_evidence_bundles")
