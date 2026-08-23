"""complete research queue indexes"""
from alembic import op
revision="0022_research_queue_indexes"; down_revision="0021_research_queue"; branch_labels=None; depends_on=None
def upgrade():
    op.create_index("ix_research_queue_items_queue_order","research_queue_items",["queue_order"]); op.create_index("ix_research_queue_player_status","research_queue_items",["player_id","status"])
def downgrade():
    op.drop_index("ix_research_queue_player_status",table_name="research_queue_items"); op.drop_index("ix_research_queue_items_queue_order",table_name="research_queue_items")
