"""add Decision Center foundation

Revision ID: 0017_decision_center_foundation
Revises: 0016_align_orchestration_schema_metadata
"""

from alembic import op
import sqlalchemy as sa

revision = "0017_decision_center_foundation"
down_revision = "0016_align_orchestration_schema_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("decision_sessions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("manager_id", sa.Integer(), sa.ForeignKey("fpl_managers.id", ondelete="RESTRICT"), nullable=False), sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("fpl_manager_gameweek_snapshots.id", ondelete="RESTRICT"), nullable=False), sa.Column("gameweek", sa.Integer(), nullable=False), sa.Column("frozen_bank", sa.Integer()), sa.Column("status", sa.String(16), nullable=False), sa.Column("selected_option_id", sa.String(36)), sa.Column("finalized_option_id", sa.String(36)), sa.Column("finalized_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("manager_id", "snapshot_id", name="uq_decision_session_manager_snapshot"), sa.CheckConstraint("status IN ('draft', 'finalized')", name="ck_decision_sessions_status"))
    op.create_index("ix_decision_sessions_manager_id", "decision_sessions", ["manager_id"]); op.create_index("ix_decision_sessions_snapshot_id", "decision_sessions", ["snapshot_id"]); op.create_index("ix_decision_sessions_gameweek", "decision_sessions", ["gameweek"])
    op.create_table("decision_session_picks", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("session_id", sa.String(36), sa.ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False), sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="RESTRICT"), nullable=False), sa.Column("squad_position", sa.Integer(), nullable=False), sa.Column("selling_price", sa.Integer()), sa.UniqueConstraint("session_id", "player_id", name="uq_decision_session_pick_player")); op.create_index("ix_decision_session_picks_session_id", "decision_session_picks", ["session_id"])
    op.create_table("decision_options", sa.Column("id", sa.String(36), primary_key=True), sa.Column("session_id", sa.String(36), sa.ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False), sa.Column("option_type", sa.String(16), nullable=False), sa.Column("is_legal", sa.Boolean(), nullable=False), sa.Column("validation_errors", sa.JSON(), nullable=False), sa.Column("budget_available", sa.Integer()), sa.Column("budget_required", sa.Integer()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.CheckConstraint("option_type IN ('hold', 'transfer')", name="ck_decision_options_type")); op.create_index("ix_decision_options_session_id", "decision_options", ["session_id"])
    op.create_table("decision_movements", sa.Column("id", sa.String(36), primary_key=True), sa.Column("option_id", sa.String(36), sa.ForeignKey("decision_options.id", ondelete="CASCADE"), nullable=False), sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("outgoing_player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="RESTRICT"), nullable=False), sa.Column("incoming_player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="RESTRICT"), nullable=False), sa.Column("outgoing_synthesis_id", sa.String(36), sa.ForeignKey("research_player_syntheses.id", ondelete="RESTRICT")), sa.Column("incoming_synthesis_id", sa.String(36), sa.ForeignKey("research_player_syntheses.id", ondelete="RESTRICT")), sa.UniqueConstraint("option_id", "sequence", name="uq_decision_movement_sequence")); op.create_index("ix_decision_movements_option_id", "decision_movements", ["option_id"])


def downgrade() -> None:
    op.drop_table("decision_movements"); op.drop_table("decision_options"); op.drop_table("decision_session_picks"); op.drop_table("decision_sessions")
