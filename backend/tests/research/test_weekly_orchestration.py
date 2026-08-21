from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fpl_intelligence.config import Settings
from fpl_intelligence.db.base import Base
from fpl_intelligence.db.session import Database
from fpl_intelligence.models import Player, WatchlistEntry, WatchlistAddedSource
from fpl_intelligence.research.orchestration import WeeklyResearchOrchestrator
from fpl_intelligence.watchlist.triggers import TriggerService


def test_cycle_monitors_full_active_watchlist_without_filling_research_quota():
    url=f"sqlite:///./weekly_cycle_{uuid4().hex}.db"; database=Database(Settings(database_url=url)); Base.metadata.create_all(database.engine)
    try:
        with database.session_factory() as session:
            session.add_all([Player(id=1),Player(id=2),Player(id=3),WatchlistEntry(player_id=1,active=True,added_source=WatchlistAddedSource.USER),WatchlistEntry(player_id=2,active=True,added_source=WatchlistAddedSource.USER),WatchlistEntry(player_id=3,active=False,added_source=WatchlistAddedSource.USER)]);session.commit()
            service=WeeklyResearchOrchestrator(trigger_service=TriggerService())
            cycle=service.create_cycle(session,gameweek=5,research_cutoff=datetime.now(timezone.utc),max_deep_runs=15)
            assert service.create_cycle(session,gameweek=5,research_cutoff=cycle.research_cutoff,max_deep_runs=15).id==cycle.id
            cycle=service.prepare_cycle(session,cycle.id)
            assert {item.player_id for item in cycle.players}=={1,2}
            assert all(item.state=="monitored" for item in cycle.players)
            assert service.execute_cycle(session,cycle.id).status=="completed"
    finally:
        database.engine.dispose();Path(url.removeprefix("sqlite:///")) .unlink(missing_ok=True)
