"""Deterministic research-trigger episodes, monitoring, and queue assembly."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fpl_intelligence.models import (
    MonitoringTrigger,
    Player,
    PlayerGameweekPulse,
    PlayerResearchTrigger,
    ResearchResult,
    ResearchTriggerSource,
    ResearchTriggerStatus,
    WatchlistEntry,
    research_result_players,
)


class TriggerType:
    ATTACKING_BLANKS = "attacking_blanks"
    EXTENDED_ATTACKING_BLANKS = "extended_attacking_blanks"
    NO_INVOLVEMENT = "no_involvement"
    MINUTES_DETERIORATION = "minutes_deterioration"
    POSITIVE_ATTACKING_RUN = "positive_attacking_run"
    EXCEPTIONAL_GAMEWEEK = "exceptional_gameweek"
    MANUAL_REQUEST = "manual_request"
    MONITORING_CONDITION = "monitoring_condition"


@dataclass(frozen=True)
class TriggerThresholds:
    attacking_blanks: int = 4
    extended_attacking_blanks: int = 6
    no_involvement_gameweeks: int = 2
    meaningful_prior_minutes: int = 60
    recent_minutes_window: int = 2
    recent_minutes_below: float = 45
    preceding_appearances: int = 3
    preceding_minutes_at_least: float = 70
    positive_returns_required: int = 3
    positive_returns_window: int = 4
    exceptional_points: int = 10


@dataclass(frozen=True)
class TriggerEvaluationSummary:
    gameweek: int
    players_evaluated: int
    new_research_triggers: int
    resolved_triggers: int
    satisfied_monitoring_triggers: int
    duplicates_skipped: int
    insufficient_history_players: int
    failures: list[dict[str, object]]

    def to_dict(self):
        return asdict(self)


@dataclass
class ResearchQueueItem:
    player_id: int
    triggers: list[PlayerResearchTrigger]
    primary_trigger: PlayerResearchTrigger
    from_previous_monitoring: bool
    most_recent_research_at: datetime | None


class TriggerService:
    def __init__(self, thresholds: TriggerThresholds | None = None):
        self.thresholds = thresholds or TriggerThresholds()

    @staticmethod
    def _active_by_key(session: Session, player_ids: list[int]) -> dict[tuple[int, str, str], PlayerResearchTrigger]:
        if not player_ids:
            return {}
        triggers = session.scalars(select(PlayerResearchTrigger).where(
            PlayerResearchTrigger.player_id.in_(player_ids),
            PlayerResearchTrigger.status.in_(ResearchTriggerStatus.ACTIVE),
        ))
        return {(t.player_id, t.trigger_type, t.episode_key): t for t in triggers}

    @staticmethod
    def _histories(session: Session, player_ids: list[int], gameweek: int) -> dict[int, list[PlayerGameweekPulse]]:
        result = {player_id: [] for player_id in player_ids}
        if not player_ids:
            return result
        for pulse in session.scalars(select(PlayerGameweekPulse).where(
            PlayerGameweekPulse.player_id.in_(player_ids),
            PlayerGameweekPulse.gameweek <= gameweek,
        ).order_by(PlayerGameweekPulse.player_id, PlayerGameweekPulse.gameweek.desc())):
            result[pulse.player_id].append(pulse)
        return result

    @staticmethod
    def _resolve(trigger: PlayerResearchTrigger, now: datetime) -> None:
        trigger.status = ResearchTriggerStatus.RESOLVED
        trigger.resolved_at = now
        trigger.updated_at = now

    @staticmethod
    def _ensure(
        session: Session,
        active: dict[tuple[int, str, str], PlayerResearchTrigger],
        *, player_id: int, trigger_type: str, source: str, description: str,
        gameweek: int | None, evidence: dict | None, priority: int = 50,
        episode_key: str = "current", monitoring_trigger_id: str | None = None,
    ) -> tuple[PlayerResearchTrigger, bool]:
        key = (player_id, trigger_type, episode_key)
        existing = active.get(key)
        if existing is not None:
            existing.description = description
            existing.gameweek = gameweek
            existing.evidence = evidence
            existing.updated_at = datetime.now(timezone.utc)
            return existing, False
        trigger = PlayerResearchTrigger(
            player_id=player_id, trigger_type=trigger_type, episode_key=episode_key,
            source=source, status=ResearchTriggerStatus.OPEN, priority=priority,
            description=description, gameweek=gameweek, evidence=evidence,
            monitoring_trigger_id=monitoring_trigger_id,
        )
        session.add(trigger)
        active[key] = trigger
        return trigger, True

    def evaluate_watchlist_triggers(self, session: Session, gameweek: int) -> TriggerEvaluationSummary:
        if gameweek < 1:
            raise ValueError("gameweek must be at least 1")
        player_ids = list(session.scalars(select(WatchlistEntry.player_id).where(WatchlistEntry.active.is_(True))))
        histories = self._histories(session, player_ids, gameweek)
        active = self._active_by_key(session, player_ids)
        monitors = list(session.scalars(select(MonitoringTrigger).where(
            MonitoringTrigger.player_id.in_(player_ids), MonitoringTrigger.active.is_(True)
        ))) if player_ids else []
        monitors_by_player: dict[int, list[MonitoringTrigger]] = {pid: [] for pid in player_ids}
        for monitor in monitors:
            monitors_by_player[monitor.player_id].append(monitor)
        created = resolved = satisfied = duplicates = insufficient = 0
        failures: list[dict[str, object]] = []
        now = datetime.now(timezone.utc)
        for player_id in player_ids:
            try:
                pulses = histories[player_id]
                if len(pulses) < self.thresholds.extended_attacking_blanks:
                    insufficient += 1
                outcomes = self._pulse_conditions(pulses, gameweek)
                for trigger_type, condition in outcomes.items():
                    qualifies, description, evidence, priority, episode_key, resolve_when_false = condition
                    key = (player_id, trigger_type, episode_key)
                    if qualifies:
                        _, was_created = self._ensure(
                            session, active, player_id=player_id, trigger_type=trigger_type,
                            source=ResearchTriggerSource.PULSE, description=description,
                            gameweek=gameweek, evidence=evidence, priority=priority, episode_key=episode_key,
                        )
                        created += int(was_created)
                        duplicates += int(not was_created)
                    elif resolve_when_false and (existing := active.get(key)) is not None:
                        self._resolve(existing, now)
                        active.pop(key)
                        resolved += 1
                for monitor in monitors_by_player[player_id]:
                    if self._monitor_satisfied(monitor, pulses):
                        monitor.active = False
                        monitor.satisfied_at = now
                        satisfied += 1
                        _, was_created = self._ensure(
                            session, active, player_id=player_id,
                            trigger_type=TriggerType.MONITORING_CONDITION,
                            episode_key=f"monitoring:{monitor.id}", source=ResearchTriggerSource.RESEARCH,
                            description=f"Monitoring condition satisfied: {monitor.description}",
                            gameweek=gameweek, evidence={"monitoring_trigger_id": monitor.id, "condition": monitor.condition},
                            priority=70, monitoring_trigger_id=monitor.id,
                        )
                        created += int(was_created)
                        duplicates += int(not was_created)
            except Exception as exc:
                failures.append({"player_id": player_id, "error": str(exc)})
        session.commit()
        return TriggerEvaluationSummary(gameweek, len(player_ids), created, resolved, satisfied,
                                        duplicates, insufficient, failures)

    def _pulse_conditions(self, pulses: list[PlayerGameweekPulse], gameweek: int):
        t = self.thresholds
        attack = lambda p: (p.goals_scored or 0) > 0 or (p.assists or 0) > 0
        blank_streak = 0
        for pulse in pulses:
            if attack(pulse):
                break
            blank_streak += 1
        zero_streak = 0
        for pulse in pulses:
            if (pulse.minutes or 0) > 0:
                break
            zero_streak += 1
        prior_meaningful = any((p.minutes or 0) >= t.meaningful_prior_minutes for p in pulses[zero_streak:])
        recent = pulses[:t.recent_minutes_window]
        prior_apps = [p for p in pulses[t.recent_minutes_window:] if (p.minutes or 0) > 0][:t.preceding_appearances]
        deterioration = (
            len(recent) == t.recent_minutes_window and len(prior_apps) == t.preceding_appearances
            and sum(p.minutes or 0 for p in recent) / len(recent) < t.recent_minutes_below
            and sum(p.minutes or 0 for p in prior_apps) / len(prior_apps) >= t.preceding_minutes_at_least
        )
        sufficient_minutes_history = (
            len(recent) == t.recent_minutes_window and len(prior_apps) == t.preceding_appearances
        )
        recent_minutes_average = (
            sum(p.minutes or 0 for p in recent) / len(recent) if len(recent) == t.recent_minutes_window else None
        )
        return_window = pulses[:t.positive_returns_window]
        positive_count = sum(1 for p in return_window if attack(p))
        latest = pulses[0] if pulses and pulses[0].gameweek == gameweek else None
        latest_attack = latest is not None and attack(latest)
        latest_positive_minutes = latest is not None and (latest.minutes or 0) > 0
        return {
            TriggerType.ATTACKING_BLANKS: (
                blank_streak >= t.attacking_blanks, f"{blank_streak} attacking blanks",
                {"streak": blank_streak, "threshold": t.attacking_blanks}, 50, "current", latest_attack,
            ),
            TriggerType.EXTENDED_ATTACKING_BLANKS: (
                blank_streak >= t.extended_attacking_blanks, f"Extended: {blank_streak} attacking blanks",
                {"streak": blank_streak, "threshold": t.extended_attacking_blanks}, 65, "current", latest_attack,
            ),
            TriggerType.NO_INVOLVEMENT: (
                zero_streak >= t.no_involvement_gameweeks and prior_meaningful,
                f"No minutes for {zero_streak} GWs",
                {"zero_minute_streak": zero_streak, "prior_meaningful_involvement": prior_meaningful}, 80, "current", latest_positive_minutes,
            ),
            TriggerType.MINUTES_DETERIORATION: (
                deterioration, "Minutes declined materially",
                {"recent_minutes": [p.minutes for p in recent], "preceding_minutes": [p.minutes for p in prior_apps]}, 75, "current",
                sufficient_minutes_history and recent_minutes_average is not None and recent_minutes_average >= t.recent_minutes_below,
            ),
            TriggerType.POSITIVE_ATTACKING_RUN: (
                len(return_window) == t.positive_returns_window and positive_count >= t.positive_returns_required,
                f"Attacking return in {positive_count} of last {t.positive_returns_window} GWs",
                {"returns": positive_count, "window": t.positive_returns_window}, 45, "current", False,
            ),
            TriggerType.EXCEPTIONAL_GAMEWEEK: (
                latest is not None and (latest.total_points or 0) >= t.exceptional_points,
                f"Exceptional GW{gameweek}: {latest.total_points if latest else 0} points",
                {"points": latest.total_points if latest else None, "threshold": t.exceptional_points}, 55,
                f"gw:{gameweek}", False,
            ),
        }

    @staticmethod
    def _monitor_satisfied(monitor: MonitoringTrigger, pulses: list[PlayerGameweekPulse]) -> bool:
        condition = monitor.condition or {}
        kind = condition.get("kind")
        if not pulses or kind not in {
            "positive_minutes", "consecutive_appearances", "consecutive_zero_minutes",
            "attacking_return", "minutes_at_least",
        }:
            return False
        if kind == "positive_minutes":
            return (pulses[0].minutes or 0) > 0
        if kind == "attacking_return":
            return (pulses[0].goals_scored or 0) > 0 or (pulses[0].assists or 0) > 0
        if kind == "minutes_at_least":
            return (pulses[0].minutes or 0) >= int(condition.get("minutes", 0))
        count = int(condition.get("count", 1))
        if len(pulses) < count:
            return False
        if kind == "consecutive_appearances":
            return all((p.minutes or 0) > 0 for p in pulses[:count])
        return all((p.minutes or 0) == 0 for p in pulses[:count])

    def manual_trigger(self, session: Session, player_id: int, reason: str | None = None) -> PlayerResearchTrigger:
        if session.get(Player, player_id) is None:
            raise LookupError("Player not found")
        active = self._active_by_key(session, [player_id])
        trigger, _ = self._ensure(
            session, active, player_id=player_id, trigger_type=TriggerType.MANUAL_REQUEST,
            source=ResearchTriggerSource.USER, description=reason or "Manual research request",
            gameweek=None, evidence={"reason": reason} if reason else None, priority=100,
        )
        session.commit()
        session.refresh(trigger)
        return trigger

    @staticmethod
    def dismiss(session: Session, trigger_id: str) -> PlayerResearchTrigger:
        trigger = session.get(PlayerResearchTrigger, trigger_id)
        if trigger is None:
            raise LookupError("Research trigger not found")
        if trigger.status not in ResearchTriggerStatus.ACTIVE:
            raise ValueError("Only open or queued triggers can be dismissed")
        now = datetime.now(timezone.utc)
        trigger.status = ResearchTriggerStatus.DISMISSED
        trigger.dismissed_at = now
        trigger.updated_at = now
        session.commit()
        return trigger

    @staticmethod
    def create_monitoring(session: Session, *, player_id: int, description: str, category: str,
                          condition: dict | None = None, research_result_id: str | None = None,
                          research_thread_id: str | None = None) -> MonitoringTrigger:
        if session.get(Player, player_id) is None:
            raise LookupError("Player not found")
        monitor = MonitoringTrigger(player_id=player_id, description=description, category=category,
                                    condition=condition, research_result_id=research_result_id,
                                    research_thread_id=research_thread_id)
        session.add(monitor)
        session.commit()
        session.refresh(monitor)
        return monitor

    @staticmethod
    def retire_monitoring(session: Session, monitor_id: str) -> MonitoringTrigger:
        monitor = session.get(MonitoringTrigger, monitor_id)
        if monitor is None:
            raise LookupError("Monitoring trigger not found")
        monitor.active = False
        monitor.retired_at = datetime.now(timezone.utc)
        session.commit()
        return monitor

    @staticmethod
    def player_triggers(session: Session, player_id: int):
        triggers = list(session.scalars(select(PlayerResearchTrigger).where(
            PlayerResearchTrigger.player_id == player_id
        ).order_by(PlayerResearchTrigger.created_at.desc())))
        monitors = list(session.scalars(select(MonitoringTrigger).where(
            MonitoringTrigger.player_id == player_id
        ).order_by(MonitoringTrigger.created_at.desc())))
        return triggers, monitors

    @staticmethod
    def queue(session: Session) -> list[ResearchQueueItem]:
        latest_research = (select(
            research_result_players.c.player_id,
            func.max(ResearchResult.researched_at).label("latest"),
        ).join(ResearchResult, ResearchResult.id == research_result_players.c.research_result_id)
         .group_by(research_result_players.c.player_id).subquery())
        rows = session.execute(select(PlayerResearchTrigger, latest_research.c.latest).outerjoin(
            latest_research, latest_research.c.player_id == PlayerResearchTrigger.player_id
        ).where(PlayerResearchTrigger.status.in_(ResearchTriggerStatus.ACTIVE)).order_by(
            PlayerResearchTrigger.priority.desc(), PlayerResearchTrigger.created_at.asc()
        )).all()
        grouped: dict[int, ResearchQueueItem] = {}
        for trigger, latest in rows:
            item = grouped.get(trigger.player_id)
            if item is None:
                grouped[trigger.player_id] = ResearchQueueItem(
                    trigger.player_id, [trigger], trigger, trigger.monitoring_trigger_id is not None, latest
                )
            else:
                item.triggers.append(trigger)
                item.from_previous_monitoring = item.from_previous_monitoring or trigger.monitoring_trigger_id is not None
        return list(grouped.values())
