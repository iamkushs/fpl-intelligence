"""add Match Center player club and position context

Revision ID: 0019_match_center_player_context
Revises: 0018_match_center
"""
from alembic import op
import sqlalchemy as sa
revision="0019_match_center_player_context"
down_revision="0018_match_center"
branch_labels=None
depends_on=None
def upgrade():
    op.add_column("fpl_match_center_player_states",sa.Column("club_id",sa.Integer(),nullable=True)); op.add_column("fpl_match_center_player_states",sa.Column("position",sa.String(8),nullable=True)); op.create_index("ix_fpl_match_center_player_states_club_id","fpl_match_center_player_states",["club_id"])
def downgrade():
    op.drop_index("ix_fpl_match_center_player_states_club_id",table_name="fpl_match_center_player_states"); op.drop_column("fpl_match_center_player_states","position"); op.drop_column("fpl_match_center_player_states","club_id")
