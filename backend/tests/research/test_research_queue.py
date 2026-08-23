from datetime import datetime, timezone

from fpl_intelligence.models import (
    Player,
    ResearchCycle,
    ResearchCyclePlayer,
    ResearchQueueItem,
    ResearchQueueSource,
)
from fpl_intelligence.research.queue import ResearchQueueService


class FailingOrchestrator:
    def create_cycle(self, session, *, gameweek, research_cutoff, max_deep_runs):
        cycle = ResearchCycle(
            gameweek=gameweek,
            research_cutoff=research_cutoff,
            max_deep_runs=max_deep_runs,
            orchestration_version="test",
        )
        session.add(cycle)
        session.commit()
        return cycle

    def prepare_cycle(self, session, cycle_id):
        return session.get(ResearchCycle, cycle_id)

    def execute_selected_player(self, *_args, **_kwargs):
        raise RuntimeError("provider unavailable")


def test_queue_failure_is_linked_and_durable(database):
    with database.session_factory() as session:
        session.add(Player(id=4, display_name="Gabriel"))
        session.commit()
        item = ResearchQueueService().add_player(
            session, player_id=4, source=ResearchQueueSource.USER, reason="Validate Gabriel"
        )

        ResearchQueueService().run(
            session,
            orchestrator=FailingOrchestrator(),
            deep_service=None,
            gameweek=1,
            research_cutoff=datetime.now(timezone.utc),
            limit=1,
            item_ids=[item.id],
        )

        persisted = session.get(ResearchQueueItem, item.id)
        cycle_player = session.get(ResearchCyclePlayer, persisted.cycle_player_id)
        cycle = session.get(ResearchCycle, persisted.cycle_id)
        assert persisted.status == "failed"
        assert cycle_player.state == "failed"
        assert cycle_player.failure_reason == "provider unavailable"
        assert cycle.status == "partial"
        assert cycle.completed_at is not None
