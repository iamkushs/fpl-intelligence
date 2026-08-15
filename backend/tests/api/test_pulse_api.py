from fastapi.testclient import TestClient

from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.integrations.fpl.schemas import FPLPlayerGameweekStats
from fpl_intelligence.models import Player, PlayerGameweekPulse
from fpl_intelligence.watchlist.service import WatchlistService


class Adapter:
    def get_gameweek_live(self, gameweek):
        return [FPLPlayerGameweekStats(player_id=7, minutes=90, total_points=8, goals_scored=1, assists=0)]


class Snapshot:
    def get_snapshot(self):
        return type("SnapshotData", (), {"players": [], "current_gameweek": None})()


def test_manual_pulse_endpoint_is_idempotent(database):
    with database.session_factory() as session:
        session.add(Player(id=7))
        session.commit()
        WatchlistService().add(session, 7)
    app = create_app(Settings(database_url="sqlite:///unused.db"), database=database,
                     codex_service=CodexService(client=object()), fpl_adapter=Adapter(),
                     fpl_snapshot_service=Snapshot())
    with TestClient(app) as client:
        first = client.post("/watchlist/pulse/7")
        second = client.post("/watchlist/pulse/7")
        assert first.status_code == 200
        assert first.json()["pulses_created"] == 1
        assert second.json()["pulses_updated"] == 1
    with database.session_factory() as session:
        pulse = session.query(PlayerGameweekPulse).one()
        assert (pulse.gameweek, pulse.minutes, pulse.total_points, pulse.goals_scored, pulse.assists) == (7, 90, 8, 1, 0)
