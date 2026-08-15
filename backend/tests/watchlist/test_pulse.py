from fpl_intelligence.integrations.fpl.schemas import FPLPlayerGameweekStats
from fpl_intelligence.models import Player, PlayerGameweekPulse
from fpl_intelligence.watchlist.pulse import PlayerPulseService, attacking_blank_streak
from fpl_intelligence.watchlist.service import WatchlistService
from sqlalchemy import event


class LiveAdapter:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_gameweek_live(self, gameweek):
        self.calls.append(gameweek)
        return self.rows


def stats(player_id, **values):
    return FPLPlayerGameweekStats(player_id=player_id, **values)


def test_pulse_run_active_only_upserts_and_preserves_official_nulls(database):
    with database.session_factory() as session:
        session.add_all([Player(id=1), Player(id=2), Player(id=3)])
        session.commit()
        watchlist = WatchlistService()
        watchlist.add(session, 1)
        watchlist.add(session, 2)
        watchlist.add(session, 3)
        watchlist.remove(session, 3)
        adapter = LiveAdapter([
            stats(1, minutes=82, total_points=8, goals_scored=1, assists=0, bonus=2,
                  bps=31, expected_goals=.7, starts=None),
            stats(3, minutes=90, total_points=12, goals_scored=2),
        ])
        service = PlayerPulseService(adapter)
        first = service.run_watchlist_pulse(session, 7)
        assert first.active_watchlist_players_considered == 2
        assert first.pulses_created == 1
        assert first.players_with_no_usable_gameweek_data == [2]
        pulse = service.recent_history(session, 1)[0]
        assert (pulse.minutes, pulse.total_points, pulse.goals_scored, pulse.assists, pulse.bonus, pulse.bps) == (82, 8, 1, 0, 2, 31)
        assert pulse.starts is None
        assert service.recent_history(session, 3) == []

        adapter.rows[0] = stats(1, minutes=90, total_points=10, goals_scored=1, assists=1)
        second = service.run_watchlist_pulse(session, 7)
        assert second.pulses_created == 0
        assert second.pulses_updated == 1
        assert len(service.recent_history(session, 1)) == 1
        assert service.recent_history(session, 1)[0].assists == 1

        watchlist.remove(session, 1)
        assert service.recent_history(session, 1)[0].gameweek == 7


def test_recent_history_and_attacking_blank_streak_are_deterministic(database):
    with database.session_factory() as session:
        session.add(Player(id=1))
        session.add_all([
            PlayerGameweekPulse(player_id=1, gameweek=gw, minutes=minutes, goals_scored=goals, assists=assists, total_points=points)
            for gw, minutes, goals, assists, points in [
                (4, 90, 1, 0, 9), (5, 0, 0, 0, 0), (6, 75, 0, 0, 2), (7, 90, 0, 0, 2),
            ]
        ])
        session.commit()
        history = PlayerPulseService.recent_history(session, 1, limit=3)
        assert [pulse.gameweek for pulse in history] == [7, 6, 5]
        assert attacking_blank_streak(history) == 3
        assert PlayerPulseService.aggregates(history)["appearances"] == 2
        session.add(PlayerGameweekPulse(player_id=1, gameweek=8, minutes=20, goals_scored=0, assists=1, total_points=4))
        session.commit()
        assert attacking_blank_streak(PlayerPulseService.recent_history(session, 1)) == 0


def test_bulk_watchlist_histories_use_one_query_not_one_per_player(database):
    with database.session_factory() as session:
        session.add_all([Player(id=1), Player(id=2), Player(id=3)])
        session.add_all([PlayerGameweekPulse(player_id=player_id, gameweek=7, total_points=2)
                         for player_id in (1, 2, 3)])
        session.commit()
        selects = []

        def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(database.engine, "before_cursor_execute", count_selects)
        try:
            histories = PlayerPulseService.recent_histories(session, [1, 2, 3])
        finally:
            event.remove(database.engine, "before_cursor_execute", count_selects)
        assert set(histories) == {1, 2, 3}
        assert len(selects) == 1
