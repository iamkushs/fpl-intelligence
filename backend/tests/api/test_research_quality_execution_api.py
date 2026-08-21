from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.db.base import Base
from fpl_intelligence.db.session import Database
from fpl_intelligence.models import MonitoringTrigger, Player, ResearchEvidence, ResearchEvidenceType, ResearchThread, ResearchThreadType
from fpl_intelligence.research.quality_execution import (
    CounterSearchQualityOutput,
    Eval2QualityExecutionService,
    FreshnessQualityOutput,
    RedditQualityOutput,
)
from fpl_intelligence.research.source_discovery import (
    AtomicEvidencePayload,
    Eval2SourceDiscoveryService,
    EvidenceExtractionOutput,
    SourceCandidatePayload,
)
from fpl_intelligence.research.two_stage import PlayerResolver, ResearchExtraction


def cutoff():
    return datetime(2026, 8, 15, 12, tzinfo=timezone.utc)


def candidate(url="https://www.reddit.com/r/FantasyPL/comments/1"):
    return SourceCandidatePayload(
        url=url,
        target_dimensions=["availability"],
        usefulness="Useful FPL source.",
        source_category="supporter_reddit" if "reddit.com" in url else "credible_general",
        expected_relevance="high",
        lineage_type="original",
    )


class Retriever:
    def retrieve(self, url):
        return "Saka trained."


class Page:
    def extract(self, **kwargs):
        return ResearchExtraction("Summary", "Findings", "Evidence", None, ["Bukayo Saka"])


class Atomic:
    def extract(self, *, prompt, prompt_version, result):
        return EvidenceExtractionOutput(evidence=[
            AtomicEvidencePayload(
                claim=f"{result.research_link.original_url} claim",
                claim_type="availability",
                evidence_type=ResearchEvidenceType.SUPPORTER_OBSERVATION,
                player_ids=[7],
                reliability="medium",
                relevance="high",
            )
        ])


class RedditProvider:
    def __init__(self, output):
        self.output = output

    def research(self, **kwargs):
        return self.output


class CounterProvider:
    def research(self, **kwargs):
        return CounterSearchQualityOutput(outcome="qualified", candidates=[candidate("https://example.com/counter")])


class FreshnessProvider:
    def research(self, **kwargs):
        return FreshnessQualityOutput(outcome="unresolved", monitoring_condition={"event": "manager_press_conference"})


def source_service():
    return Eval2SourceDiscoveryService(
        discovery_provider=object(),
        retriever=Retriever(),
        page_research_provider=Page(),
        atomic_provider=Atomic(),
    )


def execution_service(reddit=None, counter=None, freshness=None):
    player = SimpleNamespace(id=7, first_name="Bukayo", second_name="Saka", display_name="Saka")
    return Eval2QualityExecutionService(
        source_service=source_service(),
        player_resolver=PlayerResolver([player]),
        reddit_provider=reddit,
        counter_provider=counter,
        freshness_provider=freshness,
    )


def app_context(execution):
    Path(".tmp").mkdir(exist_ok=True)
    settings = Settings(database_url=f"sqlite:///./.tmp/quality_execution_api_{uuid4().hex}.db")
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    app = create_app(
        settings,
        database=database,
        codex_service=CodexService(client=object(), settings=settings),
        eval2_quality_execution_service=execution,
    )
    with database.session_factory() as session:
        session.add(Player(id=7))
        thread = ResearchThread(title="Saka", thread_type=ResearchThreadType.PLAYER)
        session.add(thread)
        session.commit()
        thread_id = thread.id
    return database, TestClient(app), thread_id


def target_evidence(database, thread_id):
    with database.session_factory() as session:
        evidence = ResearchEvidence(
            research_thread_id=thread_id,
            claim="Saka trained.",
            claim_type="training",
            evidence_type=ResearchEvidenceType.FACT,
            reliability="high",
            relevance="high",
        )
        session.add(evidence)
        session.commit()
        return evidence.id


def test_reddit_execute_endpoint_attaches_reddit_evidence():
    database, client, thread_id = app_context(execution_service(reddit=RedditProvider(RedditQualityOutput(candidates=[candidate()]))))
    try:
        run = client.post(f"/research/threads/{thread_id}/quality/reddit", json={"player_id": 7, "research_cutoff": cutoff().isoformat()}).json()
        response = client.post(f"/research/quality-runs/{run['id']}/execute-reddit")

        body = response.json()
        assert response.status_code == 200
        assert body["stage"] == "reddit"
        assert body["status"] == "completed"
        assert body["link_ids"]
        assert body["evidence_ids"]
    finally:
        database.engine.dispose()


def test_counter_search_execute_endpoint_records_qualified_outcome():
    database, client, thread_id = app_context(execution_service(counter=CounterProvider()))
    try:
        run = client.post(
            f"/research/threads/{thread_id}/quality/counter-search",
            json={"player_id": 7, "research_cutoff": cutoff().isoformat(), "challenged_claim": "Saka starts."},
        ).json()
        response = client.post(f"/research/quality-runs/{run['id']}/execute-counter-search")

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "completed"
        assert body["outcome"] == "qualified"
        assert body["evidence_ids"]
    finally:
        database.engine.dispose()


def test_freshness_execute_endpoint_creates_active_monitoring_trigger():
    database, client, thread_id = app_context(execution_service(freshness=FreshnessProvider()))
    try:
        evidence_id = target_evidence(database, thread_id)
        run = client.post(
            f"/research/threads/{thread_id}/quality/freshness",
            json={"player_id": 7, "research_cutoff": cutoff().isoformat(), "target_evidence_id": evidence_id},
        ).json()
        response = client.post(f"/research/quality-runs/{run['id']}/execute-freshness")

        body = response.json()
        assert response.status_code == 200
        assert body["outcome"] == "unresolved"
        assert body["status"] == "completed"
        with database.session_factory() as session:
            monitors = list(session.scalars(select(MonitoringTrigger).where(MonitoringTrigger.category == "freshness", MonitoringTrigger.active.is_(True))))
            assert len(monitors) == 1
    finally:
        database.engine.dispose()


def test_execution_validation_and_malformed_provider_output_failure():
    database, client, thread_id = app_context(execution_service(reddit=RedditProvider(SimpleNamespace(candidates=None))))
    try:
        counter = client.post(
            f"/research/threads/{thread_id}/quality/counter-search",
            json={"player_id": 7, "research_cutoff": cutoff().isoformat(), "challenged_claim": "Saka starts."},
        ).json()
        assert client.post(f"/research/quality-runs/{counter['id']}/execute-reddit").status_code == 422

        reddit = client.post(f"/research/threads/{thread_id}/quality/reddit", json={"player_id": 7, "research_cutoff": cutoff().isoformat()}).json()
        response = client.post(f"/research/quality-runs/{reddit['id']}/execute-reddit")

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "failed"
        assert body["failure_reason"] == "Provider candidates must be a list"
        assert body["evidence_ids"] == []
    finally:
        database.engine.dispose()
