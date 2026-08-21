"""add squad pair view state

Revision ID: 0015_squad_pair_view
Revises: 0014_weekly_research_orchestration
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_squad_pair_view"
down_revision = "0014_weekly_research_orchestration"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("fpl_managers", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("entry_id", sa.Integer(), nullable=False), sa.Column("manager_name", sa.String(255)), sa.Column("team_name", sa.String(255)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("last_synced_at", sa.DateTime(timezone=True)), sa.UniqueConstraint("entry_id", name="uq_fpl_managers_entry_id"))
    op.create_index("ix_fpl_managers_entry_id", "fpl_managers", ["entry_id"])
    op.create_table("fpl_manager_pairs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(255), nullable=False), sa.Column("side", sa.String(16), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.CheckConstraint("side IN ('ours', 'opponent')", name="ck_fpl_manager_pairs_side"), sa.UniqueConstraint("side", name="uq_fpl_manager_pairs_side"))
    op.create_index("ix_fpl_manager_pairs_side", "fpl_manager_pairs", ["side"])
    op.create_table("fpl_manager_pair_members", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("pair_id", sa.Integer(), sa.ForeignKey("fpl_manager_pairs.id", ondelete="CASCADE"), nullable=False), sa.Column("manager_id", sa.Integer(), sa.ForeignKey("fpl_managers.id", ondelete="RESTRICT"), nullable=False), sa.Column("slot", sa.Integer(), nullable=False), sa.CheckConstraint("slot IN (1, 2)", name="ck_fpl_manager_pair_members_slot"), sa.UniqueConstraint("pair_id", "manager_id", name="uq_fpl_pair_member_manager"), sa.UniqueConstraint("pair_id", "slot", name="uq_fpl_pair_member_slot"))
    op.create_index("ix_fpl_manager_pair_members_pair_id", "fpl_manager_pair_members", ["pair_id"]); op.create_index("ix_fpl_manager_pair_members_manager_id", "fpl_manager_pair_members", ["manager_id"])
    op.create_table("fpl_manager_gameweek_snapshots", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("manager_id", sa.Integer(), sa.ForeignKey("fpl_managers.id", ondelete="CASCADE"), nullable=False), sa.Column("gameweek", sa.Integer(), nullable=False), sa.Column("event_points", sa.Integer()), sa.Column("total_points", sa.Integer()), sa.Column("overall_rank", sa.Integer()), sa.Column("bank", sa.Integer()), sa.Column("squad_value", sa.Integer()), sa.Column("active_chip", sa.String(64)), sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("manager_id", "gameweek", name="uq_fpl_manager_gameweek_snapshot"))
    op.create_index("ix_fpl_manager_gameweek_snapshots_manager_id", "fpl_manager_gameweek_snapshots", ["manager_id"]); op.create_index("ix_fpl_manager_gameweek_snapshots_gameweek", "fpl_manager_gameweek_snapshots", ["gameweek"]); op.create_index("ix_fpl_manager_snapshots_manager_gameweek", "fpl_manager_gameweek_snapshots", ["manager_id", "gameweek"])
    op.create_table("fpl_manager_gameweek_picks", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("fpl_manager_gameweek_snapshots.id", ondelete="CASCADE"), nullable=False), sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="RESTRICT"), nullable=False), sa.Column("squad_position", sa.Integer(), nullable=False), sa.Column("multiplier", sa.Integer(), nullable=False), sa.Column("is_captain", sa.Boolean(), nullable=False), sa.Column("is_vice_captain", sa.Boolean(), nullable=False), sa.Column("purchase_price", sa.Integer()), sa.Column("selling_price", sa.Integer()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("snapshot_id", "squad_position", name="uq_fpl_snapshot_pick_position"), sa.UniqueConstraint("snapshot_id", "player_id", name="uq_fpl_snapshot_pick_player"))
    op.create_index("ix_fpl_manager_gameweek_picks_snapshot_id", "fpl_manager_gameweek_picks", ["snapshot_id"]); op.create_index("ix_fpl_manager_picks_player", "fpl_manager_gameweek_picks", ["player_id"])


def downgrade():
    op.drop_table("fpl_manager_gameweek_picks"); op.drop_table("fpl_manager_gameweek_snapshots"); op.drop_table("fpl_manager_pair_members"); op.drop_table("fpl_manager_pairs"); op.drop_table("fpl_managers")
