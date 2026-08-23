"""complete decision analysis session index"""
from alembic import op
revision="0024_decision_analysis_session_index"; down_revision="0023_decision_analysis"; branch_labels=None; depends_on=None
def upgrade(): op.create_index("ix_decision_analysis_runs_session_id", "decision_analysis_runs", ["session_id"])
def downgrade(): op.drop_index("ix_decision_analysis_runs_session_id", table_name="decision_analysis_runs")
