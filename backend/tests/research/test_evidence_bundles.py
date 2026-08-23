from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fpl_intelligence.db.base import Base
from fpl_intelligence.models import Player, ResearchEvidence, ResearchEvidenceType, ResearchThread, ResearchThreadType, EvidenceRelation, EvidenceRelationshipType
from fpl_intelligence.research.evidence_bundles import EvidenceBundleService

class Provider:
    def __init__(self, strength="adequate", confidence="medium"): self.strength,self.confidence=strength,confidence
    def assess(self, **kwargs): return {"bundle_strength":self.strength,"confidence":self.confidence,"thesis":"Working thesis","rationale":"Evidence supports it","contradiction_summary":None,"missing_information":["more evidence"]}

@pytest.fixture
def session():
    engine=create_engine("sqlite://"); Base.metadata.create_all(engine); value=sessionmaker(bind=engine)(); value.add(Player(id=1)); thread=ResearchThread(title="x",thread_type=ResearchThreadType.PLAYER); value.add(thread); value.commit(); yield value,thread; value.close()
def evidence(session,thread,claim_type="minutes",when=None):
    item=ResearchEvidence(research_thread_id=thread.id,claim="claim",claim_type=claim_type,evidence_type=ResearchEvidenceType.FACT,reliability="high",relevance="high",published_at=when or datetime.now(timezone.utc)); item.players=[session.get(Player,1)]; session.add(item); session.commit(); return item
def test_build_excludes_other_dimension_and_post_cutoff(session):
    db,thread=session; cutoff=datetime.now(timezone.utc); first=evidence(db,thread,when=cutoff-timedelta(days=1)); evidence(db,thread,"availability",cutoff-timedelta(days=1)); evidence(db,thread,when=cutoff+timedelta(days=1))
    bundle=EvidenceBundleService().build_dimension_bundle(db,thread_id=thread.id,player_id=1,dimension="minutes",research_cutoff=cutoff)
    assert [m.evidence_id for m in bundle.members]==[first.id]
def test_supersession_and_assessment_idempotency(session):
    db,thread=session; old=evidence(db,thread); new=evidence(db,thread); db.add(EvidenceRelation(from_evidence_id=new.id,to_evidence_id=old.id,relation_type=EvidenceRelationshipType.SUPERSEDES)); db.commit()
    service=EvidenceBundleService(Provider()); bundle=service.build_dimension_bundle(db,thread_id=thread.id,player_id=1,dimension="minutes",research_cutoff=datetime.now(timezone.utc)); assert {m.role for m in bundle.members}=={"current","superseded"}
    _,one=service.assess_bundle(db,bundle.id); _,two=service.assess_bundle(db,bundle.id); assert one.id==two.id
def test_zero_evidence_bundle_is_assessed_deterministically_without_provider(session):
    db,thread=session
    bundle=EvidenceBundleService().build_dimension_bundle(db,thread_id=thread.id,player_id=1,dimension="minutes",research_cutoff=datetime.now(timezone.utc))

    _, assessment=EvidenceBundleService().assess_bundle(db,bundle.id)

    assert (assessment.bundle_strength, assessment.confidence)==("unresolved","unresolved")
    assert assessment.missing_information==["Current, source-backed evidence for this dimension."]
