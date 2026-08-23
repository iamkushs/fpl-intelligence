from datetime import datetime, timezone

from fpl_intelligence.models import (
    Player,
    ResearchCycle,
    ResearchCyclePlayer,
    ResearchDeepRun,
    ResearchQueueItem,
    ResearchQueueSource,
)
from fpl_intelligence.research.orchestration import WeeklyResearchOrchestrator
from fpl_intelligence.research.queue import ResearchQueueService


class FailingOrchestrator:
    def __init__(self, queue_item_id):
        self.queue_item_id = queue_item_id

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

    def execute_selected_player(self, session, *_args, **_kwargs):
        queue_item = session.get(ResearchQueueItem, self.queue_item_id)
        assert queue_item.cycle_id is not None
        assert queue_item.cycle_player_id is not None
        assert session.get(ResearchCyclePlayer, queue_item.cycle_player_id).selected_for_deep_research
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
            orchestrator=FailingOrchestrator(item.id),
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


class BoundaryDeepService:
    def __init__(self):
        self.run_id = None

    def create_run(self, session, *, thread_id, player_id, research_cutoff, **_kwargs):
        run = ResearchDeepRun(
            thread_id=thread_id,
            player_id=player_id,
            research_cutoff=research_cutoff,
            target_dimensions=["availability"],
            orchestration_version="boundary-test",
        )
        session.add(run)
        session.commit()
        self.run_id = run.id
        return run

    def execute_full_run(self, session, run_id):
        return session.get(ResearchDeepRun, run_id)


class BoundaryOrchestrator(WeeklyResearchOrchestrator):
    def __init__(self, database, **kwargs):
        super().__init__(**kwargs)
        self.database = database

    def execute_selected_player(self, session, cycle_id, player_id, **kwargs):
        # Use a fresh database session immediately before the real selected
        # player guard.  The superclass call retains that guard unchanged.
        with self.database.session_factory() as separate_session:
            cycle_player = separate_session.query(ResearchCyclePlayer).filter_by(
                cycle_id=cycle_id, player_id=player_id
            ).one()
            assert cycle_player.state == "selected"
            assert cycle_player.selected_for_deep_research is True
        return super().execute_selected_player(session, cycle_id, player_id, **kwargs)


def test_queue_crosses_selected_player_guard_and_creates_deep_run(database):
    with database.session_factory() as session:
        session.add(Player(id=4, display_name="Gabriel"))
        session.commit()
        queue = ResearchQueueService()
        item = queue.add_player(session, player_id=4, source=ResearchQueueSource.USER)
        deep_service = BoundaryDeepService()
        orchestrator = BoundaryOrchestrator(database, deep_service=deep_service)

        queue.run(
            session,
            orchestrator=orchestrator,
            deep_service=deep_service,
            gameweek=1,
            research_cutoff=datetime.now(timezone.utc),
            limit=1,
            item_ids=[item.id],
        )

        persisted = session.get(ResearchQueueItem, item.id)
        cycle_player = session.get(ResearchCyclePlayer, persisted.cycle_player_id)
        assert persisted.status == "completed"
        assert cycle_player.state == "researched"
        assert persisted.deep_run_id == deep_service.run_id
        assert session.get(ResearchDeepRun, deep_service.run_id) is not None
