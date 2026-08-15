from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.db.base import Base
from fpl_intelligence.db.session import Database
from fpl_intelligence.models import Player, ResearchThreadType
from fpl_intelligence.research.persistence import ResearchPersistenceService
from fpl_intelligence.research.two_stage import ResearchExtraction, SearchResult, TwoStageResearchService


class CountingClient:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def execute(self, prompt, config):
        self.calls += 1
        return self.result


class StaticSnapshotService:
    def get_snapshot(self):
        player = type("OfficialPlayer", (), {"id": 7, "first_name": "Bukayo", "second_name": "Saka", "display_name": "Saka"})()
        return type("Snapshot", (), {"players": [player]})()


class StaticSearch:
    def search(self, query, *, domains):
        return [SearchResult(url="https://www.reddit.com/r/FantasyPL/test", title="News", player_names=["Saka"])]


class StaticRetriever:
    def retrieve(self, url):
        return "Saka trained with the team."


class StaticExtractor:
    def extract(self, **kwargs):
        return ResearchExtraction("Training update", "Saka trained.", "Source page", None, ["Saka"])


def _workspace_database_url():
    return f"sqlite:///./research_test_{uuid4().hex}.db"


def test_post_persists_and_get_retrieves_without_codex(execution_result):
    database_url = _workspace_database_url()
    settings = Settings(database_url=database_url)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    client = CountingClient(execution_result)
    app = create_app(
        settings,
        database=database,
        codex_service=CodexService(client=client, settings=settings),
    )

    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/research/documents/run",
                json={"question": "What should be investigated?", "season_id": "2026-27", "gameweek_id": 1},
            )
            assert response.status_code == 201, response.text
            document = response.json()
            document_id = document["id"]
            assert document["content"] == execution_result.final_text
            assert client.calls == 1

            get_response = test_client.get(f"/research/documents/{document_id}")
            assert get_response.status_code == 200
            assert get_response.json()["id"] == document_id
            assert get_response.json()["content"] == execution_result.final_text
            assert client.calls == 1
    finally:
        database.engine.dispose()
        Path(database_url.removeprefix("sqlite:///" )).unlink(missing_ok=True)


def test_get_missing_document_returns_404():
    database_url = _workspace_database_url()
    settings = Settings(database_url=database_url)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    app = create_app(settings, database=database, codex_service=CodexService(client=object()))

    try:
        with TestClient(app) as test_client:
            response = test_client.get("/research/documents/not-found")
        assert response.status_code == 404
    finally:
        database.engine.dispose()
        Path(database_url.removeprefix("sqlite:///" )).unlink(missing_ok=True)


def test_research_run_api_persists_and_retrieves_hierarchy():
    database_url = _workspace_database_url()
    settings = Settings(database_url=database_url)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    app = create_app(settings, database=database, codex_service=CodexService(client=object()))

    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/research/runs",
                json={
                    "season_id": "2026-27",
                    "gameweek_id": 1,
                    "sections": [
                        {
                            "key": "context",
                            "title": "Context",
                            "jobs": [
                                {
                                    "key": "first",
                                    "subject": "First",
                                    "question": "What matters?",
                                },
                                {
                                    "key": "second",
                                    "subject": "Second",
                                    "question": "What follows?",
                                    "dependencies": ["first"],
                                },
                            ],
                        }
                    ],
                },
            )
            assert response.status_code == 201, response.text
            run_id = response.json()["id"]

            retrieved = test_client.get(f"/research/runs/{run_id}")
            assert retrieved.status_code == 200
            jobs = retrieved.json()["sections"][0]["jobs"]
            assert jobs[0]["status"] == "READY"
            assert jobs[1]["status"] == "PENDING"

            listed = test_client.get(f"/research/runs/{run_id}/jobs")
            assert listed.status_code == 200
            assert len(listed.json()) == 2
    finally:
        database.engine.dispose()
        Path(database_url.removeprefix("sqlite:///" )).unlink(missing_ok=True)


def test_execute_research_job_endpoint_returns_job_and_document(execution_result):
    database_url = _workspace_database_url()
    settings = Settings(database_url=database_url)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    client = CountingClient(execution_result)
    app = create_app(settings, database=database, codex_service=CodexService(client=client, settings=settings))

    try:
        with TestClient(app) as test_client:
            created = test_client.post(
                "/research/runs",
                json={
                    "sections": [{
                        "title": "Context",
                        "jobs": [{"key": "first", "subject": "First", "question": "Return a note."}],
                    }],
                },
            )
            assert created.status_code == 201, created.text
            job_id = created.json()["sections"][0]["jobs"][0]["id"]

            response = test_client.post(f"/research/jobs/{job_id}/execute")

            assert response.status_code == 200, response.text
            assert response.json()["job"]["status"] == "COMPLETE"
            assert response.json()["document"]["research_job_id"] == job_id
            assert client.calls == 1
    finally:
        database.engine.dispose()
        Path(database_url.removeprefix("sqlite:///" )).unlink(missing_ok=True)


def test_two_stage_thread_api_collects_lists_researches_and_returns_results():
    database_url = _workspace_database_url()
    settings = Settings(database_url=database_url)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    with database.session_factory() as session:
        session.add(Player(id=7))
        session.commit()
        thread = ResearchPersistenceService().create_thread(
            session, title="Arsenal", thread_type=ResearchThreadType.DISCOVERY
        )
        thread_id = thread.id
    workflow = TwoStageResearchService(
        search_provider=StaticSearch(), retriever=StaticRetriever(), extractor=StaticExtractor()
    )
    app = create_app(
        settings,
        database=database,
        codex_service=CodexService(client=object()),
        fpl_snapshot_service=StaticSnapshotService(),
        two_stage_research_service=workflow,
    )
    try:
        with TestClient(app) as test_client:
            collected = test_client.post(f"/research/threads/{thread_id}/collect", json={})
            assert collected.status_code == 200, collected.text
            assert collected.json()["links_added"] == 1
            links = test_client.get(f"/research/threads/{thread_id}/links")
            assert links.status_code == 200
            link_id = links.json()[0]["id"]
            researched = test_client.post(
                f"/research/threads/{thread_id}/research", json={"link_ids": [link_id]}
            )
            assert researched.status_code == 200, researched.text
            results = test_client.get(f"/research/threads/{thread_id}/results")
            assert results.status_code == 200
            assert results.json()[0]["source_url"].startswith("https://www.reddit.com/")
            assert results.json()[0]["player_ids"] == [7]
    finally:
        database.engine.dispose()
        Path(database_url.removeprefix("sqlite:///")).unlink(missing_ok=True)
