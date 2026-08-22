"""align orchestration schema metadata

Revision ID: 0016_align_orchestration_schema_metadata
Revises: 0015_squad_pair_view
"""

from alembic import op


revision = "0016_align_orchestration_schema_metadata"
down_revision = "0015_squad_pair_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_research_cycles_status", "research_cycles", ["status"])


def downgrade() -> None:
    op.drop_index("ix_research_cycles_status", table_name="research_cycles")
