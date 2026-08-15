from sqlalchemy import event
from fastapi.testclient import TestClient

from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.integrations.fpl.schemas import FPLPlayer
from fpl_intelligence.models import Player
from fpl_intelligence.watchlist.service import WatchlistService


class SnapshotService:
    def get_snapshot(self):
        players = [FPLPlayer(
            id=player_id, first_name="Player", second_name=str(player_id), display_name=f"P{player_id}",
            club_id=1, club_name="Club", club_short_name="CLB", position="MID", price=7.0,
            availability_status="a",
        ) for player_id in (1, 2, 3)]
        return type("Snapshot", (), {"players": players, "current_gameweek": None})()


def app_for(database):
    return create_app(Settings(database_url="sqlite:///unused.db"), database=database,
                      codex_service=CodexService(client=object()), fpl_snapshot_service=SnapshotService())


def test_manual_queue_player_lifecycle_and_monitoring_endpoints(database):
    with database.session_factory() as session:
        session.add(Player(id=1)); session.commit(); WatchlistService().add(session, 1)
    with TestClient(app_for(database)) as client:
        manual = client.post("/fpl/players/1/trigger-research", json={"reason": "Review minutes"})
        assert manual.status_code == 200
        assert manual.json()["source"] == "user"
        queue = client.get("/research/queue").json()
        assert len(queue) == 1
        assert queue[0]["player_id"] == 1
        assert queue[0]["primary_trigger"]["description"] == "Review minutes"

        monitor = client.post("/fpl/players/1/monitoring-triggers", json={
            "description": "Next competitive XI", "category": "team_selection",
            "condition": {"kind": "team_selection"},
        })
        assert monitor.status_code == 201
        listed = client.get("/fpl/players/1/triggers").json()
        assert len(listed["research_triggers"]) == 1
        assert len(listed["monitoring_triggers"]) == 1

        retired = client.patch(f"/research/monitoring-triggers/{monitor.json()['id']}/retire")
        assert retired.json()["active"] is False
        dismissed = client.patch(f"/research/triggers/{manual.json()['id']}/dismiss")
        assert dismissed.json()["status"] == "dismissed"
        assert client.get("/research/queue").json() == []


def test_watchlist_trigger_summary_uses_fixed_bulk_queries(database):
    with database.session_factory() as session:
        session.add_all([Player(id=i) for i in (1, 2, 3)]); session.commit()
        for player_id in (1, 2, 3):
            WatchlistService().add(session, player_id)
    with TestClient(app_for(database)) as client:
        client.post("/fpl/players/1/trigger-research")
        selects = []

        def count(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(database.engine, "before_cursor_execute", count)
        try:
            payload = client.get("/watchlist").json()
        finally:
            event.remove(database.engine, "before_cursor_execute", count)
        by_id = {item["player_id"]: item for item in payload}
        assert by_id[1]["research_needed"] is True
        assert by_id[1]["open_trigger_count"] == 1
        assert by_id[2]["research_needed"] is False
        assert len(selects) == 3
