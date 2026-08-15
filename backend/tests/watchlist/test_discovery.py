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
from fpl_intelligence.models import (
    Player, ResearchResult, ResearchThreadType, WatchlistEntry, WatchlistSuggestion,
    WatchlistSuggestionStatus,
)
from fpl_intelligence.research.persistence import ResearchPersistenceService
from fpl_intelligence.watchlist.discovery import DiscoveryCandidate, DiscoveryService


def official(player_id, first, second, display, club="Arsenal"):
    return FPLPlayer(
        id=player_id, first_name=first, second_name=second, display_name=display,
        club_id=1, club_name=club, club_short_name=club[:3].upper(), position="MID",
        price=8.0, ownership_percent=10.0, availability_status="a",
    )


PLAYERS = [
    official(7, "Bukayo", "Saka", "Saka"),
    official(8, "Cole", "Palmer", "Palmer", "Chelsea"),
    official(9, "Martin", "Odegaard", "Odegaard"),
    official(10, "Adam", "Smith", "Smith", "Bournemouth"),
    official(11, "Ben", "Smith", "Smith", "Fulham"),
]


class SnapshotService:
    def get_snapshot(self):
        return type("Snapshot", (), {"players": PLAYERS, "current_gameweek": None})()


class Analyzer:
    def __init__(self):
        self.calls = 0

    def analyze(self, *, thread, results):
        self.calls += 1
        result_id = results[0].id
        return [
            DiscoveryCandidate("Bukayo Saka", "Role and underlying numbers have improved.", [result_id]),
            DiscoveryCandidate("Saka", "Duplicate conclusion.", [result_id]),
            DiscoveryCandidate("Cole Palmer", "Strong recent evidence.", [result_id]),
            DiscoveryCandidate("Martin Odegaard", "Now taking more set pieces.", [result_id]),
            DiscoveryCandidate("Smith", "Ambiguous name.", [result_id]),
            DiscoveryCandidate("Invented Person", "Unknown player.", [result_id]),
            DiscoveryCandidate("Saka", "No researched evidence.", ["not-a-result"]),
        ]


def test_discovery_lifecycle_is_evidence_backed_deduplicated_and_user_controlled():
    url = f"sqlite:///./discovery_test_{uuid4().hex}.db"
    settings = Settings(database_url=url)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    persistence = ResearchPersistenceService()
    analyzer = Analyzer()
    with database.session_factory() as session:
        session.add_all(Player(id=item.id) for item in PLAYERS)
        session.add(WatchlistEntry(player_id=7, active=False, added_source="user"))
        session.add(WatchlistEntry(player_id=8, active=True, added_source="user"))
        session.commit()
        thread = persistence.create_thread(
            session, title="Emerging midfield roles", thread_type=ResearchThreadType.DISCOVERY,
            question="Who is becoming more relevant?",
        )
        link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/roles")
        result = persistence.persist_result(
            session, link=link, summary="Role changes", findings="Several midfield roles changed.",
            evidence="Manager comments and match data.", player_ids=[], researched_at=datetime.now(timezone.utc),
        )
        empty_thread = persistence.create_thread(
            session, title="Titles are not evidence", thread_type=ResearchThreadType.DISCOVERY,
        )
        persistence.add_collected_link(
            session, thread_id=empty_thread.id, url="https://premierleague.com/title-only", title="Saka stars",
        )

    app = create_app(
        settings, database=database, codex_service=CodexService(client=object()),
        fpl_snapshot_service=SnapshotService(),
    )
    app.state.discovery_service = DiscoveryService(analyzer)
    try:
        with TestClient(app) as client:
            no_evidence = client.post(f"/research/threads/{empty_thread.id}/discover-players")
            assert no_evidence.status_code == 200
            assert no_evidence.json()["research_results_considered"] == 0
            assert analyzer.calls == 0

            generated = client.post(f"/research/threads/{thread.id}/discover-players")
            assert generated.status_code == 200
            counts = generated.json()
            assert counts["research_results_considered"] == 1
            assert counts["candidate_players_identified"] == 7
            assert counts["suggestions_created"] == 2
            assert counts["duplicates_skipped"] == 1
            assert counts["already_watchlisted_players_skipped"] == 1
            assert set(counts["unresolved_player_references"]) == {"Smith", "Invented Person"}
            assert counts["candidates_without_evidence_skipped"] == 1

            repeated = client.post(f"/research/threads/{thread.id}/discover-players").json()
            assert repeated["suggestions_created"] == 0
            assert repeated["duplicates_skipped"] >= 3

            pending = client.get("/watchlist/suggestions").json()
            assert {item["player_name"] for item in pending} == {"Saka", "Odegaard"}
            saka = next(item for item in pending if item["player_name"] == "Saka")
            odegaard = next(item for item in pending if item["player_name"] == "Odegaard")
            assert saka["research_thread_id"] == thread.id
            assert saka["evidence"][0]["research_result_id"] == result.id
            assert saka["evidence"][0]["source_url"].endswith("/roles")

            accepted = client.post(f"/watchlist/suggestions/{saka['id']}/accept")
            assert accepted.status_code == 200
            assert accepted.json()["watchlist"]["added_source"] == "research"
            assert accepted.json()["watchlist"]["addition_reason"] == saka["reason"]
            assert client.post(f"/watchlist/suggestions/{saka['id']}/accept").status_code == 200

            rejected = client.post(f"/watchlist/suggestions/{odegaard['id']}/reject")
            assert rejected.status_code == 200
            assert client.post(f"/watchlist/suggestions/{odegaard['id']}/reject").status_code == 200
            assert client.get("/watchlist/suggestions").json() == []

        with database.session_factory() as session:
            suggestions = session.query(WatchlistSuggestion).all()
            assert {item.status for item in suggestions} == {
                WatchlistSuggestionStatus.ACCEPTED, WatchlistSuggestionStatus.REJECTED,
            }
            accepted_row = next(item for item in suggestions if item.status == WatchlistSuggestionStatus.ACCEPTED)
            rejected_row = next(item for item in suggestions if item.status == WatchlistSuggestionStatus.REJECTED)
            assert accepted_row.player_id == 7
            assert accepted_row.research_thread_id == thread.id
            assert {item.id for item in accepted_row.research_results} == {result.id}
            assert rejected_row.rejected_at is not None
            assert session.get(ResearchResult, result.id) is not None
            assert session.get(type(thread), thread.id) is not None
            entry = session.query(WatchlistEntry).filter_by(player_id=7).one()
            assert entry.active is True and entry.added_source == "research"
            assert session.query(WatchlistEntry).count() == 2
    finally:
        database.engine.dispose()
        Path(url.removeprefix("sqlite:///./")).unlink(missing_ok=True)
