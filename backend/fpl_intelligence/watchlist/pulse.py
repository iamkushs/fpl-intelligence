"""Official-data-only weekly Watchlist pulse execution and history."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from fpl_intelligence.integrations.fpl.adapter import OfficialFPLAdapter
from fpl_intelligence.models import PlayerGameweekPulse, WatchlistEntry


STAT_FIELDS = (
    "minutes", "starts", "total_points", "goals_scored", "assists", "clean_sheets",
    "goals_conceded", "own_goals", "penalties_saved", "penalties_missed", "yellow_cards",
    "red_cards", "saves", "bonus", "bps", "expected_goals", "expected_assists",
    "expected_goal_involvements", "expected_goals_conceded",
)


@dataclass(frozen=True)
class PulseRunSummary:
    gameweek: int
    active_watchlist_players_considered: int
    pulses_created: int
    pulses_updated: int
    players_with_no_usable_gameweek_data: list[int]
    failures: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def attacking_blank_streak(pulses: list[PlayerGameweekPulse]) -> int:
    """Consecutive newest Gameweeks with zero goals AND zero assists."""
    streak = 0
    for pulse in pulses:
        if (pulse.goals_scored or 0) > 0 or (pulse.assists or 0) > 0:
            break
        streak += 1
    return streak


class PlayerPulseService:
    def __init__(self, adapter: OfficialFPLAdapter):
        self.adapter = adapter

    def run_watchlist_pulse(self, session: Session, gameweek: int) -> PulseRunSummary:
        if gameweek < 1:
            raise ValueError("gameweek must be at least 1")
        player_ids = list(session.scalars(
            select(WatchlistEntry.player_id).where(WatchlistEntry.active.is_(True))
        ))
        official = {item.player_id: item for item in self.adapter.get_gameweek_live(gameweek)}
        existing = {pulse.player_id: pulse for pulse in session.scalars(
            select(PlayerGameweekPulse).where(
                PlayerGameweekPulse.gameweek == gameweek,
                PlayerGameweekPulse.player_id.in_(player_ids),
            )
        )} if player_ids else {}
        created = updated = 0
        missing: list[int] = []
        failures: list[dict[str, object]] = []
        now = datetime.now(timezone.utc)
        for player_id in player_ids:
            stats = official.get(player_id)
            if stats is None:
                missing.append(player_id)
                continue
            try:
                pulse = existing.get(player_id)
                if pulse is None:
                    pulse = PlayerGameweekPulse(player_id=player_id, gameweek=gameweek, captured_at=now)
                    session.add(pulse)
                    created += 1
                else:
                    updated += 1
                for field in STAT_FIELDS:
                    setattr(pulse, field, getattr(stats, field))
                pulse.updated_at = now
            except Exception as exc:  # isolate malformed/unusable player data
                failures.append({"player_id": player_id, "error": str(exc)})
        session.commit()
        return PulseRunSummary(gameweek, len(player_ids), created, updated, missing, failures)

    @staticmethod
    def recent_history(session: Session, player_id: int, limit: int = 5) -> list[PlayerGameweekPulse]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        return list(session.scalars(
            select(PlayerGameweekPulse)
            .where(PlayerGameweekPulse.player_id == player_id)
            .order_by(PlayerGameweekPulse.gameweek.desc())
            .limit(limit)
        ))

    @staticmethod
    def recent_histories(session: Session, player_ids: list[int], limit: int = 5) -> dict[int, list[PlayerGameweekPulse]]:
        """Bulk history lookup used by Watchlist responses; never queries per player."""
        result = {player_id: [] for player_id in player_ids}
        if not player_ids:
            return result
        pulses = session.scalars(
            select(PlayerGameweekPulse)
            .where(PlayerGameweekPulse.player_id.in_(player_ids))
            .order_by(PlayerGameweekPulse.player_id, PlayerGameweekPulse.gameweek.desc())
        )
        for pulse in pulses:
            if len(result[pulse.player_id]) < limit:
                result[pulse.player_id].append(pulse)
        return result

    @staticmethod
    def aggregates(pulses: list[PlayerGameweekPulse]) -> dict[str, int | float]:
        count = len(pulses)
        return {
            "gameweeks": count,
            "appearances": sum(1 for p in pulses if (p.minutes or 0) > 0),
            "attacking_blank_streak": attacking_blank_streak(pulses),
            "total_goals": sum(p.goals_scored or 0 for p in pulses),
            "total_assists": sum(p.assists or 0 for p in pulses),
            "total_points": sum(p.total_points or 0 for p in pulses),
            "average_minutes": (sum(p.minutes or 0 for p in pulses) / count if count else 0.0),
            "total_bonus": sum(p.bonus or 0 for p in pulses),
            "total_expected_goals": sum(p.expected_goals or 0 for p in pulses),
            "total_expected_assists": sum(p.expected_assists or 0 for p in pulses),
            "total_expected_goal_involvements": sum(p.expected_goal_involvements or 0 for p in pulses),
        }
