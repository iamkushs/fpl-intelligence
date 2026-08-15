from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import pytest

from fpl_intelligence.db.base import Base
from fpl_intelligence.models import ResearchLinkStatus, ResearchThreadType
from fpl_intelligence.research.persistence import DuplicateResearchLinkError, ResearchPersistenceService


@pytest.fixture
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
    engine.dispose()


def _thread(session, title="Player news"):
    return ResearchPersistenceService().create_thread(session, title=title, thread_type=ResearchThreadType.DISCOVERY, gameweek_id=2)


def test_create_thread_and_add_and_list_link(session):
    service = ResearchPersistenceService()
    thread = _thread(session)
    link = service.add_collected_link(session, thread_id=thread.id, url="HTTPS://Example.com/story/?utm_source=x&b=2&a=1#part", title="Story")
    assert thread.id and thread.created_at and thread.updated_at
    assert link.canonical_url == "https://example.com/story?a=1&b=2"
    assert link.domain == "example.com"
    assert link.status == ResearchLinkStatus.COLLECTED
    assert service.list_links(session, thread.id) == [link]


def test_duplicate_canonical_url_is_rejected_within_thread_but_allowed_across_threads(session):
    service = ResearchPersistenceService()
    first = _thread(session, "First")
    second = _thread(session, "Second")
    service.add_collected_link(session, thread_id=first.id, url="https://EXAMPLE.com/news/?utm_campaign=x")
    with pytest.raises(DuplicateResearchLinkError):
        service.add_collected_link(session, thread_id=first.id, url="https://example.com/news")
    assert service.add_collected_link(session, thread_id=second.id, url="https://example.com/news").id


def test_link_and_result_can_concern_multiple_players_and_preserve_provenance(session):
    service = ResearchPersistenceService()
    thread = _thread(session)
    link = service.add_collected_link(session, thread_id=thread.id, url="https://example.com/team-news", player_ids=[10, 20])
    result = service.persist_result(session, link=link, summary="Two players discussed", findings="Both trained.", evidence="Club interview", uncertainty="Lineup unknown", player_ids=[10, 20])
    assert {player.id for player in link.players} == {10, 20}
    assert {player.id for player in result.players} == {10, 20}
    retrieved = service.get_player_research(session, 10)[0]
    assert retrieved.id == result.id
    assert retrieved.research_link.original_url == "https://example.com/team-news"
    assert retrieved.thread.id == thread.id
    assert retrieved.research_link.thread.id == thread.id


def test_one_player_accumulates_multiple_results(session):
    service = ResearchPersistenceService()
    thread = _thread(session)
    first = service.add_collected_link(session, thread_id=thread.id, url="https://example.com/one")
    second = service.add_collected_link(session, thread_id=thread.id, url="https://example.com/two")
    service.persist_result(session, link=first, summary="One", findings="First", evidence="Source one", player_ids=[10])
    service.persist_result(session, link=second, summary="Two", findings="Second", evidence="Source two", player_ids=[10])
    assert {result.summary for result in service.get_player_research(session, 10)} == {"One", "Two"}
