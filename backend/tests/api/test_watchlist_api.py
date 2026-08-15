from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.db.base import Base
from fpl_intelligence.db.session import Database
from fpl_intelligence.integrations.fpl.schemas import FPLPlayer
from fpl_intelligence.models import Player, ResearchThreadType, WatchlistEntry
from fpl_intelligence.research.persistence import ResearchPersistenceService


class SnapshotService:
    def get_snapshot(self):
        players = [
            FPLPlayer(
                id=7, first_name="Bukayo", second_name="Saka", display_name="Saka",
                club_id=1, club_name="Arsenal", club_short_name="ARS", position="MID",
                price=10.0, ownership_percent=30.5, availability_status="a",
            ),
            FPLPlayer(
                id=8, first_name="Cole", second_name="Palmer", display_name="Palmer",
                club_id=2, club_name="Chelsea", club_short_name="CHE", position="MID",
                price=10.5, ownership_percent=42.0, availability_status="a",
            ),
        ]
        return type("Snapshot", (), {"players": players, "current_gameweek": None})()


def test_watchlist_full_manual_lifecycle_and_player_details():
    url = f"sqlite:///./watchlist_test_{uuid4().hex}.db"
    settings = Settings(database_url=url)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    persistence = ResearchPersistenceService()
    with database.session_factory() as session:
        session.add(Player(id=7))
        session.commit()
        thread = persistence.create_thread(session, title="Player research", thread_type=ResearchThreadType.PLAYER)
        link = persistence.add_collected_link(session, thread_id=thread.id, url="https://example.com/saka", player_ids=[7])
        result = persistence.persist_result(
            session, link=link, summary="Role", findings="Advanced role", evidence="Report",
            player_ids=[7], researched_at=datetime.now(timezone.utc),
        )

    app = create_app(settings, database=database, codex_service=CodexService(client=object()), fpl_snapshot_service=SnapshotService())
    try:
        with TestClient(app) as client:
            assert client.post("/watchlist/999").status_code == 404

            options = client.get("/watchlist/players")
            assert options.status_code == 200
            assert options.json() == [{
                "player_id": 7, "player_name": "Saka", "club": "Arsenal",
                "position": "MID", "price": 10.0, "watchlisted": False,
            }]

            assert client.get("/fpl/players").json() == []
            assert client.get("/fpl/players", params={"search": "palmer"}).json() == []
            assert client.get("/fpl/players", params={"search": "ARSENAL"}).json()[0]["player_name"] == "Saka"
            with database.session_factory() as session:
                assert session.query(Player).count() == 1

            added = client.post("/watchlist/7", json={"reason": "Strong underlying numbers", "pinned": False})
            assert added.status_code == 200
            assert added.json()["added_source"] == "user"
            assert added.json()["addition_reason"] == "Strong underlying numbers"
            refreshed_options = client.get("/watchlist/players").json()
            assert len(refreshed_options) == 1
            assert refreshed_options[0]["watchlisted"] is True

            duplicate = client.post("/watchlist/7", json={"reason": "ignored duplicate", "pinned": True})
            assert duplicate.status_code == 200
            assert duplicate.json()["pinned"] is False
            with database.session_factory() as session:
                assert session.query(WatchlistEntry).count() == 1

            listed = client.get("/watchlist")
            assert listed.status_code == 200
            assert len(listed.json()) == 1
            assert listed.json()[0]["player_name"] == "Saka"
            assert listed.json()[0]["ownership_percent"] == 30.5
            assert listed.json()[0]["addition_reason"] == "Strong underlying numbers"
            assert listed.json()[0]["last_research_at"] is not None

            assert client.patch("/watchlist/7/pin", json={"pinned": True}).json()["pinned"] is True
            assert client.patch("/watchlist/7/pin", json={"pinned": False}).json()["pinned"] is False

            details = client.get("/fpl/players/7").json()
            assert details["watchlist"]["active"] is True
            assert details["watchlist"]["addition_reason"] == "Strong underlying numbers"

            removed = client.request("DELETE", "/watchlist/7", json={"reason": "Manual review"})
            assert removed.status_code == 200
            assert removed.json()["removed_at"] is not None
            assert client.get("/watchlist").json() == []
            assert client.get("/fpl/players/7").json()["watchlist"]["active"] is False

            readded = client.post("/watchlist/7", json={"pinned": True})
            assert readded.status_code == 200
            assert readded.json()["active"] is True
            assert readded.json()["removed_at"] is None

        with database.session_factory() as session:
            assert session.get(type(result), result.id) is not None
            assert session.query(WatchlistEntry).count() == 1
    finally:
        database.engine.dispose()
        Path(url.removeprefix("sqlite:///./")).unlink(missing_ok=True)
