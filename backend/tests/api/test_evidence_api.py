from fastapi.testclient import TestClient
from sqlalchemy import event

from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.models import Player, ResearchThreadType
from fpl_intelligence.research.persistence import ResearchPersistenceService


class SnapshotService:
    def get_snapshot(self):
        return type("Snapshot", (), {"players": [], "current_gameweek": None})()


def app_for(database):
    return create_app(Settings(database_url="sqlite:///unused.db"), database=database,
                      codex_service=CodexService(client=object()), fpl_snapshot_service=SnapshotService())


def test_evidence_and_source_cluster_endpoints(database):
    persistence = ResearchPersistenceService()
    with database.session_factory() as session:
        session.add_all([Player(id=1), Player(id=2)])
        session.commit()
        thread = persistence.create_thread(session, title="Evidence", thread_type=ResearchThreadType.PLAYER)
        first_link = persistence.add_collected_link(session, thread_id=thread.id, url="https://example.com/one")
        second_link = persistence.add_collected_link(session, thread_id=thread.id, url="https://example.com/two")

    with TestClient(app_for(database)) as client:
        cluster = client.post("/research/source-clusters", json={"research_thread_id": thread.id, "narrative": "Report"})
        assert cluster.status_code == 201, cluster.text
        cluster_id = cluster.json()["id"]
        assert client.post(f"/research/source-clusters/{cluster_id}/links", json={"research_link_id": first_link.id, "lineage_type": "original"}).json()["independent_confirmation_count"] == 1
        assert client.post(f"/research/source-clusters/{cluster_id}/links", json={"research_link_id": second_link.id, "lineage_type": "derivative"}).json()["independent_confirmation_count"] == 1
        created = client.post("/research/evidence", json={
            "research_thread_id": thread.id, "claim": "Started wide", "claim_type": "starting_status", "evidence_type": "report",
            "reliability": "high", "relevance": "medium", "player_ids": [1], "research_link_id": first_link.id,
            "source_cluster_id": cluster_id, "season": "2026/27",
        })
        assert created.status_code == 201, created.text
        evidence_id = created.json()["id"]
        assert created.json()["source_provenance"]["url"] == "https://example.com/one"
        assert client.post(f"/research/evidence/{evidence_id}/players", json={"player_ids": [1, 2]}).json()["player_ids"] == [1, 2]
        second = client.post("/research/evidence", json={
            "research_thread_id": thread.id, "claim": "Second observation", "claim_type": "performance", "evidence_type": "fact",
            "reliability": "high", "relevance": "medium",
        }).json()["id"]
        third = client.post("/research/evidence", json={
            "research_thread_id": thread.id, "claim": "Third observation", "claim_type": "performance", "evidence_type": "fact",
            "reliability": "high", "relevance": "medium",
        }).json()["id"]
        assert client.post(f"/research/evidence/{third}/relations", json={"to_evidence_id": second, "relation_type": "supports"}).status_code == 201
        relation_queries = []

        def capture(_, __, statement, ___, ____, _____):
            if "evidence_relations" in statement.lower():
                relation_queries.append(statement)

        event.listen(database.engine, "before_cursor_execute", capture)
        try:
            response = client.get(f"/research/threads/{thread.id}/evidence")
        finally:
            event.remove(database.engine, "before_cursor_execute", capture)
        assert len(response.json()) == 3
        assert len(relation_queries) == 1
        assert len(client.get("/fpl/players/1/evidence").json()) == 1
        assert client.get(f"/research/evidence/{evidence_id}").status_code == 200
        assert client.post("/research/evidence", json={"research_thread_id": thread.id, "claim": "Bad", "claim_type": "other", "evidence_type": "bad", "reliability": "high", "relevance": "high"}).status_code == 422
