from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from fpl_intelligence.db.base import Base
from fpl_intelligence.models import Player, ResearchLinkStatus, ResearchResult, ResearchThreadType
from fpl_intelligence.research.persistence import ResearchPersistenceService
from fpl_intelligence.research.two_stage import (
    PlayerResolver,
    ResearchExtraction,
    SearchResult,
    TwoStageResearchService,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
    engine.dispose()


def player(player_id, first, second, display):
    return SimpleNamespace(id=player_id, first_name=first, second_name=second, display_name=display)


class Search:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, *, domains):
        self.calls.append((query, domains))
        return self.results


class Retriever:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.calls = []

    def retrieve(self, url):
        self.calls.append(url)
        if url in self.failures:
            raise RuntimeError("page unavailable")
        return "Actual page evidence"


class Extractor:
    def extract(self, *, thread, link, page_content):
        assert page_content == "Actual page evidence"
        return ResearchExtraction(
            summary="Useful update",
            findings="Likely to start after returning to training.",
            evidence="Manager said the player trained.",
            uncertainties="Final lineup is unknown.",
            referenced_players=["Bukayo Saka", "Invented Person"],
        )


def setup(session, results=(), failures=()):
    session.add(Player(id=7))
    session.commit()
    thread = ResearchPersistenceService().create_thread(
        session,
        title="Arsenal availability",
        thread_type=ResearchThreadType.DISCOVERY,
        gameweek_id=3,
        question="Who will start?",
    )
    service = TwoStageResearchService(
        search_provider=Search(results),
        retriever=Retriever(failures),
        extractor=Extractor(),
    )
    resolver = PlayerResolver([player(7, "Bukayo", "Saka", "Saka"), player(8, "Other", "Known", "Known")])
    return thread, service, resolver


def test_collection_persists_metadata_and_players_but_no_results(session):
    results = [
        SearchResult(
            url="https://www.reddit.com/r/FantasyPL/comments/abc/news/?utm_source=x",
            title="Training update",
            snippet="Saka discussed by the manager",
            player_names=["Bukayo Saka", "Made Up"],
        )
    ]
    thread, service, resolver = setup(session, results)

    summary = service.collect(session, thread_id=thread.id, player_resolver=resolver)

    links = service.persistence.list_links(session, thread.id)
    assert summary["results_considered"] == summary["links_accepted"] == summary["links_added"] == 1
    assert links[0].canonical_url.endswith("/abc/news")
    assert links[0].source_type == "reddit"
    assert links[0].relevance_reason == "Saka discussed by the manager"
    assert {item.id for item in links[0].players} == {7}
    assert session.scalar(select(func.count()).select_from(ResearchResult)) == 0
    assert session.get(Player, 8) is None


def test_collection_skips_canonical_duplicate_and_unapproved_domain(session):
    results = [
        SearchResult(url="https://allaboutfpl.com/news/?utm_campaign=x"),
        SearchResult(url="https://allaboutfpl.com/news"),
        SearchResult(url="https://unapproved.example/story"),
    ]
    thread, service, resolver = setup(session, results)
    summary = service.collect(session, thread_id=thread.id, player_resolver=resolver, queries=["one"])
    assert summary["results_considered"] == 3
    assert summary["links_accepted"] == 2
    assert summary["links_added"] == 1
    assert summary["duplicates_skipped"] == 1


def test_research_selected_links_persists_provenance_and_content_players(session):
    thread, service, resolver = setup(session)
    selected = service.persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/selected")
    other = service.persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/other")

    summary = service.research(session, thread_id=thread.id, player_resolver=resolver, link_ids=[selected.id])

    assert summary["researched"] == 1
    assert selected.status == ResearchLinkStatus.RESEARCHED
    assert other.status == ResearchLinkStatus.COLLECTED
    result = service.persistence.repository.list_results(session, thread.id)[0]
    assert result.research_link.id == selected.id
    assert result.thread.id == thread.id
    assert result.research_link.original_url.endswith("/selected")
    assert {item.id for item in result.players} == {7}
    assert {item.id for item in selected.players} == {7}
    assert session.get(Player, 8) is None


def test_one_retrieval_failure_does_not_discard_success(session):
    failed_url = "https://premierleague.com/fail"
    thread, service, resolver = setup(session, failures=[failed_url])
    failed = service.persistence.add_collected_link(session, thread_id=thread.id, url=failed_url)
    good = service.persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/good")

    summary = service.research(session, thread_id=thread.id, player_resolver=resolver, all_collected=True)

    assert summary["failed"] == 1
    assert summary["researched"] == 1
    assert session.get(type(failed), failed.id).status == ResearchLinkStatus.FAILED
    assert session.get(type(good), good.id).status == ResearchLinkStatus.RESEARCHED
    assert len(service.persistence.repository.list_results(session, thread.id)) == 1


def test_invalid_selection_and_inappropriate_state_are_handled(session):
    thread, service, resolver = setup(session)
    link = service.persistence.add_collected_link(session, thread_id=thread.id, url="https://premierleague.com/once")
    service.research(session, thread_id=thread.id, player_resolver=resolver, link_ids=[link.id])

    repeated = service.research(session, thread_id=thread.id, player_resolver=resolver, link_ids=[link.id])
    assert repeated["skipped"] == 1
    assert len(service.persistence.repository.list_results(session, thread.id)) == 1
    with pytest.raises(ValueError, match="do not belong"):
        service.research(session, thread_id=thread.id, player_resolver=resolver, link_ids=["missing"])
    with pytest.raises(ValueError, match="selected"):
        service.research(session, thread_id=thread.id, player_resolver=resolver)
