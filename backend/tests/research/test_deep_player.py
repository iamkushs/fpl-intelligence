from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fpl_intelligence.db.base import Base
from fpl_intelligence.models import Player, ResearchThread, ResearchThreadType
from fpl_intelligence.research.deep_player import DEEP_PLAYER_RESEARCH_DIMENSIONS, DeepPlayerResearchService
from fpl_intelligence.research.evidence_bundles import EvidenceBundleService
from fpl_intelligence.research.two_stage import PlayerResolver

@pytest.fixture
def db():
    engine=create_engine("sqlite://"); Base.metadata.create_all(engine); session=sessionmaker(bind=engine)(); session.add(Player(id=1)); thread=ResearchThread(title="Player",thread_type=ResearchThreadType.PLAYER);session.add(thread);session.commit();yield session,thread;session.close()
def service(): return DeepPlayerResearchService(source_service=object(),quality_execution=object(),player_resolver=PlayerResolver([]),bundle_service=EvidenceBundleService())
def test_create_run_is_player_centric_and_uses_valid_dimensions(db):
    session,thread=db; run=service().create_run(session,thread_id=thread.id,player_id=1,research_cutoff=datetime.now(timezone.utc)); assert run.status=="pending" and set(run.target_dimensions)<=set(DEEP_PLAYER_RESEARCH_DIMENSIONS)
def test_create_run_requires_canonical_player(db):
    session,thread=db
    with pytest.raises(LookupError): service().create_run(session,thread_id=thread.id,player_id=9,research_cutoff=datetime.now(timezone.utc))

class Blind:
    def find(self, **kwargs): return {"findings":[{"dimension":"penalties","category":"order","question":"Who takes penalties?","why_it_matters":"Role can change.","preferred_source_types":["official"],"suggested_queries":["penalty order"]}]}
class Synthesis:
    def synthesize(self, **kwargs): return {"overall_research_state":"thin","executive_summary":"Research remains limited.","dimension_summaries":[],"key_strengths":[],"key_risks":[],"contradictions":[],"missing_information":["Evidence"],"future_monitoring":[{"category":"availability","description":"Recheck availability after press conference.","condition":{"event":"press_conference"}}]}
def test_blind_spots_and_synthesis_are_durable_and_monitoring_is_idempotent(db):
    session,thread=db; item=DeepPlayerResearchService(source_service=object(),quality_execution=object(),player_resolver=PlayerResolver([]),bundle_service=EvidenceBundleService(),blind_spot_provider=Blind(),synthesis_provider=Synthesis())
    run=item.create_run(session,thread_id=thread.id,player_id=1,research_cutoff=datetime.now(timezone.utc)); item.run_blind_spot_pass(session,run.id); one=item.synthesize(session,run.id); two=item.synthesize(session,run.id)
    assert one.synthesis.id==two.synthesis.id and one.blind_spots[0].status=="unresolved"


def test_full_run_marks_deep_run_failed_when_a_stage_raises(db):
    session, thread = db
    item = service()
    run = item.create_run(session, thread_id=thread.id, player_id=1, research_cutoff=datetime.now(timezone.utc))
    item.execute_research = lambda *_: (_ for _ in ()).throw(ValueError("provider unavailable"))

    with pytest.raises(ValueError, match="provider unavailable"):
        item.execute_full_run(session, run.id)

    persisted = item.get_run(session, run.id)
    assert persisted.status == "failed" and persisted.failure_reason == "provider unavailable"
