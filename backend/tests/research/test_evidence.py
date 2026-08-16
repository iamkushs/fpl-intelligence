from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from fpl_intelligence.db.base import Base
from fpl_intelligence.models import Player, ResearchEvidenceType, ResearchThreadType
from fpl_intelligence.research.evidence import ResearchEvidenceService
from fpl_intelligence.research.persistence import ResearchPersistenceService
from fpl_intelligence.research.situations import ResearchSituationService
from fpl_intelligence.watchlist.service import WatchlistService


@pytest.fixture
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        value.add_all([Player(id=1), Player(id=2), Player(id=3)])
        value.commit()
        yield value
    engine.dispose()


def context(session):
    persistence = ResearchPersistenceService()
    thread = persistence.create_thread(session, title="Evidence research", thread_type=ResearchThreadType.PLAYER)
    link_one = persistence.add_collected_link(session, thread_id=thread.id, url="https://example.com/one")
    link_two = persistence.add_collected_link(session, thread_id=thread.id, url="https://example.com/two")
    link_three = persistence.add_collected_link(session, thread_id=thread.id, url="https://example.com/three")
    result = persistence.persist_result(session, link=link_one, summary="Summary", findings="Findings", evidence="Basis", player_ids=[])
    situation = ResearchSituationService().create_situation(session, title="Role", context="Context", fpl_relevance="FPL", player_ids=[1])
    hypothesis = ResearchSituationService().add_hypothesis(session, situation.id, statement="Starts")
    return thread, link_one, link_two, link_three, result, situation, hypothesis


def create(service, session, thread, **overrides):
    payload = dict(research_thread_id=thread.id, claim="Player started wide.", claim_type="starting_status", evidence_type="fact", reliability="high", relevance="high")
    payload.update(overrides)
    return service.create_evidence(session, **payload)


def test_atomic_evidence_validates_taxonomy_time_provenance_and_players(session):
    service = ResearchEvidenceService()
    thread, link, _, _, result, situation, _ = context(session)
    published = datetime(2026, 8, 14, tzinfo=timezone.utc)
    observed = datetime(2026, 8, 12, tzinfo=timezone.utc)
    retrieved = datetime(2026, 8, 15, tzinfo=timezone.utc)
    evidence = create(service, session, thread, research_situation_id=situation.id, research_link_id=link.id,
                      research_result_id=result.id, player_ids=[1, 2, 2], published_at=published, observed_at=observed,
                      retrieved_at=retrieved, season="2026/27")
    assert evidence.research_situation_id == situation.id
    assert {player.id for player in evidence.players} == {1, 2}
    # SQLite drops timezone offsets, but must preserve the three independently supplied moments.
    assert [item.date() for item in (evidence.published_at, evidence.observed_at, evidence.retrieved_at)] == [
        published.date(), observed.date(), retrieved.date()
    ]
    assert evidence.season == "2026/27" and evidence.is_volatile
    assert create(service, session, thread, claim="No situation", claim_type="performance", evidence_type="inference", reliability="low", relevance="medium").research_situation_id is None
    for evidence_type in (ResearchEvidenceType.FACT, ResearchEvidenceType.STATISTIC, ResearchEvidenceType.REPORT,
                          ResearchEvidenceType.SUPPORTER_OBSERVATION, ResearchEvidenceType.SPECULATION, ResearchEvidenceType.INFERENCE):
        assert create(service, session, thread, evidence_type=evidence_type, claim=f"{evidence_type} claim").evidence_type == evidence_type
    with pytest.raises(ValueError): create(service, session, thread, evidence_type="unknown")
    with pytest.raises(ValueError): create(service, session, thread, reliability="certain")
    with pytest.raises(ValueError): create(service, session, thread, relevance="none")
    with pytest.raises(LookupError): create(service, session, thread, player_ids=[999])
    with pytest.raises(LookupError): create(service, session, thread, research_situation_id="missing")
    with pytest.raises(LookupError): create(service, session, thread, research_link_id="missing")
    with pytest.raises(LookupError): create(service, session, thread, research_result_id="missing")
    WatchlistService().add(session, 1)
    WatchlistService().remove(session, 1, reason="Reviewed")
    assert service.list_evidence(session, player_id=1)[0].id == evidence.id


def test_relations_are_directional_idempotent_and_preserve_history(session):
    service = ResearchEvidenceService()
    thread, _, _, _, _, situation, hypothesis = context(session)
    first = create(service, session, thread, research_situation_id=situation.id)
    later = create(service, session, thread, claim="Player took the penalty.", claim_type="penalties")
    supports = service.add_hypothesis_relation(session, evidence_id=first.id, hypothesis_id=hypothesis.id, relationship_type="supports")
    assert service.add_hypothesis_relation(session, evidence_id=first.id, hypothesis_id=hypothesis.id, relationship_type="supports").id == supports.id
    assert service.add_hypothesis_relation(session, evidence_id=first.id, hypothesis_id=hypothesis.id, relationship_type="contradicts").relationship_type == "contradicts"
    for relation_type in ("supports", "contradicts", "supersedes"):
        assert service.add_evidence_relation(session, from_evidence_id=later.id, to_evidence_id=first.id, relation_type=relation_type).relation_type == relation_type
    assert service.add_evidence_relation(session, from_evidence_id=later.id, to_evidence_id=first.id, relation_type="supersedes").to_evidence_id == first.id
    assert service.get_evidence(session, first.id).claim == "Player started wide."
    with pytest.raises(ValueError): service.add_evidence_relation(session, from_evidence_id=first.id, to_evidence_id=first.id, relation_type="supports")
    with pytest.raises(ValueError): service.add_hypothesis_relation(session, evidence_id=later.id, hypothesis_id=hypothesis.id, relationship_type="other")


def test_source_clusters_derive_independent_confirmation_from_lineage(session):
    service = ResearchEvidenceService()
    thread, original, derivative, independent, _, situation, _ = context(session)
    cluster = service.create_cluster(session, research_thread_id=thread.id, research_situation_id=situation.id, narrative="Original report")
    cluster = service.attach_cluster_link(session, cluster_id=cluster.id, research_link_id=original.id, lineage_type="original")
    assert cluster.likely_original_research_link_id == original.id and service.independent_confirmation_count(cluster) == 1
    cluster = service.attach_cluster_link(session, cluster_id=cluster.id, research_link_id=derivative.id, lineage_type="derivative")
    assert service.independent_confirmation_count(cluster) == 1
    cluster = service.attach_cluster_link(session, cluster_id=cluster.id, research_link_id=independent.id, lineage_type="independent")
    assert service.independent_confirmation_count(cluster) == 2
    assert len(service.attach_cluster_link(session, cluster_id=cluster.id, research_link_id=independent.id, lineage_type="independent").memberships) == 3
    second = service.create_cluster(session, research_thread_id=thread.id, narrative="Other narrative")
    assert len(service.attach_cluster_link(session, cluster_id=second.id, research_link_id=original.id, lineage_type="independent").memberships) == 1
    evidence = create(service, session, thread, source_cluster_id=cluster.id, research_situation_id=situation.id, research_link_id=original.id)
    assert service.get_evidence(session, evidence.id).source_cluster_id == cluster.id
    service.remove_cluster_link(session, cluster_id=cluster.id, research_link_id=derivative.id)
    assert len(service.get_cluster(session, cluster.id).memberships) == 2
    service.remove_cluster_link(session, cluster_id=cluster.id, research_link_id=original.id)
    assert service.get_cluster(session, cluster.id).likely_original_research_link_id is None


def test_collection_relation_traversal_uses_one_relation_query(session):
    service = ResearchEvidenceService()
    thread, *_ = context(session)
    evidence = [create(service, session, thread, claim=f"Observation {index}") for index in range(3)]
    service.add_evidence_relation(session, from_evidence_id=evidence[1].id, to_evidence_id=evidence[0].id, relation_type="supports")
    service.add_evidence_relation(session, from_evidence_id=evidence[2].id, to_evidence_id=evidence[1].id, relation_type="contradicts")
    statements = []

    def capture(_, __, statement, ___, ____, _____):
        if "evidence_relations" in statement.lower():
            statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        relations = service.relations_for_many(session, [item.id for item in evidence])
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert {item.relation_type for item in relations[evidence[1].id]} == {"supports", "contradicts"}
