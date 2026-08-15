from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.db.base import Base
from fpl_intelligence.db.session import Database
from fpl_intelligence.integrations.fpl.schemas import FPLPlayer
from fpl_intelligence.models import Player, PlayerGameweekPulse, ResearchLinkStatus, ResearchThreadType
from fpl_intelligence.research.persistence import ResearchPersistenceService
from fpl_intelligence.watchlist.triggers import TriggerService


class SnapshotService:
    def __init__(self):
        self.player = FPLPlayer(
            id=7,
            first_name="Bukayo",
            second_name="Saka",
            display_name="Saka",
            club_id=1,
            club_name="Arsenal",
            club_short_name="ARS",
            position="MID",
            price=10.0,
            ownership_percent=30.5,
            availability_status="a",
        )

    def get_snapshot(self):
        return type("Snapshot", (), {"players": [self.player], "current_gameweek": None})()


def database_url():
    return f"sqlite:///./player_details_test_{uuid4().hex}.db"


def test_player_details_aggregates_ordered_results_and_separate_sources():
    url = database_url()
    settings = Settings(database_url=url)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    persistence = ResearchPersistenceService()
    with database.session_factory() as session:
        session.add(Player(id=7))
        session.add_all([
            PlayerGameweekPulse(player_id=7, gameweek=6, minutes=90, total_points=2, goals_scored=0, assists=0),
            PlayerGameweekPulse(player_id=7, gameweek=7, minutes=82, total_points=8, goals_scored=1, assists=0),
        ])
        session.commit()
        thread = persistence.create_thread(
            session, title="GW3 Player Investigation", thread_type=ResearchThreadType.PLAYER
        )
        older_link = persistence.add_collected_link(
            session,
            thread_id=thread.id,
            url="https://example.com/older",
            title="Older report",
            source_type="club",
            player_ids=[7],
        )
        newer_link = persistence.add_collected_link(
            session,
            thread_id=thread.id,
            url="https://example.com/newer",
            title="New report",
            source_type="specialist_fpl",
            player_ids=[7],
        )
        pending = persistence.add_collected_link(
            session,
            thread_id=thread.id,
            url="https://www.reddit.com/r/FantasyPL/pending",
            title="Collected discussion",
            relevance_reason="Possible team news",
            player_ids=[7],
        )
        failed = persistence.add_collected_link(
            session,
            thread_id=thread.id,
            url="https://example.com/failed",
            player_ids=[7],
        )
        failed.status = ResearchLinkStatus.FAILED
        session.commit()
        now = datetime.now(timezone.utc)
        persistence.persist_result(
            session,
            link=older_link,
            summary="Older",
            findings="Older finding",
            evidence="Report",
            player_ids=[7],
            researched_at=now - timedelta(days=1),
        )
        persistence.persist_result(
            session,
            link=newer_link,
            summary="Newest",
            findings="Newest finding",
            evidence="Report",
            uncertainty="Minutes uncertain",
            player_ids=[7],
            researched_at=now,
        )
        TriggerService().manual_trigger(session, 7, "Reassess role")
        TriggerService.create_monitoring(session, player_id=7, description="Next appearance",
                                         category="appearance", condition={"kind": "positive_minutes"})
    app = create_app(
        settings,
        database=database,
        codex_service=CodexService(client=object()),
        fpl_snapshot_service=SnapshotService(),
    )
    try:
        with TestClient(app) as client:
            response = client.get("/fpl/players/7")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["player"]["display_name"] == "Saka"
            assert payload["player"]["club_name"] == "Arsenal"
            assert [item["summary"] for item in payload["completed_research"]] == ["Newest", "Older"]
            newest = payload["completed_research"][0]
            assert newest["source_url"] == "https://example.com/newer"
            assert newest["source_title"] == "New report"
            assert newest["thread_title"] == "GW3 Player Investigation"
            assert newest["thread_type"] == ResearchThreadType.PLAYER
            assert {item["status"] for item in payload["collected_sources"]} == {
                ResearchLinkStatus.COLLECTED,
                ResearchLinkStatus.FAILED,
            }
            assert {item["id"] for item in payload["collected_sources"]} == {pending.id, failed.id}
            assert len({item["id"] for item in payload["completed_research"]}) == 2
            assert [item["gameweek"] for item in payload["recent_pulses"]] == [7, 6]
            assert payload["recent_pulse_summary"]["attacking_blank_streak"] == 0
            assert payload["recent_pulse_summary"]["total_points"] == 10
            assert payload["research_triggers"][0]["description"] == "Reassess role"
            assert payload["monitoring_triggers"][0]["description"] == "Next appearance"

            unknown = client.get("/fpl/players/999")
            assert unknown.status_code == 404

            created = client.post("/fpl/players/7/research")
            assert created.status_code == 201, created.text
            assert created.json()["collect_url"].endswith("/collect")
        with database.session_factory() as session:
            created_thread = persistence.repository.get_thread(session, created.json()["thread_id"])
            assert created_thread.thread_type == ResearchThreadType.PLAYER
            assert "Bukayo Saka" in created_thread.question
    finally:
        database.engine.dispose()
        Path(url.removeprefix("sqlite:///")).unlink(missing_ok=True)
