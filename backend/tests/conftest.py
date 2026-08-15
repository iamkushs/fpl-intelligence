from datetime import datetime, timezone

import pytest

from fpl_intelligence.codex.client import CodexExecutionResult
from fpl_intelligence.config import Settings
from fpl_intelligence.db.base import Base
from fpl_intelligence.db.session import Database


@pytest.fixture
def execution_result():
    started = datetime.now(timezone.utc)
    return CodexExecutionResult(
        thread_id="thread-test",
        turn_id="turn-test",
        final_text="# Research\n\nFree-form result.",
        model="codex-test",
        reasoning_effort="medium",
        usage={"total_tokens": 12},
        started_at=started,
        completed_at=started,
    )


@pytest.fixture
def database(tmp_path):
    database = Database(Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}"))
    Base.metadata.create_all(database.engine)
    try:
        yield database
    finally:
        database.engine.dispose()
