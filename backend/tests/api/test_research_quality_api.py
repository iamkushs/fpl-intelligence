from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.db.base import Base
from fpl_intelligence.db.session import Database
from fpl_intelligence.models import (
    MonitoringTrigger,
    Player,
    ResearchEvidence,
    ResearchEvidenceType,
    ResearchLink,
    ResearchQualityRun,
    ResearchThread,
    ResearchThreadType,
)
from fpl_intelligence.research.eval2_prompts import (
    EVAL2_COUNTER_SEARCH_PROMPT_VERSION,
    EVAL2_FRESHNESS_PROMPT_VERSION,
    EVAL2_REDDIT_RESEARCH_PROMPT_VERSION,
)


def cutoff():
    return datetime(2026, 8, 15, 12, tzinfo=timezone.utc)


def _database():
    Path(".tmp").mkdir(exist_ok=True)
    settings = Settings(database_url=f"sqlite:///./.tmp/quality_api_test_{uuid4().hex}.db")
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    return settings, database


def _client():
    settings, database = _database()
    app = create_app(settings, database=database, codex_service=CodexService(client=object(), settings=settings))
    with database.session_factory() as session:
        session.add(Player(id=7))
        thread = ResearchThread(title="Saka", thread_type=ResearchThreadType.PLAYER)
        session.add(thread)
        session.commit()
        thread_id = thread.id
    return database, TestClient(app), thread_id


def _evidence(database, thread_id, claim="Saka trained."):
    with database.session_factory() as session:
        item = ResearchEvidence(
            research_thread_id=thread_id,
            claim=claim,
            claim_type="training",
            evidence_type=ResearchEvidenceType.FACT,
            reliability="high",
            relevance="high",
        )
        session.add(item)
        session.commit()
        return item.id


def _link(database, thread_id):
    with database.session_factory() as session:
        item = ResearchLink(
            research_thread_id=thread_id,
            original_url="https://example.com/source",
            canonical_url=f"https://example.com/source/{uuid4().hex}",
            domain="example.com",
        )
        session.add(item)
        session.commit()
        return item.id


def test_start_quality_endpoints_return_stage_prompt_and_identity():
    database, client, thread_id = _client()
    try:
        evidence_id = _evidence(database, thread_id)
        payload = {"player_id": 7, "research_cutoff": cutoff().isoformat()}

        reddit = client.post(f"/research/threads/{thread_id}/quality/reddit", json=payload).json()
        counter = client.post(f"/research/threads/{thread_id}/quality/counter-search", json={**payload, "challenged_claim": "Saka is nailed."}).json()
        fresh = client.post(f"/research/threads/{thread_id}/quality/freshness", json={**payload, "target_evidence_id": evidence_id}).json()

        assert (reddit["stage"], reddit["prompt_version"], reddit["player_id"], reddit["thread_id"]) == ("reddit", EVAL2_REDDIT_RESEARCH_PROMPT_VERSION, 7, thread_id)
        assert (counter["stage"], counter["prompt_version"], counter["player_id"], counter["thread_id"]) == ("counter_search", EVAL2_COUNTER_SEARCH_PROMPT_VERSION, 7, thread_id)
        assert (fresh["stage"], fresh["prompt_version"], fresh["target_evidence_id"]) == ("freshness", EVAL2_FRESHNESS_PROMPT_VERSION, evidence_id)
        assert fresh["research_cutoff"].startswith("2026-08-15T12:00:00")
    finally:
        database.engine.dispose()


def test_counter_search_empty_challenged_claim_returns_422():
    database, client, thread_id = _client()
    try:
        response = client.post(f"/research/threads/{thread_id}/quality/counter-search", json={"player_id": 7, "research_cutoff": cutoff().isoformat(), "challenged_claim": "   "})
        assert response.status_code == 422
    finally:
        database.engine.dispose()


def test_freshness_supersession_requires_superseding_evidence_and_preserves_original():
    database, client, thread_id = _client()
    try:
        original_id = _evidence(database, thread_id)
        newer_id = _evidence(database, thread_id, "Saka missed training.")
        run = client.post(f"/research/threads/{thread_id}/quality/freshness", json={"player_id": 7, "research_cutoff": cutoff().isoformat(), "target_evidence_id": original_id}).json()

        rejected = client.post(f"/research/quality-runs/{run['id']}/complete", json={"outcome": "superseded"})
        accepted = client.post(f"/research/quality-runs/{run['id']}/complete", json={"outcome": "superseded", "superseding_evidence_id": newer_id})

        assert rejected.status_code == 422
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "completed"
        assert accepted.json()["superseding_evidence_id"] == newer_id
        with database.session_factory() as session:
            assert session.get(ResearchEvidence, original_id) is not None
    finally:
        database.engine.dispose()


def test_unresolved_freshness_creates_one_idempotent_monitoring_trigger():
    database, client, thread_id = _client()
    try:
        evidence_id = _evidence(database, thread_id)
        run = client.post(f"/research/threads/{thread_id}/quality/freshness", json={"player_id": 7, "research_cutoff": cutoff().isoformat(), "target_evidence_id": evidence_id}).json()
        payload = {"outcome": "unresolved", "monitoring_condition": {"event": "manager_press_conference"}}

        assert client.post(f"/research/quality-runs/{run['id']}/complete", json=payload).status_code == 200
        assert client.post(f"/research/quality-runs/{run['id']}/complete", json=payload).status_code == 200
        with database.session_factory() as session:
            monitors = list(session.scalars(select(MonitoringTrigger).where(MonitoringTrigger.player_id == 7, MonitoringTrigger.category == "freshness", MonitoringTrigger.active.is_(True))))
            assert len(monitors) == 1
            assert monitors[0].research_thread_id == thread_id
            assert monitors[0].condition == {"event": "manager_press_conference"}
    finally:
        database.engine.dispose()


def test_quality_run_retrieval_returns_attachments_and_state():
    database, client, thread_id = _client()
    try:
        evidence_id = _evidence(database, thread_id)
        link_id = _link(database, thread_id)
        run = client.post(f"/research/threads/{thread_id}/quality/counter-search", json={"player_id": 7, "research_cutoff": cutoff().isoformat(), "challenged_claim": "Saka starts."}).json()
        client.post(f"/research/quality-runs/{run['id']}/complete", json={"outcome": "qualified", "link_ids": [link_id], "evidence_ids": [evidence_id]})

        retrieved = client.get(f"/research/quality-runs/{run['id']}").json()

        assert retrieved["link_ids"] == [link_id]
        assert retrieved["evidence_ids"] == [evidence_id]
        assert retrieved["prompt_version"] == EVAL2_COUNTER_SEARCH_PROMPT_VERSION
        assert retrieved["outcome"] == "qualified"
        assert retrieved["status"] == "completed"
    finally:
        database.engine.dispose()


def test_thread_quality_run_list_is_newest_first_without_duplicates():
    database, client, thread_id = _client()
    try:
        payload = {"player_id": 7, "research_cutoff": cutoff().isoformat()}
        first = client.post(f"/research/threads/{thread_id}/quality/reddit", json=payload).json()
        second = client.post(f"/research/threads/{thread_id}/quality/counter-search", json={**payload, "challenged_claim": "Saka starts."}).json()
        with database.session_factory() as session:
            session.get(ResearchQualityRun, first["id"]).created_at = cutoff()
            session.get(ResearchQualityRun, second["id"]).created_at = cutoff() + timedelta(minutes=1)
            session.commit()

        listed = client.get(f"/research/threads/{thread_id}/quality-runs").json()

        assert [item["id"] for item in listed] == [second["id"], first["id"]]
        assert len({item["id"] for item in listed}) == 2
        assert all(item["stage"] and item["status"] and item["prompt_version"] for item in listed)
    finally:
        database.engine.dispose()
