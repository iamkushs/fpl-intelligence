from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.db.base import Base
from fpl_intelligence.db.session import Database
from fpl_intelligence.integrations.fpl.schemas import FPLPlayer
from fpl_intelligence.models import Player


class SnapshotService:
    def get_snapshot(self):
        player = FPLPlayer(id=7, first_name="Bukayo", second_name="Saka", display_name="Saka", club_id=1,
                           club_name="Arsenal", club_short_name="ARS", position="MID", price=10.0,
                           ownership_percent=30.5, availability_status="a")
        return type("Snapshot", (), {"players": [player], "current_gameweek": None})()


def test_intelligence_returns_clean_empty_dossier_for_player_without_research():
    url = f"sqlite:///./player_intelligence_{uuid4().hex}.db"
    database = Database(Settings(database_url=url)); Base.metadata.create_all(database.engine)
    with database.session_factory() as session:
        session.add(Player(id=7)); session.commit()
    app = create_app(Settings(database_url=url), database=database, codex_service=CodexService(client=object()), fpl_snapshot_service=SnapshotService())
    try:
        with TestClient(app) as client:
            response = client.get("/fpl/players/7/intelligence")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["player"]["display_name"] == "Saka"
            assert payload["latest_synthesis"] is None
            assert payload["dimension_assessments"] == []
            assert payload["sources"] == {"researched": [], "collected": [], "failed": []}
    finally:
        database.engine.dispose(); Path(url.removeprefix("sqlite:///")) .unlink(missing_ok=True)
