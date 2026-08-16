import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.orm import Session

from fpl_intelligence.db.base import Base
from fpl_intelligence.models import (
    EvidenceRelation,
    Player,
    PlayerResearchTrigger,
    ResearchDiscoveryExecution,
    ResearchEvidence,
    ResearchEvidenceType,
    ResearchLink,
    ResearchLinkStatus,
    ResearchPageResearchAttempt,
    ResearchPageResearchAttemptStatus,
    ResearchResult,
    ResearchSourceCandidate,
    ResearchThreadType,
    ResearchTriggerSource,
    ResearchTriggerStatus,
)
from fpl_intelligence.research.eval2_prompts import EVAL2_DISCOVERY_DIMENSIONS, EVAL2_SOURCE_HIERARCHY
from fpl_intelligence.research.evidence import ResearchEvidenceService
from fpl_intelligence.research.persistence import ResearchPersistenceService
from fpl_intelligence.research.situations import ResearchSituationService
from fpl_intelligence.research.source_discovery import (
    AtomicEvidencePayload,
    CodexEval2AtomicEvidenceProvider,
    CodexEval2DiscoveryProvider,
    CodexEval2PageResearchProvider,
    DiscoveryOutput,
    Eval2SourceDiscoveryService,
    EvidenceExtractionOutput,
    EvidenceRelationPayload,
    HypothesisRelationPayload,
    SourceCandidatePayload,
)
from fpl_intelligence.research.two_stage import PlayerResolver, ResearchExtraction


@pytest.fixture
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        value.add_all([Player(id=7), Player(id=8), Player(id=9)])
        value.commit()
        yield value
    engine.dispose()


def cutoff():
    return datetime(2026, 8, 15, 12, tzinfo=timezone.utc)


def fpl_player(player_id, first, second, display):
    return SimpleNamespace(id=player_id, first_name=first, second_name=second, display_name=display)


class FakeDiscovery:
    def __init__(self, outputs=None, error_phases=()):
        self.outputs = outputs or {}
        self.error_phases = set(error_phases)
        self.calls = []

    def discover(self, *, prompt, prompt_version, phase):
        self.calls.append({"prompt": prompt, "prompt_version": prompt_version, "phase": phase})
        if phase in self.error_phases:
            raise RuntimeError(f"{phase} unavailable")
        return self.outputs.get(phase, DiscoveryOutput())


class ModelDiscovery(FakeDiscovery):
    def discover(self, *, prompt, prompt_version, phase):
        output = super().discover(prompt=prompt, prompt_version=prompt_version, phase=phase)
        return DiscoveryOutput(candidates=output.candidates, known_gaps=output.known_gaps, model_id=f"{phase}-model")


class FakeRetriever:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.calls = []

    def retrieve(self, url):
        self.calls.append(url)
        if url in self.failures:
            raise RuntimeError("blocked")
        return "Manager said Saka trained and may start."


class FakePageResearch:
    def __init__(self):
        self.calls = []

    def extract(self, *, prompt, prompt_version, thread, link, page_content):
        self.calls.append({"prompt": prompt, "prompt_version": prompt_version, "link_id": link.id})
        return ResearchExtraction(
            summary="Training update",
            findings="Saka trained with the group.",
            evidence="Manager said Saka trained.",
            uncertainties="Lineup not confirmed.",
            referenced_players=["Bukayo Saka"],
        )


class ModelPageResearch(FakePageResearch):
    def extract(self, *, prompt, prompt_version, thread, link, page_content):
        link.discovery_metadata = {**(link.discovery_metadata or {}), "page_research_model_id": "page-model"}
        return super().extract(prompt=prompt, prompt_version=prompt_version, thread=thread, link=link, page_content=page_content)


class FakeAtomic:
    def __init__(self, output=None):
        self.output = output or EvidenceExtractionOutput()
        self.calls = []

    def extract(self, *, prompt, prompt_version, result):
        self.calls.append({"prompt": prompt, "prompt_version": prompt_version, "result_id": result.id})
        return self.output


class FakeCodex:
    def __init__(self, final_text, model="test-model"):
        self.final_text = final_text
        self.model = model
        self.prompts = []

    def execute(self, *, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(final_text=self.final_text, model=self.model)


def service(discovery=None, retriever=None, page=None, atomic=None):
    return Eval2SourceDiscoveryService(
        discovery_provider=discovery or FakeDiscovery(),
        retriever=retriever or FakeRetriever(),
        page_research_provider=page or FakePageResearch(),
        atomic_provider=atomic or FakeAtomic(),
    )


def resolver():
    return PlayerResolver([
        fpl_player(7, "Bukayo", "Saka", "Saka"),
        fpl_player(8, "Martin", "Odegaard", "Odegaard"),
    ])


def candidate(url="https://premierleague.com/news/saka", **overrides):
    payload = dict(
        url=url,
        source="Premier League",
        publisher="Premier League",
        title="Team news",
        target_dimensions=["availability", "starting likelihood / expected XI"],
        usefulness="Direct FPL availability signal.",
        source_category="official_primary",
        expected_relevance="high",
        published_at=cutoff() - timedelta(hours=2),
        recency="same day",
        lineage_type="original",
        lineage_notes="primary source",
        query="Saka injury team news",
    )
    payload.update(overrides)
    return SourceCandidatePayload(**payload)


def test_two_phase_player_discovery_persists_candidates_links_cutoff_and_prompts(session):
    discovery = FakeDiscovery(outputs={
        "broad": DiscoveryOutput(candidates=[candidate()], known_gaps=["penalties"]),
        "targeted": DiscoveryOutput(candidates=[candidate("https://fantasyfootballscout.co.uk/saka-penalties", source_category="specialist_direct", target_dimensions=["penalties"])])
    })
    svc = service(discovery=discovery)

    state = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff(), known_missing_dimensions=["set pieces"], durable_context={"recent_coverage": []})

    assert state["status"] == "partial"
    assert [call["phase"] for call in discovery.calls] == ["broad", "targeted"]
    broad_prompt = discovery.calls[0]["prompt"]
    assert "Player" in broad_prompt and "FPL decision relevance" in broad_prompt
    for dimension in ("availability", "penalties", "credible information contrary"):
        assert dimension in broad_prompt
    for source_class in EVAL2_SOURCE_HIERARCHY:
        assert source_class in broad_prompt
    assert set(EVAL2_DISCOVERY_DIMENSIONS)
    assert state["research_cutoff"].replace(tzinfo=timezone.utc) == cutoff()
    assert {item["discovery_phase"] for item in state["candidates"]} == {"broad", "targeted"}
    assert session.scalar(select(func.count()).select_from(ResearchLink)) == 2
    assert session.scalar(select(func.count()).select_from(ResearchEvidence)) == 0


def test_discovery_model_metadata_is_durable_and_exposed_after_reload(session):
    discovery = ModelDiscovery(outputs={
        "broad": DiscoveryOutput(candidates=[candidate()], known_gaps=["penalties"]),
        "targeted": DiscoveryOutput(candidates=[candidate("https://fantasyfootballscout.co.uk/saka-penalties", source_category="specialist_direct", target_dimensions=["penalties"])]),
    })
    svc = service(discovery=discovery)

    state = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    session.expire_all()
    persisted = session.get(ResearchDiscoveryExecution, state["id"])
    reloaded = svc.execution_state(session, state["id"])

    assert state["status"] == "partial"
    assert persisted.model_metadata == {"broad_model_id": "broad-model", "targeted_model_id": "targeted-model"}
    assert reloaded["model_metadata"] == persisted.model_metadata


def test_discovery_accepts_optional_situation_and_trigger_but_rejects_unknown_player(session):
    situation = ResearchSituationService().create_situation(session, title="Right wing role", context="Role change", fpl_relevance="Minutes", player_ids=[7])
    trigger = PlayerResearchTrigger(
        player_id=7,
        trigger_type="manual",
        episode_key="now",
        source=ResearchTriggerSource.USER,
        status=ResearchTriggerStatus.OPEN,
        description="Investigate role",
    )
    session.add(trigger)
    session.commit()
    svc = service(discovery=FakeDiscovery(outputs={"broad": DiscoveryOutput(candidates=[candidate()])}))

    with_context = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff(), situation_id=situation.id, trigger_id=trigger.id)
    without_context = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())

    assert with_context["research_situation_id"] == situation.id
    assert with_context["trigger_id"] == trigger.id
    assert without_context["research_situation_id"] is None
    with pytest.raises(LookupError):
        svc.start_player_discovery(session, player_id=999, research_cutoff=cutoff())


def test_malformed_candidate_is_rejected_without_losing_valid_sources(session):
    discovery = FakeDiscovery(outputs={
        "broad": DiscoveryOutput(candidates=[
            candidate(source_category="bad"),
            candidate("https://premierleague.com/valid"),
            candidate("https://premierleague.com/future", published_at=cutoff() + timedelta(days=1)),
        ])
    })
    state = service(discovery=discovery).start_player_discovery(session, player_id=7, research_cutoff=cutoff())

    assert state["status"] == "partial"
    assert state["candidate_count"] == 1
    assert "Unknown source category" in state["failure_reason"]
    assert "after the research cutoff" in state["failure_reason"]


def test_canonical_url_reuse_and_duplicate_candidate_idempotency(session):
    svc = service(discovery=FakeDiscovery(outputs={
        "broad": DiscoveryOutput(candidates=[
            candidate("https://premierleague.com/news/?utm_source=x"),
            candidate("https://premierleague.com/news"),
        ])
    }))

    state = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    same_execution_candidates = session.scalars(select(ResearchSourceCandidate)).all()

    assert state["candidate_count"] == 1
    assert len(same_execution_candidates) == 1
    assert session.scalar(select(func.count()).select_from(ResearchLink)) == 1
    link = session.scalar(select(ResearchLink))
    assert link.status == ResearchLinkStatus.COLLECTED
    assert session.scalar(select(func.count()).select_from(ResearchEvidence)) == 0


def test_duplicate_candidate_preserves_broad_and_targeted_phase_provenance(session):
    svc = service(discovery=FakeDiscovery(outputs={
        "broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/news/?utm_source=x")], known_gaps=["availability"]),
        "targeted": DiscoveryOutput(candidates=[candidate("https://premierleague.com/news")]),
    }))

    state = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())

    assert state["candidate_count"] == 1
    candidate_row = session.scalar(select(ResearchSourceCandidate))
    assert candidate_row.discovery_phase == "broad"
    assert candidate_row.provenance["phases"] == ["broad", "targeted"]
    assert state["candidates"][0]["discovery_phases"] == ["broad", "targeted"]
    link = session.scalar(select(ResearchLink))
    assert link.discovery_metadata["discovery_phases"] == ["broad", "targeted"]
    assert session.scalar(select(func.count()).select_from(ResearchLink)) == 1


def test_targeted_discovery_failure_leaves_partial_run_state(session):
    discovery = FakeDiscovery(outputs={"broad": DiscoveryOutput(candidates=[candidate()])}, error_phases={"targeted"})
    state = service(discovery=discovery).start_player_discovery(session, player_id=7, research_cutoff=cutoff(), known_missing_dimensions=["minutes"])

    assert state["status"] == "partial"
    assert "targeted unavailable" in state["failure_reason"]
    assert state["candidate_count"] == 1


def test_selected_source_research_preserves_link_result_cutoff_and_failure_isolation(session):
    discovery = FakeDiscovery(outputs={"broad": DiscoveryOutput(candidates=[candidate(), candidate("https://premierleague.com/blocked")])})
    retriever = FakeRetriever(failures={"https://premierleague.com/blocked"})
    page = FakePageResearch()
    svc = service(discovery=discovery, retriever=retriever, page=page)
    state = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    links = {link.original_url: link for link in session.scalars(select(ResearchLink))}

    good = svc.research_link(session, link_id=links["https://premierleague.com/news/saka"].id, player_resolver=resolver(), research_cutoff=cutoff())
    bad = svc.research_link(session, link_id=links["https://premierleague.com/blocked"].id, player_resolver=resolver(), research_cutoff=cutoff())

    assert good["researched"] == 1 and bad["failed"] == 1
    result = session.get(type(session.scalar(select(ResearchLink).where(ResearchLink.original_url == "https://premierleague.com/news/saka")).results[0]), good["result_ids"][0])
    assert result.research_cutoff.replace(tzinfo=timezone.utc) == cutoff()
    assert result.prompt_version == "eval2_page_research_v1"
    assert "Target dimensions" in page.calls[0]["prompt"]
    assert session.get(ResearchLink, links["https://premierleague.com/blocked"].id).failure_reason == "blocked"
    assert svc.thread_execution_state(session, state["research_thread_id"])["links"]


def test_initial_page_failure_is_request_scoped_and_visible_in_execution_link_state(session):
    discovery = FakeDiscovery(outputs={"broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/initial-failure")])})
    retriever = FakeRetriever(failures={"https://premierleague.com/initial-failure"})
    svc = service(discovery=discovery, retriever=retriever)
    state = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    link = session.scalar(select(ResearchLink))

    failed = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=cutoff())
    execution = svc.execution_state(session, state["id"])
    attempt = session.scalar(select(ResearchPageResearchAttempt))

    assert failed["failed"] == 1
    assert attempt.status == ResearchPageResearchAttemptStatus.FAILED
    assert attempt.failure_reason == "blocked"
    assert execution["status"] == "partial"
    assert execution["links"][0]["status"] == ResearchLinkStatus.FAILED
    assert execution["links"][0]["failure_reason"] == "blocked"
    assert execution["links"][0]["result_ids"] == []


def test_failed_cutoff_does_not_block_distinct_cutoff_page_research(session):
    discovery = FakeDiscovery(outputs={
        "broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/cutoff-isolated")]),
        "targeted": DiscoveryOutput(candidates=[]),
    })
    retriever = FakeRetriever(failures={"https://premierleague.com/cutoff-isolated"})
    svc = service(discovery=discovery, retriever=retriever)
    first = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    link = session.scalar(select(ResearchLink))
    first_failed = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=cutoff())

    second_cutoff = cutoff() + timedelta(hours=1)
    second = svc.discover_for_thread(session, thread_id=first["research_thread_id"], player_id=7, research_cutoff=second_cutoff)
    retriever.failures.clear()
    second_researched = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=second_cutoff)
    state = svc.thread_execution_state(session, first["research_thread_id"])
    by_id = {execution["id"]: execution for execution in state["executions"]}

    assert first_failed["failed"] == 1
    assert second_researched["researched"] == 1
    assert by_id[first["id"]]["links"][0]["status"] == ResearchLinkStatus.FAILED
    assert by_id[first["id"]]["links"][0]["result_ids"] == []
    assert by_id[second["id"]]["links"][0]["status"] == ResearchLinkStatus.RESEARCHED
    assert by_id[second["id"]]["links"][0]["result_ids"] == second_researched["result_ids"]


def test_failed_cutoff_is_not_visible_to_distinct_cutoff_before_research(session):
    discovery = FakeDiscovery(outputs={
        "broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/pre-research-isolated")]),
        "targeted": DiscoveryOutput(candidates=[]),
    })
    retriever = FakeRetriever(failures={"https://premierleague.com/pre-research-isolated"})
    svc = service(discovery=discovery, retriever=retriever)
    first = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    link = session.scalar(select(ResearchLink))
    svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=cutoff())

    second_cutoff = cutoff() + timedelta(hours=1)
    second = svc.discover_for_thread(session, thread_id=first["research_thread_id"], player_id=7, research_cutoff=second_cutoff)
    state = svc.thread_execution_state(session, first["research_thread_id"])
    by_id = {execution["id"]: execution for execution in state["executions"]}

    assert by_id[first["id"]]["links"][0]["status"] == ResearchLinkStatus.FAILED
    assert by_id[first["id"]]["links"][0]["failure_reason"] == "blocked"
    assert by_id[second["id"]]["links"][0]["status"] == ResearchLinkStatus.COLLECTED
    assert by_id[second["id"]]["links"][0]["failure_reason"] is None
    assert by_id[second["id"]]["links"][0]["result_ids"] == []
    assert by_id[second["id"]]["status"] == "partial"


def test_atomic_extraction_persists_provenance_taxonomy_times_cluster_and_relationships(session):
    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/saka", player_ids=[7])
    result = persistence.persist_result(session, link=link, summary="Summary", findings="Findings", evidence="Evidence", player_ids=[7], research_cutoff=cutoff())
    situation = ResearchSituationService().create_situation(session, title="Role", context="Context", fpl_relevance="FPL", player_ids=[7])
    hypothesis = ResearchSituationService().add_hypothesis(session, situation.id, statement="Saka starts")
    evidence_service = ResearchEvidenceService()
    cluster = evidence_service.create_cluster(session, research_thread_id=thread.id, narrative="Primary manager report")
    evidence_service.attach_cluster_link(session, cluster_id=cluster.id, research_link_id=link.id, lineage_type="derivative")
    output = EvidenceExtractionOutput(
        evidence=[
            AtomicEvidencePayload(
                claim="Manager said Saka trained fully.",
                claim_type="availability",
                evidence_type=ResearchEvidenceType.REPORT,
                player_ids=[7],
                published_at=cutoff() - timedelta(hours=1),
                observed_at=None,
                retrieved_at=cutoff(),
                season="2026/27",
                reliability="high",
                relevance="high",
                is_volatile=True,
            ),
            AtomicEvidencePayload(
                claim="The report infers Saka is likely to start.",
                claim_type="starting_status",
                evidence_type=ResearchEvidenceType.INFERENCE,
                player_ids=[7, 8],
                reliability="medium",
                relevance="high",
            ),
        ],
        relationships=[EvidenceRelationPayload(from_index=1, to_index=0, relation_type="supports", rationale="Inference depends on availability report")],
        hypothesis_relationships=[HypothesisRelationPayload(evidence_index=0, hypothesis_id=hypothesis.id, relationship_type="supports", rationale="Manager availability supports the start hypothesis")],
    )
    svc = service(atomic=FakeAtomic(output))

    response = svc.extract_atomic_evidence(session, result_id=result.id, research_cutoff=cutoff(), situation_id=situation.id)
    second = svc.extract_atomic_evidence(session, result_id=result.id, research_cutoff=cutoff(), situation_id=situation.id)

    assert response["created"] == 2
    assert second["created"] == 0 and second["reused"] == 2
    evidence = evidence_service.list_evidence(session, thread_id=thread.id)
    by_claim = {item.claim: item for item in evidence}
    report = by_claim["Manager said Saka trained fully."]
    inference = by_claim["The report infers Saka is likely to start."]
    assert {item.evidence_type for item in evidence} == {ResearchEvidenceType.REPORT, ResearchEvidenceType.INFERENCE}
    assert report.research_link_id == link.id and report.research_result_id == result.id
    assert report.source_cluster_id == cluster.id
    assert report.observed_at is None
    assert report.season == "2026/27"
    assert report.reliability == "high" and report.relevance == "high"
    assert report.is_volatile
    assert {player.id for player in inference.players} == {7, 8}
    assert session.scalar(select(func.count()).select_from(EvidenceRelation)) == 1
    assert len(evidence_service.get_evidence(session, report.id).hypothesis_relations) == 1


def test_extraction_rejects_unknown_player_and_skips_post_cutoff_without_inventing_dates_or_season(session):
    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/saka", player_ids=[7])
    result = persistence.persist_result(session, link=link, summary="Summary", findings="Findings", evidence="Evidence", player_ids=[7])
    post_cutoff = EvidenceExtractionOutput(evidence=[
        AtomicEvidencePayload(
            claim="After cutoff claim.",
            claim_type="availability",
            evidence_type=ResearchEvidenceType.FACT,
            player_ids=[7],
            reliability="high",
            relevance="high",
            published_at=cutoff() + timedelta(minutes=1),
            observed_at=None,
            season=None,
        )
    ])
    skipped = service(atomic=FakeAtomic(post_cutoff)).extract_atomic_evidence(session, result_id=result.id, research_cutoff=cutoff())
    assert skipped["created"] == 0 and skipped["skipped_post_cutoff"] == 1
    assert session.scalar(select(func.count()).select_from(ResearchEvidence)) == 0

    unknown = EvidenceExtractionOutput(evidence=[
        AtomicEvidencePayload(
            claim="Unknown player claim.",
            claim_type="availability",
            evidence_type=ResearchEvidenceType.FACT,
            player_ids=[999],
            reliability="high",
            relevance="high",
        )
    ])
    with pytest.raises(LookupError):
        service(atomic=FakeAtomic(unknown)).extract_atomic_evidence(session, result_id=result.id, research_cutoff=cutoff())


def test_successful_zero_evidence_extraction_completes_execution(session):
    discovery = FakeDiscovery(outputs={"broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/zero")])})
    svc = service(discovery=discovery, atomic=FakeAtomic(EvidenceExtractionOutput()))
    state = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    link = session.scalar(select(ResearchLink))
    researched = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=cutoff())

    extracted = svc.extract_atomic_evidence(session, result_id=researched["result_ids"][0], research_cutoff=cutoff())
    execution = svc.execution_state(session, state["id"])

    assert extracted["created"] == 0
    assert execution["status"] == "complete"
    assert execution["links"][0]["results"][0]["extraction_status"] == "complete"


def test_cutoff_comparisons_normalize_timezone_offsets(session):
    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/saka", player_ids=[7])
    result = persistence.persist_result(session, link=link, summary="Summary", findings="Findings", evidence="Evidence", player_ids=[7], research_cutoff=cutoff())
    output = EvidenceExtractionOutput(evidence=[
        AtomicEvidencePayload(
            claim="Equivalent instant claim.",
            claim_type="availability",
            evidence_type="fact",
            player_ids=[7],
            reliability="high",
            relevance="high",
            published_at=datetime(2026, 8, 15, 17, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        ),
        AtomicEvidencePayload(
            claim="Post cutoff with offset.",
            claim_type="availability",
            evidence_type="fact",
            player_ids=[7],
            reliability="high",
            relevance="high",
            published_at=datetime(2026, 8, 15, 17, 31, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        ),
    ])

    response = service(atomic=FakeAtomic(output)).extract_atomic_evidence(
        session,
        result_id=result.id,
        research_cutoff=datetime(2026, 8, 15, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
    )

    assert response["created"] == 1
    assert response["skipped_post_cutoff"] == 1
    assert session.scalar(select(ResearchEvidence).where(ResearchEvidence.claim == "Equivalent instant claim."))


def test_naive_research_cutoff_is_rejected(session):
    with pytest.raises(ValueError, match="timezone"):
        service().start_player_discovery(session, player_id=7, research_cutoff=datetime(2026, 8, 15, 12))


def test_distinct_observations_are_not_fuzzy_deduplicated(session):
    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/saka", player_ids=[7])
    result = persistence.persist_result(session, link=link, summary="Summary", findings="Findings", evidence="Evidence", player_ids=[7])
    output = EvidenceExtractionOutput(evidence=[
        AtomicEvidencePayload(claim="Saka trained.", claim_type="availability", evidence_type="fact", player_ids=[7], reliability="high", relevance="high", observed_at=cutoff() - timedelta(days=1)),
        AtomicEvidencePayload(claim="Saka trained.", claim_type="availability", evidence_type="fact", player_ids=[7], reliability="high", relevance="high", observed_at=cutoff() - timedelta(days=2)),
    ])

    response = service(atomic=FakeAtomic(output)).extract_atomic_evidence(session, result_id=result.id, research_cutoff=cutoff())

    assert response["created"] == 2
    assert session.scalar(select(func.count()).select_from(ResearchEvidence)) == 2


def test_failed_link_can_be_retried_explicitly_and_execution_state_exposes_evidence(session):
    discovery = FakeDiscovery(outputs={"broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/retry")])})
    retriever = FakeRetriever(failures={"https://premierleague.com/retry"})
    svc = service(discovery=discovery, retriever=retriever)
    state = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    link = session.scalar(select(ResearchLink))

    failed = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=cutoff())
    skipped = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=cutoff())
    retriever.failures.clear()
    retried = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=cutoff(), retry_failed=True)
    result_id = retried["result_ids"][0]
    output = EvidenceExtractionOutput(evidence=[
        AtomicEvidencePayload(claim="Saka trained.", claim_type="availability", evidence_type="fact", player_ids=[7], reliability="high", relevance="high")
    ])
    extracted = service(atomic=FakeAtomic(output)).extract_atomic_evidence(session, result_id=result_id, research_cutoff=cutoff())
    execution = svc.execution_state(session, state["id"])

    assert failed["failed"] == 1
    assert skipped["skipped"] == 1
    assert retried["researched"] == 1
    assert execution["status"] == "complete"
    assert extracted["evidence_ids"][0] in execution["evidence_ids"]
    assert execution["links"][0]["results"][0]["evidence_ids"] == extracted["evidence_ids"]


def test_post_commit_page_research_exception_reports_existing_result_as_skipped(session):
    discovery = FakeDiscovery(outputs={"broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/post-commit")])})
    svc = service(discovery=discovery)
    svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    link = session.scalar(select(ResearchLink))

    def fail_refresh(session, thread_id):
        raise RuntimeError("status refresh unavailable")

    svc._refresh_execution_status_for_thread = fail_refresh
    response = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=cutoff())

    assert response["failed"] == 0
    assert response["skipped"] == 1
    assert response["result_ids"]
    assert session.get(ResearchLink, link.id).status == ResearchLinkStatus.RESEARCHED


def test_same_link_different_cutoff_executions_keep_results_and_status_isolated(session):
    discovery = FakeDiscovery(outputs={
        "broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/shared")]),
        "targeted": DiscoveryOutput(candidates=[]),
    })
    svc = service(discovery=discovery)
    first = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    link = session.scalar(select(ResearchLink).where(ResearchLink.original_url == "https://premierleague.com/shared"))
    first_result = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=cutoff())["result_ids"][0]
    first_output = EvidenceExtractionOutput(evidence=[
        AtomicEvidencePayload(claim="First cutoff claim.", claim_type="availability", evidence_type="fact", player_ids=[7], reliability="high", relevance="high")
    ])
    service(atomic=FakeAtomic(first_output)).extract_atomic_evidence(session, result_id=first_result, research_cutoff=cutoff())

    second_cutoff = cutoff() + timedelta(hours=1)
    second = svc.discover_for_thread(session, thread_id=first["research_thread_id"], player_id=7, research_cutoff=second_cutoff)
    second_result = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=second_cutoff)["result_ids"][0]
    second_output = EvidenceExtractionOutput(evidence=[
        AtomicEvidencePayload(claim="Second cutoff claim.", claim_type="availability", evidence_type="fact", player_ids=[7], reliability="high", relevance="high")
    ])
    service(atomic=FakeAtomic(second_output)).extract_atomic_evidence(session, result_id=second_result, research_cutoff=second_cutoff)

    thread_state = svc.thread_execution_state(session, first["research_thread_id"])
    by_id = {execution["id"]: execution for execution in thread_state["executions"]}

    assert first_result != second_result
    assert by_id[first["id"]]["status"] == "complete"
    assert by_id[second["id"]]["status"] == "complete"
    assert by_id[first["id"]]["links"][0]["result_ids"] == [first_result]
    assert by_id[second["id"]]["links"][0]["result_ids"] == [second_result]
    assert by_id[first["id"]]["evidence_ids"] != by_id[second["id"]]["evidence_ids"]


def test_same_link_new_cutoff_failure_does_not_reuse_old_result_and_retry_isolated(session):
    discovery = FakeDiscovery(outputs={
        "broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/shared-failure")]),
        "targeted": DiscoveryOutput(candidates=[]),
    })
    retriever = FakeRetriever()
    svc = service(discovery=discovery, retriever=retriever, page=ModelPageResearch())
    first = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    link = session.scalar(select(ResearchLink).where(ResearchLink.original_url == "https://premierleague.com/shared-failure"))
    first_result = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=cutoff())["result_ids"][0]
    first_output = EvidenceExtractionOutput(
        evidence=[AtomicEvidencePayload(claim="First cutoff claim.", claim_type="availability", evidence_type="fact", player_ids=[7], reliability="high", relevance="high")],
        model_id="extract-model-a",
    )
    service(atomic=FakeAtomic(first_output)).extract_atomic_evidence(session, result_id=first_result, research_cutoff=cutoff())

    second_cutoff = cutoff() + timedelta(hours=1)
    second = svc.discover_for_thread(session, thread_id=first["research_thread_id"], player_id=7, research_cutoff=second_cutoff)
    retriever.failures.add("https://premierleague.com/shared-failure")
    failed = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=second_cutoff)
    failed_state = svc.thread_execution_state(session, first["research_thread_id"])
    failed_by_id = {execution["id"]: execution for execution in failed_state["executions"]}

    assert failed["failed"] == 1
    assert failed["result_ids"] == []
    assert failed_by_id[first["id"]]["status"] == "complete"
    assert failed_by_id[second["id"]]["status"] == "partial"
    assert "blocked" in failed_by_id[second["id"]]["failure_reason"]
    assert failed_by_id[second["id"]]["links"][0]["status"] == ResearchLinkStatus.FAILED
    assert failed_by_id[second["id"]]["links"][0]["failure_reason"] == "blocked"
    assert failed_by_id[second["id"]]["links"][0]["result_ids"] == []
    assert failed_by_id[first["id"]]["links"][0]["status"] == ResearchLinkStatus.RESEARCHED
    assert failed_by_id[first["id"]]["links"][0]["result_ids"] == [first_result]

    retriever.failures.clear()
    retried = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=second_cutoff, retry_failed=True)
    second_result = retried["result_ids"][0]
    second_output = EvidenceExtractionOutput(
        evidence=[AtomicEvidencePayload(claim="Second cutoff claim.", claim_type="availability", evidence_type="fact", player_ids=[7], reliability="high", relevance="high")],
        model_id="extract-model-b",
    )
    service(atomic=FakeAtomic(second_output)).extract_atomic_evidence(session, result_id=second_result, research_cutoff=second_cutoff)
    final_state = svc.thread_execution_state(session, first["research_thread_id"])
    final_by_id = {execution["id"]: execution for execution in final_state["executions"]}

    assert retried["researched"] == 1
    assert second_result != first_result
    assert final_by_id[first["id"]]["links"][0]["result_ids"] == [first_result]
    assert final_by_id[second["id"]]["links"][0]["result_ids"] == [second_result]
    assert final_by_id[second["id"]]["links"][0]["status"] == ResearchLinkStatus.RESEARCHED
    assert final_by_id[second["id"]]["links"][0]["failure_reason"] is None
    assert final_by_id[first["id"]]["links"][0]["results"][0]["page_research_model_id"] == "page-model"
    assert final_by_id[first["id"]]["links"][0]["results"][0]["extraction_model_id"] == "extract-model-a"
    assert final_by_id[second["id"]]["links"][0]["results"][0]["extraction_model_id"] == "extract-model-b"


def test_research_result_equivalent_request_has_unique_index(session):
    indexes = inspect(session.get_bind()).get_indexes("research_results")

    assert any(
        index["name"] == "uq_research_results_link_prompt_cutoff"
        and index.get("unique")
        and index["column_names"] == ["research_link_id", "prompt_version", "research_cutoff"]
        for index in indexes
    )


def test_direct_link_new_cutoff_failure_persists_attempt_without_erasing_historical_result(session):
    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/direct-failure", player_ids=[7])
    historical = persistence.persist_result(
        session,
        link=link,
        summary="Summary",
        findings="Findings",
        evidence="Evidence",
        player_ids=[7],
        prompt_version="eval2_page_research_v1",
        research_cutoff=cutoff(),
    )
    retriever = FakeRetriever(failures={"https://premierleague.com/direct-failure"})
    svc = service(retriever=retriever, page=ModelPageResearch())
    newer_cutoff = cutoff() + timedelta(hours=1)

    failed = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=newer_cutoff)

    attempts = list(session.scalars(select(ResearchPageResearchAttempt)))
    assert failed["failed"] == 1
    assert failed["result_ids"] == []
    assert session.get(ResearchResult, historical.id) is not None
    assert session.get(ResearchLink, link.id).status == ResearchLinkStatus.RESEARCHED
    assert len(attempts) == 1
    assert attempts[0].status == ResearchPageResearchAttemptStatus.FAILED
    assert attempts[0].failure_reason == "blocked"
    assert attempts[0].research_result_id is None

    retriever.failures.clear()
    retried = svc.research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=newer_cutoff, retry_failed=True)
    retry_attempts = list(session.scalars(select(ResearchPageResearchAttempt).order_by(ResearchPageResearchAttempt.research_cutoff)))

    assert retried["researched"] == 1
    assert retried["result_ids"] != [historical.id]
    assert session.get(ResearchResult, historical.id) is not None
    assert len(retry_attempts) == 1
    assert retry_attempts[0].status == ResearchPageResearchAttemptStatus.RESEARCHED
    assert retry_attempts[0].failure_reason is None
    assert retry_attempts[0].research_result_id == retried["result_ids"][0]
    assert retry_attempts[0].page_research_model_id == "page-model"


def test_naive_sqlite_cutoff_matches_equivalent_aware_utc_cutoff(session):
    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/naive-cutoff", player_ids=[7])
    aware_cutoff = cutoff()
    result = persistence.persist_result(
        session,
        link=link,
        summary="Summary",
        findings="Findings",
        evidence="Evidence",
        player_ids=[7],
        prompt_version="eval2_page_research_v1",
        research_cutoff=aware_cutoff,
    )
    result.research_cutoff = aware_cutoff.replace(tzinfo=None)
    session.commit()
    session.expire_all()

    skipped = service().research_link(session, link_id=link.id, player_resolver=resolver(), research_cutoff=aware_cutoff)

    assert skipped["skipped"] == 1
    assert skipped["result_ids"] == [result.id]


def test_equivalent_page_result_unique_conflict_recovers_existing_result(session):
    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/unique-result", player_ids=[7])
    original = persistence.persist_result(
        session,
        link=link,
        summary="Summary",
        findings="Findings",
        evidence="Evidence",
        player_ids=[7],
        prompt_version="eval2_page_research_v1",
        research_cutoff=cutoff(),
    )
    other_cutoff = persistence.persist_result(
        session,
        link=link,
        summary="Other cutoff",
        findings="Other cutoff",
        evidence="Other cutoff",
        player_ids=[7],
        prompt_version="eval2_page_research_v1",
        research_cutoff=cutoff() + timedelta(hours=1),
    )
    real_lookup = persistence.repository.get_result_by_link_prompt_cutoff
    calls = {"count": 0}

    def miss_once(session, *, research_link_id, prompt_version, research_cutoff):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return real_lookup(
            session,
            research_link_id=research_link_id,
            prompt_version=prompt_version,
            research_cutoff=research_cutoff,
        )

    persistence.repository.get_result_by_link_prompt_cutoff = miss_once

    recovered = persistence.persist_result(
        session,
        link=link,
        summary="Duplicate",
        findings="Duplicate",
        evidence="Duplicate",
        player_ids=[7],
        prompt_version="eval2_page_research_v1",
        research_cutoff=cutoff(),
    )

    assert recovered.id == original.id
    assert recovered.id != other_cutoff.id
    assert session.scalar(select(func.count()).select_from(ResearchResult)) == 2


def test_execution_state_scopes_links_results_and_evidence_to_each_execution(session):
    discovery = FakeDiscovery(outputs={
        "broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/one")]),
        "targeted": DiscoveryOutput(candidates=[]),
    })
    svc = service(discovery=discovery)
    first = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    first_link = session.scalar(select(ResearchLink).where(ResearchLink.original_url == "https://premierleague.com/one"))
    first_result = svc.research_link(session, link_id=first_link.id, player_resolver=resolver(), research_cutoff=cutoff())["result_ids"][0]
    first_evidence = EvidenceExtractionOutput(evidence=[
        AtomicEvidencePayload(claim="First execution claim.", claim_type="availability", evidence_type="fact", player_ids=[7], reliability="high", relevance="high")
    ])
    service(atomic=FakeAtomic(first_evidence)).extract_atomic_evidence(session, result_id=first_result, research_cutoff=cutoff())

    discovery.outputs = {"broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/two")]), "targeted": DiscoveryOutput(candidates=[])}
    second = svc.discover_for_thread(session, thread_id=first["research_thread_id"], player_id=7, research_cutoff=cutoff())
    second_link = session.scalar(select(ResearchLink).where(ResearchLink.original_url == "https://premierleague.com/two"))
    second_result = svc.research_link(session, link_id=second_link.id, player_resolver=resolver(), research_cutoff=cutoff())["result_ids"][0]
    second_evidence = EvidenceExtractionOutput(evidence=[
        AtomicEvidencePayload(claim="Second execution claim.", claim_type="availability", evidence_type="fact", player_ids=[7], reliability="high", relevance="high")
    ])
    service(atomic=FakeAtomic(second_evidence)).extract_atomic_evidence(session, result_id=second_result, research_cutoff=cutoff())

    thread_state = svc.thread_execution_state(session, first["research_thread_id"])
    by_id = {execution["id"]: execution for execution in thread_state["executions"]}

    assert [link["url"] for link in by_id[first["id"]]["links"]] == ["https://premierleague.com/one"]
    assert [link["url"] for link in by_id[second["id"]]["links"]] == ["https://premierleague.com/two"]
    assert len(by_id[first["id"]]["evidence_ids"]) == 1
    assert len(by_id[second["id"]]["evidence_ids"]) == 1


def test_cutoff_mismatch_rejects_extraction_and_records_failure(session):
    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/saka", player_ids=[7])
    result = persistence.persist_result(session, link=link, summary="Summary", findings="Findings", evidence="Evidence", player_ids=[7], research_cutoff=cutoff())

    with pytest.raises(ValueError, match="must match"):
        service().extract_atomic_evidence(session, result_id=result.id, research_cutoff=cutoff() + timedelta(hours=1))

    refreshed = session.get(type(result), result.id)
    assert (refreshed.source_metadata or {}).get("extraction_failure") is None


def test_extraction_failure_is_transactional_and_persisted_on_result(session):
    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/saka", player_ids=[7])
    result = persistence.persist_result(session, link=link, summary="Summary", findings="Findings", evidence="Evidence", player_ids=[7], research_cutoff=cutoff())
    output = EvidenceExtractionOutput(
        evidence=[
            AtomicEvidencePayload(claim="Saka trained.", claim_type="availability", evidence_type="fact", player_ids=[7], reliability="high", relevance="high"),
            AtomicEvidencePayload(claim="Bad claim.", claim_type="not_a_type", evidence_type="fact", player_ids=[7], reliability="high", relevance="high"),
        ]
    )

    with pytest.raises(ValueError, match="Unknown claim type"):
        service(atomic=FakeAtomic(output)).extract_atomic_evidence(session, result_id=result.id, research_cutoff=cutoff())

    assert session.scalar(select(func.count()).select_from(ResearchEvidence)) == 0
    refreshed = session.get(type(result), result.id)
    assert (refreshed.source_metadata or {})["extraction_status"] == "failed"
    assert "Unknown claim type" in (refreshed.source_metadata or {})["extraction_failure"]


def test_ambiguous_relationship_is_ignored_but_contradiction_and_supersession_are_preserved(session):
    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/saka", player_ids=[7])
    result = persistence.persist_result(session, link=link, summary="Summary", findings="Findings", evidence="Evidence", player_ids=[7], research_cutoff=cutoff())
    output = EvidenceExtractionOutput(
        evidence=[
            AtomicEvidencePayload(claim="Saka is expected to start.", claim_type="starting_status", evidence_type="report", player_ids=[7], reliability="high", relevance="high"),
            AtomicEvidencePayload(claim="Saka is expected to be benched.", claim_type="starting_status", evidence_type="report", player_ids=[7], reliability="medium", relevance="high"),
            AtomicEvidencePayload(claim="Newer report says Saka starts.", claim_type="starting_status", evidence_type="report", player_ids=[7], reliability="high", relevance="high"),
        ],
        relationships=[
            EvidenceRelationPayload(from_index=1, to_index=0, relation_type="contradicts", rationale="Bench expectation conflicts with start expectation"),
            EvidenceRelationPayload(from_index=2, to_index=0, relation_type="supersedes", rationale="Report explicitly says it is a newer update"),
            EvidenceRelationPayload(from_index=2, to_index=1, relation_type="supports"),
        ],
    )

    response = service(atomic=FakeAtomic(output)).extract_atomic_evidence(session, result_id=result.id, research_cutoff=cutoff())

    assert response["created"] == 3
    assert {item["relation_type"] for item in response["relations"]} == {"contradicts", "supersedes"}
    assert session.scalar(select(func.count()).select_from(EvidenceRelation)) == 2
    assert session.scalar(select(func.count()).select_from(ResearchEvidence)) == 3


def test_relationship_to_other_thread_evidence_is_rejected_transactionally(session):
    persistence = ResearchPersistenceService()
    first_thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    first_link = persistence.add_collected_link(session, thread_id=first_thread.id, url="https://premierleague.com/saka", player_ids=[7])
    first_result = persistence.persist_result(session, link=first_link, summary="Summary", findings="Findings", evidence="Evidence", player_ids=[7], research_cutoff=cutoff())
    other_thread = persistence.create_thread(session, title="Other", thread_type=ResearchThreadType.PLAYER)
    existing = ResearchEvidenceService().create_evidence(
        session,
        research_thread_id=other_thread.id,
        claim="Other thread claim.",
        claim_type="availability",
        evidence_type="fact",
        reliability="high",
        relevance="high",
        player_ids=[7],
    )
    output = EvidenceExtractionOutput(
        evidence=[AtomicEvidencePayload(claim="Saka trained.", claim_type="availability", evidence_type="fact", player_ids=[7], reliability="high", relevance="high")],
        relationships=[EvidenceRelationPayload(from_index=0, to_evidence_id=existing.id, relation_type="supports", rationale="Would cross threads")],
    )

    with pytest.raises(ValueError, match="must belong to the result thread"):
        service(atomic=FakeAtomic(output)).extract_atomic_evidence(session, result_id=first_result.id, research_cutoff=cutoff())

    assert session.scalar(select(func.count()).select_from(ResearchEvidence)) == 1
    assert session.scalar(select(func.count()).select_from(EvidenceRelation)) == 0


def test_codex_adapters_preserve_model_ids_and_reject_malformed_structured_entries(session):
    discovery_payload = {
        "candidates": [{
            "url": "https://premierleague.com/news/saka",
            "source": "Premier League",
            "target_dimensions": ["availability"],
            "usefulness": "Primary team news",
            "source_category": "official_primary",
            "expected_relevance": "high",
            "lineage_type": "original",
        }],
        "known_gaps": ["penalties"],
    }
    discovery = CodexEval2DiscoveryProvider(FakeCodex(json.dumps(discovery_payload), model="disc-model"))

    output = discovery.discover(prompt="discover", prompt_version="eval2_source_discovery_v1", phase="broad")

    assert output.model_id == "disc-model"
    assert output.candidates[0].url == "https://premierleague.com/news/saka"
    with pytest.raises(ValueError, match="candidate entry"):
        CodexEval2DiscoveryProvider(FakeCodex(json.dumps({"candidates": ["bad"], "known_gaps": []}))).discover(prompt="discover", prompt_version="v", phase="broad")
    malformed_candidate = {**discovery_payload["candidates"][0], "target_dimensions": "availability"}
    with pytest.raises(ValueError, match="target_dimensions"):
        CodexEval2DiscoveryProvider(FakeCodex(json.dumps({"candidates": [malformed_candidate], "known_gaps": []}))).discover(prompt="discover", prompt_version="v", phase="broad")
    with pytest.raises(ValueError, match="known_gaps entry"):
        CodexEval2DiscoveryProvider(FakeCodex(json.dumps({"candidates": [], "known_gaps": [{"bad": "gap"}]}))).discover(prompt="discover", prompt_version="v", phase="broad")

    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Saka", thread_type=ResearchThreadType.PLAYER)
    link = persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/saka", player_ids=[7])
    page_payload = {"summary": "Summary", "findings": "Findings", "evidence": "Evidence", "uncertainties": None, "referenced_players": ["Saka"]}
    page = CodexEval2PageResearchProvider(FakeCodex(json.dumps(page_payload), model="page-model"))

    extraction = page.extract(prompt="page", prompt_version="eval2_page_research_v1", thread=thread, link=link, page_content="content")

    assert extraction.summary == "Summary"
    assert link.discovery_metadata["page_research_model_id"] == "page-model"
    with pytest.raises(ValueError, match="referenced_players"):
        CodexEval2PageResearchProvider(FakeCodex(json.dumps({**page_payload, "referenced_players": "Saka"}))).extract(prompt="page", prompt_version="v", thread=thread, link=link, page_content="content")

    result = persistence.persist_result(session, link=link, summary="Summary", findings="Findings", evidence="Evidence", player_ids=[7], research_cutoff=cutoff())
    atomic_payload = {"evidence": [], "relationships": [], "hypothesis_relationships": []}
    atomic = CodexEval2AtomicEvidenceProvider(FakeCodex(json.dumps(atomic_payload), model="extract-model"))

    atomic_output = atomic.extract(prompt="atomic", prompt_version="eval2_atomic_evidence_extraction_v1", result=result)

    assert atomic_output.model_id == "extract-model"
    with pytest.raises(ValueError, match="evidence entry"):
        CodexEval2AtomicEvidenceProvider(FakeCodex(json.dumps({"evidence": ["bad"], "relationships": [], "hypothesis_relationships": []}))).extract(prompt="atomic", prompt_version="v", result=result)
    malformed_atomic = {
        "claim": "Saka trained.",
        "claim_type": "availability",
        "evidence_type": "fact",
        "player_ids": ["7"],
        "reliability": "high",
        "relevance": "high",
    }
    with pytest.raises(ValueError, match="player_ids entry"):
        CodexEval2AtomicEvidenceProvider(FakeCodex(json.dumps({"evidence": [malformed_atomic], "relationships": [], "hypothesis_relationships": []}))).extract(prompt="atomic", prompt_version="v", result=result)
    malformed_time = {**malformed_atomic, "player_ids": [7], "published_at": 123}
    with pytest.raises(ValueError, match="datetime"):
        CodexEval2AtomicEvidenceProvider(FakeCodex(json.dumps({"evidence": [malformed_time], "relationships": [], "hypothesis_relationships": []}))).extract(prompt="atomic", prompt_version="v", result=result)


def test_atomic_prompt_forbids_topical_similarity_and_auto_supersession():
    from fpl_intelligence.research.eval2_prompts import atomic_extraction_prompt

    prompt = atomic_extraction_prompt(
        player_payload={"id": 7},
        result_payload={"id": "result"},
        research_cutoff=cutoff().isoformat(),
        durable_context={},
    ).prompt

    assert "Do not infer hypothesis support or contradiction merely from topical similarity" in prompt
    assert "do not automatically label newer claims as superseding older claims" in prompt
    assert "If the relationship is unclear, leave it unresolved" in prompt


def test_thread_execution_state_uses_bounded_queries(session):
    discovery = FakeDiscovery(outputs={
        "broad": DiscoveryOutput(candidates=[candidate("https://premierleague.com/one")]),
        "targeted": DiscoveryOutput(candidates=[candidate("https://premierleague.com/two")]),
    })
    svc = service(discovery=discovery)
    first = svc.start_player_discovery(session, player_id=7, research_cutoff=cutoff())
    svc.discover_for_thread(session, thread_id=first["research_thread_id"], player_id=7, research_cutoff=cutoff())
    statements = []

    def capture(_, __, statement, ___, ____, _____):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        state = svc.thread_execution_state(session, first["research_thread_id"])
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert len(state["executions"]) == 2
    assert sum("research_discovery_executions" in statement.lower() for statement in statements) == 1
