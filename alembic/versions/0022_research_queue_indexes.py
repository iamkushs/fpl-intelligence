"""complete research queue indexes"""
from alembic import op
revision="0022_research_queue_indexes"; down_revision="0021_research_queue"; branch_labels=None; depends_on=None
def upgrade():
    # These indexes are created by 0021; this revision records the completed schema head.
    pass
def downgrade():
    pass
