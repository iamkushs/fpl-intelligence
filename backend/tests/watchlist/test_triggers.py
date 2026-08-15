from sqlalchemy import select

from fpl_intelligence.models import (
    MonitoringTrigger, Player, PlayerGameweekPulse, PlayerResearchTrigger,
    ResearchTriggerStatus,
)
from fpl_intelligence.watchlist.service import WatchlistService
from fpl_intelligence.watchlist.triggers import TriggerService, TriggerType


def setup_player(session, player_id=1, active=True):
    session.add(Player(id=player_id)); session.commit()
    if active:
        WatchlistService().add(session, player_id)


def add_pulses(session, rows, player_id=1):
    session.add_all([PlayerGameweekPulse(
        player_id=player_id, gameweek=gw, minutes=minutes, goals_scored=goals,
        assists=assists, total_points=points,
    ) for gw, minutes, goals, assists, points in rows])
    session.commit()


def types(session, status=None):
    query = select(PlayerResearchTrigger)
    if status:
        query = query.where(PlayerResearchTrigger.status == status)
    return [item.trigger_type for item in session.scalars(query)]


def test_four_blanks_trigger_fewer_do_not_and_rerun_deduplicates(database):
    with database.session_factory() as session:
        setup_player(session)
        add_pulses(session, [(1, 90, 0, 0, 2), (2, 90, 0, 0, 2), (3, 90, 0, 0, 2)])
        service = TriggerService()
        assert service.evaluate_watchlist_triggers(session, 3).new_research_triggers == 0
        add_pulses(session, [(4, 90, 0, 0, 2)])
        first = service.evaluate_watchlist_triggers(session, 4)
        second = service.evaluate_watchlist_triggers(session, 4)
        assert first.new_research_triggers == 1
        assert second.new_research_triggers == 0 and second.duplicates_skipped == 1
        assert types(session).count(TriggerType.ATTACKING_BLANKS) == 1


def test_extended_escalation_is_distinct_and_return_resolves_then_new_episode_recurs(database):
    with database.session_factory() as session:
        setup_player(session)
        add_pulses(session, [(gw, 90, 0, 0, 2) for gw in range(1, 7)])
        service = TriggerService()
        summary = service.evaluate_watchlist_triggers(session, 6)
        assert summary.new_research_triggers == 2
        assert set(types(session, ResearchTriggerStatus.OPEN)) == {
            TriggerType.ATTACKING_BLANKS, TriggerType.EXTENDED_ATTACKING_BLANKS,
        }
        add_pulses(session, [(7, 90, 1, 0, 8)])
        assert service.evaluate_watchlist_triggers(session, 7).resolved_triggers == 2
        add_pulses(session, [(gw, 90, 0, 0, 2) for gw in range(8, 12)])
        assert service.evaluate_watchlist_triggers(session, 11).new_research_triggers == 1
        blank_rows = list(session.scalars(select(PlayerResearchTrigger).where(
            PlayerResearchTrigger.trigger_type == TriggerType.ATTACKING_BLANKS
        )))
        assert len(blank_rows) == 2
        assert {row.status for row in blank_rows} == {ResearchTriggerStatus.RESOLVED, ResearchTriggerStatus.OPEN}


def test_no_involvement_requires_prior_involvement_and_positive_minutes_resolve(database):
    with database.session_factory() as session:
        setup_player(session)
        add_pulses(session, [(1, 0, 0, 0, 0), (2, 0, 0, 0, 0)])
        service = TriggerService()
        assert TriggerType.NO_INVOLVEMENT not in types(session)
        assert service.evaluate_watchlist_triggers(session, 2).new_research_triggers == 0
        add_pulses(session, [(3, 80, 0, 0, 2), (4, 0, 0, 0, 0), (5, 0, 0, 0, 0)])
        service.evaluate_watchlist_triggers(session, 5)
        assert TriggerType.NO_INVOLVEMENT in types(session, ResearchTriggerStatus.OPEN)
        add_pulses(session, [(6, 10, 0, 0, 1)])
        service.evaluate_watchlist_triggers(session, 6)
        row = session.scalar(select(PlayerResearchTrigger).where(
            PlayerResearchTrigger.trigger_type == TriggerType.NO_INVOLVEMENT
        ))
        assert row.status == ResearchTriggerStatus.RESOLVED


def test_minutes_deterioration_requires_full_baseline(database):
    with database.session_factory() as session:
        setup_player(session)
        service = TriggerService()
        add_pulses(session, [(1, 90, 0, 0, 2), (2, 80, 0, 0, 2), (4, 20, 0, 0, 1), (5, 20, 0, 0, 1)])
        service.evaluate_watchlist_triggers(session, 5)
        assert TriggerType.MINUTES_DETERIORATION not in types(session)
        add_pulses(session, [(3, 90, 0, 0, 2)])
        service.evaluate_watchlist_triggers(session, 5)
        assert TriggerType.MINUTES_DETERIORATION in types(session)


def test_positive_run_and_exceptional_gameweek_create_deterministic_events(database):
    with database.session_factory() as session:
        setup_player(session)
        add_pulses(session, [
            (1, 90, 1, 0, 7), (2, 90, 0, 1, 6), (3, 90, 0, 0, 2), (4, 90, 1, 0, 12),
        ])
        service = TriggerService()
        first = service.evaluate_watchlist_triggers(session, 4)
        second = service.evaluate_watchlist_triggers(session, 4)
        assert first.new_research_triggers == 2
        assert second.new_research_triggers == 0
        assert set(types(session)) == {TriggerType.POSITIVE_ATTACKING_RUN, TriggerType.EXCEPTIONAL_GAMEWEEK}


def test_manual_triggers_queue_once_dismiss_recur_and_survive_watchlist_removal(database):
    with database.session_factory() as session:
        setup_player(session)
        service = TriggerService()
        manual = service.manual_trigger(session, 1, "Review role")
        same = service.manual_trigger(session, 1, "Updated question")
        assert same.id == manual.id
        service._ensure(session, service._active_by_key(session, [1]), player_id=1,
                        trigger_type=TriggerType.ATTACKING_BLANKS, source="pulse",
                        description="4 attacking blanks", gameweek=4, evidence={}, priority=50)
        session.commit()
        queue = service.queue(session)
        assert len(queue) == 1 and len(queue[0].triggers) == 2
        assert queue[0].primary_trigger.trigger_type == TriggerType.MANUAL_REQUEST
        service.dismiss(session, manual.id)
        replacement = service.manual_trigger(session, 1, "New episode")
        assert replacement.id != manual.id
        WatchlistService().remove(session, 1)
        assert session.query(PlayerResearchTrigger).count() == 3


def test_monitoring_persists_qualitative_is_not_guessed_and_pulse_condition_satisfies_with_provenance(database):
    with database.session_factory() as session:
        setup_player(session)
        add_pulses(session, [(1, 90, 0, 1, 5)])
        service = TriggerService()
        qualitative = service.create_monitoring(session, player_id=1, description="Manager confirms role",
                                                category="manager_comment", condition={"kind": "manager_comment"})
        evaluable = service.create_monitoring(session, player_id=1, description="Next attacking return",
                                              category="attacking_return", condition={"kind": "attacking_return"})
        summary = service.evaluate_watchlist_triggers(session, 1)
        session.refresh(qualitative); session.refresh(evaluable)
        assert summary.satisfied_monitoring_triggers == 1
        assert qualitative.active is True and qualitative.satisfied_at is None
        assert evaluable.active is False and evaluable.satisfied_at is not None
        trigger = session.scalar(select(PlayerResearchTrigger).where(
            PlayerResearchTrigger.monitoring_trigger_id == evaluable.id
        ))
        assert trigger.source == "research"
        assert trigger.evidence["monitoring_trigger_id"] == evaluable.id
        assert session.get(MonitoringTrigger, evaluable.id) is not None
