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
