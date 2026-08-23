"""Cheap official-data refresh and no-I/O Gameweek Briefing read model."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.integrations.fpl.bootstrap import FPLBootstrapSyncService
from fpl_intelligence.match_center.service import MatchCenterService
from fpl_intelligence.models import (
    FPLGameweek, Player, PlayerGameweekPulse, PlayerResearchTrigger,
    ResearchPlayerSynthesis, ResearchQueueItem, WatchlistEntry,
)
from fpl_intelligence.watchlist.pulse import PlayerPulseService
from fpl_intelligence.watchlist.triggers import TriggerService


@dataclass(frozen=True)
class GameweekRefreshResult:
    gameweek: int
    bootstrap_refreshed: bool
    players_considered: int
    pulses_created: int
    pulses_updated: int
    signals_created: int
    signals_resolved: int
    failures: list[dict[str, object]]

    def to_dict(self): return asdict(self)


def relevant_gameweek(session: Session) -> FPLGameweek | None:
    """Prefer current, then next, then the newest durable official event."""
    rows = list(session.scalars(select(FPLGameweek).order_by(FPLGameweek.number.desc())))
    return next((x for x in rows if x.is_current), None) or next((x for x in rows if x.is_next), None) or (rows[0] if rows else None)


def gameweek_phase(event: FPLGameweek | None, now: datetime | None = None) -> str:
    if event is None: return "between_gameweeks"
    now = now or datetime.now(timezone.utc)
    if event.finished: return "between_gameweeks"
    deadline = event.deadline
    if deadline and deadline.tzinfo is None: deadline = deadline.replace(tzinfo=timezone.utc)
    if deadline and now < deadline: return "pre_deadline"
    return "live"


class GameweekBriefingService:
    def __init__(self, adapter): self.adapter = adapter

    def refresh(self, session: Session, gameweek: int | None = None) -> GameweekRefreshResult:
        # Bootstrap is the only external catalogue call. Pulse and trigger work is deterministic.
        FPLBootstrapSyncService(self.adapter).sync(session)
        event = session.get(FPLGameweek, gameweek) if gameweek else relevant_gameweek(session)
        if event is None: raise ValueError("No official gameweek is available after bootstrap refresh")
        pulse = PlayerPulseService(self.adapter).run_watchlist_pulse(session, event.number)
        triggers = TriggerService().evaluate_watchlist_triggers(session, event.number)
        failures = [*pulse.failures, *triggers.failures]
        return GameweekRefreshResult(event.number, True, pulse.active_watchlist_players_considered,
            pulse.pulses_created, pulse.pulses_updated, triggers.new_research_triggers,
            triggers.resolved_triggers, failures)

    def briefing(self, session: Session, now: datetime | None = None) -> dict:
        event = relevant_gameweek(session); phase = gameweek_phase(event, now)
        queue = list(session.scalars(select(ResearchQueueItem).options(selectinload(ResearchQueueItem.player)).order_by(ResearchQueueItem.queue_order)))
        active_queue_players = {x.player_id for x in queue if x.status in {"queued", "running", "snoozed"}}
        triggers = list(session.scalars(select(PlayerResearchTrigger).options(selectinload(PlayerResearchTrigger.player)).where(PlayerResearchTrigger.status == "open").order_by(PlayerResearchTrigger.priority.desc(), PlayerResearchTrigger.created_at.desc()).limit(8)))
        watch_ids = list(session.scalars(select(WatchlistEntry.player_id).where(WatchlistEntry.active.is_(True))))
        pulses = list(session.scalars(select(PlayerGameweekPulse).options(selectinload(PlayerGameweekPulse.player)).where(PlayerGameweekPulse.player_id.in_(watch_ids)).order_by(PlayerGameweekPulse.player_id, PlayerGameweekPulse.gameweek.desc()))) if watch_ids else []
        movements = []
        for player_id in watch_ids:
            history = [x for x in pulses if x.player_id == player_id][:2]
            if len(history) == 2 and history[0].minutes != history[1].minutes:
                movements.append({"player_id": player_id, "player_name": history[0].player.display_name, "kind": "minutes", "from": history[1].minutes, "to": history[0].minutes, "gameweek": history[0].gameweek})
        syntheses = list(session.scalars(select(ResearchPlayerSynthesis).options(selectinload(ResearchPlayerSynthesis.deep_run)).order_by(ResearchPlayerSynthesis.created_at.desc()).limit(5)))
        names = {x.id: x.display_name for x in session.scalars(select(Player).where(Player.id.in_([x.player_id for x in syntheses])))} if syntheses else {}
        match = MatchCenterService(None, None).get_match_center(session, event.number) if event and phase == "live" else None
        return {
            "gameweek": None if event is None else event.number, "phase": phase,
            "deadline": None if event is None else event.deadline, "generated_at": datetime.now(timezone.utc),
            "research_summary": {"queued": sum(x.status == "queued" for x in queue), "running": sum(x.status == "running" for x in queue), "failed": sum(x.status == "failed" for x in queue), "snoozed": sum(x.status == "snoozed" for x in queue), "items": [{"player_id": x.player_id, "player_name": x.player.display_name, "source": x.source, "reason": x.reason, "queue_order": x.queue_order} for x in queue if x.status in {"queued", "running"}][:5]},
            "attention_signals": [{"id": x.id, "player_id": x.player_id, "player_name": x.player.display_name, "reason": x.description, "source": x.source, "type": x.trigger_type, "gameweek": x.gameweek, "already_queued": x.player_id in active_queue_players} for x in triggers],
            "watchlist_movements": movements[:8],
            "player_intelligence_changes": [{"player_id": x.player_id, "player_name": names.get(x.player_id), "state": x.overall_research_state, "summary": x.executive_summary, "cutoff": x.research_cutoff} for x in syntheses],
            "match_center_summary": None if match is None else {"fetched_at": match["fetched_at"], "our_points": match["scoreboard"]["our_pair"]["total"], "opponent_points": match["scoreboard"]["opponent_pair"]["total"], "swing": match["scoreboard"]["pair_live_swing"], "fixtures": {"live": sum(x["started"] and not x["finished"] for x in match["fixtures"]), "finished": sum(x["finished"] for x in match["fixtures"]), "upcoming": sum(not x["started"] for x in match["fixtures"])}},
        }
