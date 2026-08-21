from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from fpl_intelligence.db.base import Base
from fpl_intelligence.models import (
    EvidenceRelation,
    MonitoringTrigger,
    Player,
    ResearchEvidence,
    ResearchEvidenceType,
    ResearchLink,
    ResearchQualityStatus,
    ResearchThreadType,
)
from fpl_intelligence.research.persistence import ResearchPersistenceService
from fpl_intelligence.research.quality import ResearchQualityService
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


def candidate(url):
    return SourceCandidatePayload(
        url=url,
        target_dimensions=["availability"],
        usefulness="Useful FPL source.",
        source_category="supporter_reddit" if "reddit" in url else "credible_general",
        expected_relevance="high",
        lineage_type="original",
    )


class Retriever:
    def __init__(self, failures=()):
        self.failures = set(failures)

    def retrieve(self, url):
        if url in self.failures:
            raise RuntimeError("blocked")
        return "Saka trained."


class Page:
    def extract(self, **kwargs):
        return ResearchExtraction("Summary", "Findings", "Evidence", None, ["Bukayo Saka"])


class Atomic:
    def __init__(self, evidence_type=ResearchEvidenceType.SUPPORTER_OBSERVATION):
        self.evidence_type = evidence_type

    def extract(self, *, prompt, prompt_version, result):
        return EvidenceExtractionOutput(evidence=[
            AtomicEvidencePayload(
                claim=f"{result.research_link.original_url} claim",
                claim_type="availability",
                evidence_type=self.evidence_type,
                player_ids=[7],
                reliability="medium",
                relevance="high",
            )
        ])


class RedditProvider:
    def __init__(self, urls):
        self.urls = urls

    def research(self, **kwargs):
        return RedditQualityOutput(candidates=[candidate(url) for url in self.urls])


class CounterProvider:
    def __init__(self, outcome, urls=()):
        self.outcome = outcome
        self.urls = urls

    def research(self, **kwargs):
        return CounterSearchQualityOutput(outcome=self.outcome, candidates=[candidate(url) for url in self.urls])


class FreshnessProvider:
    def __init__(self, outcome, urls=(), superseding_candidate_index=None, monitoring_condition=None):
        self.outcome = outcome
        self.urls = urls
        self.superseding_candidate_index = superseding_candidate_index
        self.monitoring_condition = monitoring_condition

    def research(self, **kwargs):
        return FreshnessQualityOutput(
            outcome=self.outcome,
            candidates=[candidate(url) for url in self.urls],
            superseding_candidate_index=self.superseding_candidate_index,
            monitoring_condition=self.monitoring_condition,
        )


def source_service(retriever=None, atomic=None):
    return Eval2SourceDiscoveryService(
        discovery_provider=object(),
        retriever=retriever or Retriever(),
        page_research_provider=Page(),
        atomic_provider=atomic or Atomic(),
    )


def resolver():
    player = SimpleNamespace(id=7, first_name="Bukayo", second_name="Saka", display_name="Saka")
    return PlayerResolver([player])


def execution(source=None, reddit=None, counter=None, freshness=None):
    return Eval2QualityExecutionService(
        source_service=source or source_service(),
        player_resolver=resolver(),
        reddit_provider=reddit,
        counter_provider=counter,
        freshness_provider=freshness,
    )


def evidence(session, thread_id, claim="Saka trained."):
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
    return item


def session_with_thread():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(Player(id=7))
    session.commit()
    thread = ResearchPersistenceService().create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    return engine, session, thread


def test_reddit_specialized_execution_filters_non_reddit_and_reuses_link():
    engine, session, thread = session_with_thread()
    try:
        existing = ResearchPersistenceService().add_collected_link(session, thread_id=thread.id, url="https://www.reddit.com/r/FantasyPL/comments/1", player_ids=[7])
        run = ResearchQualityService().start_reddit_run(session, thread_id=thread.id, player_id=7, research_cutoff=cutoff())
        result = execution(reddit=RedditProvider([existing.original_url, "https://example.com/not-reddit"])).execute_reddit(session, run.id)

        refreshed = ResearchQualityService().repository.get_run_detail(session, run.id)
        evidence_item = session.get(ResearchEvidence, result["evidence_ids"][0])
        assert refreshed.status == ResearchQualityStatus.COMPLETED
        assert [link.id for link in refreshed.links] == [existing.id]
        assert evidence_item.evidence_type == ResearchEvidenceType.SUPPORTER_OBSERVATION
        assert session.scalar(select(func.count()).select_from(ResearchLink)) == 1
    finally:
        session.close(); engine.dispose()


def test_reddit_partial_failure_attaches_success_only():
    engine, session, thread = session_with_thread()
    try:
        ok = "https://reddit.com/r/FantasyPL/comments/ok"
        bad = "https://old.reddit.com/r/FantasyPL/comments/bad"
        run = ResearchQualityService().start_reddit_run(session, thread_id=thread.id, player_id=7, research_cutoff=cutoff())
        service = source_service(retriever=Retriever(failures={bad}))
        result = execution(source=service, reddit=RedditProvider([ok, bad])).execute_reddit(session, run.id)

        refreshed = ResearchQualityService().repository.get_run_detail(session, run.id)
        assert refreshed.status == ResearchQualityStatus.PARTIAL
        assert len(result["link_ids"]) == 1
        assert len(result["evidence_ids"]) == 1
        assert result["failures"]
    finally:
        session.close(); engine.dispose()


def test_counter_search_qualified_and_no_counter_evidence_zero_evidence():
    engine, session, thread = session_with_thread()
    try:
        run = ResearchQualityService().start_counter_search_run(session, thread_id=thread.id, player_id=7, challenged_claim="Saka starts.", research_cutoff=cutoff())
        result = execution(counter=CounterProvider("qualified", ["https://example.com/counter"])).execute_counter_search(session, run.id)
        refreshed = ResearchQualityService().repository.get_run_detail(session, run.id)
        assert refreshed.challenged_claim == "Saka starts."
        assert refreshed.outcome == "qualified"
        assert refreshed.status == ResearchQualityStatus.COMPLETED
        assert result["link_ids"] and result["evidence_ids"]

        empty = ResearchQualityService().start_counter_search_run(session, thread_id=thread.id, player_id=7, challenged_claim="Saka starts.", research_cutoff=cutoff())
        execution(counter=CounterProvider("no_credible_counter_evidence")).execute_counter_search(session, empty.id)
        assert ResearchQualityService().repository.get_run_detail(session, empty.id).outcome == "no_credible_counter_evidence"
    finally:
        session.close(); engine.dispose()


def test_freshness_unresolved_creates_one_monitoring_trigger():
    engine, session, thread = session_with_thread()
    try:
        target = evidence(session, thread.id)
        run = ResearchQualityService().start_freshness_run(session, thread_id=thread.id, player_id=7, target_evidence_id=target.id, research_cutoff=cutoff())
        execution(freshness=FreshnessProvider("unresolved", monitoring_condition={"event": "manager_press_conference"})).execute_freshness(session, run.id)
        ResearchQualityService().complete_freshness_run(session, run_id=run.id, outcome="unresolved", monitoring_condition={"event": "manager_press_conference"})
        monitors = list(session.scalars(select(MonitoringTrigger).where(MonitoringTrigger.category == "freshness", MonitoringTrigger.active.is_(True))))
        assert ResearchQualityService().repository.get_run_detail(session, run.id).outcome == "unresolved"
        assert len(monitors) == 1
    finally:
        session.close(); engine.dispose()


def test_freshness_supersession_records_new_evidence_relation_and_preserves_original():
    engine, session, thread = session_with_thread()
    try:
        target = evidence(session, thread.id)
        run = ResearchQualityService().start_freshness_run(session, thread_id=thread.id, player_id=7, target_evidence_id=target.id, research_cutoff=cutoff())
        result = execution(freshness=FreshnessProvider("superseded", ["https://example.com/fresh"], superseding_candidate_index=0)).execute_freshness(session, run.id)
        refreshed = ResearchQualityService().repository.get_run_detail(session, run.id)
        relation = session.scalar(select(EvidenceRelation).where(EvidenceRelation.from_evidence_id == refreshed.superseding_evidence_id, EvidenceRelation.to_evidence_id == target.id))

        assert session.get(ResearchEvidence, target.id) is not None
        assert session.get(ResearchEvidence, refreshed.superseding_evidence_id) is not None
        assert refreshed.superseding_evidence_id == result["evidence_ids"][0]
        assert relation is not None and relation.relation_type == "supersedes"
        assert refreshed.links and refreshed.evidence
        assert refreshed.status == ResearchQualityStatus.COMPLETED
    finally:
        session.close(); engine.dispose()
