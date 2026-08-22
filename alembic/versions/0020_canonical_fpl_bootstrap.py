"""persist canonical FPL bootstrap catalogue

Revision ID: 0020_canonical_fpl_bootstrap
Revises: 0019_match_center_player_context
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_canonical_fpl_bootstrap"
down_revision = "0019_match_center_player_context"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("fpl_clubs", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("short_name", sa.String(32), nullable=False))
    op.create_table("fpl_gameweeks", sa.Column("number", sa.Integer(), primary_key=True, autoincrement=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("deadline", sa.DateTime(timezone=True)), sa.Column("finished", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("is_next", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("is_previous", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("players") as batch:
        batch.add_column(sa.Column("first_name", sa.String(255)))
        batch.add_column(sa.Column("second_name", sa.String(255)))
        batch.add_column(sa.Column("display_name", sa.String(255)))
        batch.add_column(sa.Column("club_id", sa.Integer()))
        batch.add_column(sa.Column("position", sa.String(8)))
        batch.add_column(sa.Column("price", sa.Float()))
        batch.add_column(sa.Column("ownership_percent", sa.Float()))
        batch.add_column(sa.Column("availability_status", sa.String(32)))
        batch.add_column(sa.Column("chance_of_playing_next_round", sa.Integer()))
        batch.add_column(sa.Column("news", sa.Text()))
        batch.create_index("ix_players_club_id", ["club_id"])


def downgrade():
    with op.batch_alter_table("players") as batch:
        batch.drop_index("ix_players_club_id")
        for name in ("news", "chance_of_playing_next_round", "availability_status", "ownership_percent", "price", "position", "club_id", "display_name", "second_name", "first_name"):
            batch.drop_column(name)
    op.drop_table("fpl_gameweeks")
    op.drop_table("fpl_clubs")
