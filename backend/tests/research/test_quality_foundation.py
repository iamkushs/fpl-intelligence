from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from fpl_intelligence.db.base import Base
from fpl_intelligence.models import (
    Player,
    ResearchEvidence,
    ResearchEvidenceType,
    ResearchLink,
    ResearchQualityStage,
    ResearchQualityStatus,
    ResearchSituation,
    ResearchThread,
    ResearchThreadType,
    research_quality_run_evidence,
    research_quality_run_links,
)
from fpl_intelligence.research.eval2_prompts import (
    EVAL2_COUNTER_SEARCH_PROMPT_VERSION,
    EVAL2_FRESHNESS_PROMPT_VERSION,
    EVAL2_REDDIT_RESEARCH_PROMPT_VERSION,
    blind_spot_prompt,
    counter_search_prompt,
    deep_player_research_prompt,
    evidence_bundle_assessment_prompt,
    final_player_synthesis_prompt,
    freshness_prompt,
    reddit_research_prompt,
)
from fpl_intelligence.research.quality import ResearchQualityService


@pytest.fixture
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        value.add(Player(id=7))
        value.commit()
        yield value
    engine.dispose()


def cutoff():
    return datetime(2026, 8, 15, 12, tzinfo=timezone.utc)


def thread(session):
    item = ResearchThread(title="Saka", thread_type=ResearchThreadType.PLAYER)
    session.add(item)
    session.commit()
    return item


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


def link(session, thread_id):
    item = ResearchLink(
        research_thread_id=thread_id,
        original_url="https://example.com/source",
        canonical_url="https://example.com/source",
        domain="example.com",
    )
    session.add(item)
    session.commit()
    return item


def test_reddit_run_situation_optional_and_sets_stage_prompt(session):
    run = ResearchQualityService().start_reddit_run(session, thread_id=thread(session).id, player_id=7, research_cutoff=cutoff())

    assert run.situation_id is None
    assert run.stage == ResearchQualityStage.REDDIT
    assert run.status == ResearchQualityStatus.RUNNING
    assert run.prompt_version == EVAL2_REDDIT_RESEARCH_PROMPT_VERSION


def test_counter_search_requires_challenged_claim_and_persists_valid_outcome(session):
    service = ResearchQualityService()
    item_thread = thread(session)

    with pytest.raises(ValueError):
        service.start_counter_search_run(session, thread_id=item_thread.id, player_id=7, challenged_claim="", research_cutoff=cutoff())

    run = service.start_counter_search_run(session, thread_id=item_thread.id, player_id=7, challenged_claim="Saka is nailed.", research_cutoff=cutoff())
    completed = service.complete_counter_search_run(session, run_id=run.id, outcome="qualified")

    assert completed.outcome == "qualified"
    assert completed.completed_at is not None


def test_freshness_requires_target_and_superseded_requires_superseding_without_deleting_original(session):
    service = ResearchQualityService()
    item_thread = thread(session)
    original = evidence(session, item_thread.id)
    newer = evidence(session, item_thread.id, claim="Saka missed training.")

    with pytest.raises(ValueError):
        service.start_freshness_run(session, thread_id=item_thread.id, player_id=7, target_evidence_id=None, research_cutoff=cutoff())

    run = service.start_freshness_run(session, thread_id=item_thread.id, player_id=7, target_evidence_id=original.id, research_cutoff=cutoff())
    with pytest.raises(ValueError):
        service.complete_freshness_run(session, run_id=run.id, outcome="superseded")
    completed = service.complete_freshness_run(session, run_id=run.id, outcome="superseded", superseding_evidence_id=newer.id)

    assert completed.superseding_evidence_id == newer.id
    assert session.get(ResearchEvidence, original.id) is not None


def test_duplicate_link_and_evidence_attachments_are_idempotent(session):
    service = ResearchQualityService()
    item_thread = thread(session)
    item_link = link(session, item_thread.id)
    item_evidence = evidence(session, item_thread.id)
    run = service.start_reddit_run(session, thread_id=item_thread.id, player_id=7, research_cutoff=cutoff())

    service.complete_reddit_run(session, run_id=run.id, link_ids=[item_link.id, item_link.id], evidence_ids=[item_evidence.id, item_evidence.id])
    service.complete_reddit_run(session, run_id=run.id, link_ids=[item_link.id], evidence_ids=[item_evidence.id])

    assert session.scalar(select(func.count()).select_from(research_quality_run_links)) == 1
    assert session.scalar(select(func.count()).select_from(research_quality_run_evidence)) == 1


def test_invalid_stage_specific_outcome_rejected(session):
    service = ResearchQualityService()
    item_thread = thread(session)
    item_evidence = evidence(session, item_thread.id)
    counter = service.start_counter_search_run(session, thread_id=item_thread.id, player_id=7, challenged_claim="Saka starts.", research_cutoff=cutoff())
    fresh = service.start_freshness_run(session, thread_id=item_thread.id, player_id=7, target_evidence_id=item_evidence.id, research_cutoff=cutoff())

    with pytest.raises(ValueError):
        service.complete_counter_search_run(session, run_id=counter.id, outcome="still_current")
    with pytest.raises(ValueError):
        service.complete_freshness_run(session, run_id=fresh.id, outcome="contradicted")


def test_prompt_contracts_include_quality_rules():
    reddit = reddit_research_prompt(player_payload={"id": 7}, research_cutoff=cutoff().isoformat()).prompt
    counter = counter_search_prompt(challenged_claim="Saka is nailed.", research_cutoff=cutoff().isoformat()).prompt
    freshness = freshness_prompt(evidence_payload={"claim": "Saka trained."}, research_cutoff=cutoff().isoformat()).prompt

    assert "Never use upvotes as reliability" in reddit
    assert "Challenge the current evidence interpretation" in counter
    assert "Do not make a transfer recommendation" in counter
    for outcome in ("still_current", "changed", "unresolved", "superseded"):
        assert outcome in freshness


def test_production_prompt_envelopes_include_structured_contracts():
    envelopes = [
        deep_player_research_prompt(context={}),
        blind_spot_prompt(context={}),
        evidence_bundle_assessment_prompt(context={}),
        final_player_synthesis_prompt(context={}),
    ]

    assert all(envelope.structured_output_contract for envelope in envelopes)
