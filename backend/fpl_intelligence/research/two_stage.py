"""Executable collection and link-research stages built on NR01 persistence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.integrations.fpl.schemas import FPLPlayer
from fpl_intelligence.models import ResearchLinkStatus
from fpl_intelligence.research.persistence import (
    DuplicateResearchLinkError,
    ResearchPersistenceService,
)


# Kept here so approval policy is centralized and can later move to runtime config.
APPROVED_SOURCE_DOMAINS = {
    "fantasy.premierleague.com": "official_fpl",
    "premierleague.com": "official_premier_league",
    "reddit.com": "reddit",
    "www.reddit.com": "reddit",
    "old.reddit.com": "reddit",
    "fantasyfootballscout.co.uk": "specialist_fpl",
    "allaboutfpl.com": "specialist_fpl",
    "fantasyfootballhub.co.uk": "specialist_fpl",
    "fplwire.com": "specialist_fpl",
}


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str | None = None
    snippet: str | None = None
    source_type: str | None = None
    player_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchExtraction:
    summary: str
    findings: str
    evidence: str
    uncertainties: str | None = None
    referenced_players: list[str] = field(default_factory=list)


class SearchProvider(Protocol):
    def search(self, query: str, *, domains: tuple[str, ...]) -> list[SearchResult]: ...


class PageRetriever(Protocol):
    def retrieve(self, url: str) -> str: ...


class ResearchExtractor(Protocol):
    def extract(self, *, thread, link, page_content: str) -> ResearchExtraction: ...


def _json_object(text: str) -> dict:
    value = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", value, re.DOTALL)
    if fenced:
        value = fenced.group(1)
    else:
        start, end = value.find("{"), value.rfind("}")
        if start >= 0 and end > start:
            value = value[start : end + 1]
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("Structured provider response must be an object")
    return payload


class CodexSearchProvider:
    """Uses the existing model gateway and its web capability for discovery only."""

    def __init__(self, codex: CodexService):
        self.codex = codex

    def search(self, query: str, *, domains: tuple[str, ...]) -> list[SearchResult]:
        prompt = (
            "Search the web for the query below. Only return results from the allowed domains. "
            "Do not read deeply, summarize pages, or infer findings. Return JSON only as "
            "{\"results\":[{\"url\":str,\"title\":str|null,\"snippet\":str|null,"
            "\"source_type\":str|null,\"player_names\":[str]}]}.\n"
            f"Allowed domains: {', '.join(domains)}\nQuery: {query}"
        )
        payload = _json_object(self.codex.execute(prompt=prompt).final_text)
        return [SearchResult(**item) for item in payload.get("results", []) if isinstance(item, dict)]


class HTTPPageRetriever:
    def __init__(self, *, timeout_seconds: float = 20.0, max_characters: int = 100_000):
        self.timeout_seconds = timeout_seconds
        self.max_characters = max_characters

    def retrieve(self, url: str) -> str:
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "FPL-Intelligence/0.1"})
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not any(kind in content_type.lower() for kind in ("text/", "html", "json")):
                raise ValueError(f"Unsupported page content type: {content_type or 'unknown'}")
            text = response.text.strip()
            if not text:
                raise ValueError("Retrieved page was empty")
            return text[: self.max_characters]


class CodexResearchExtractor:
    def __init__(self, codex: CodexService):
        self.codex = codex

    def extract(self, *, thread, link, page_content: str) -> ResearchExtraction:
        prompt = (
            "Extract only useful FPL intelligence supported by this page in the thread context. "
            "Do not invent claims or player IDs. Return JSON only with string fields summary, findings, "
            "evidence, uncertainties (nullable), and referenced_players (array of names). Findings must "
            "cover relevant availability, minutes, role, rotation, set pieces, performance, comments, "
            "team changes, fixtures, conflicts, and limitations only where evidenced.\n"
            f"Thread title: {thread.title}\nThread type: {thread.thread_type}\n"
            f"Question: {thread.question or ''}\nGameweek: {thread.gameweek_id or ''}\n"
            f"Source URL: {link.original_url}\nPage content:\n{page_content}"
        )
        payload = _json_object(self.codex.execute(prompt=prompt).final_text)
        extraction = ResearchExtraction(
            summary=str(payload.get("summary", "")).strip(),
            findings=str(payload.get("findings", "")).strip(),
            evidence=str(payload.get("evidence", "")).strip(),
            uncertainties=(str(payload["uncertainties"]).strip() if payload.get("uncertainties") else None),
            referenced_players=[str(name) for name in payload.get("referenced_players", [])],
        )
        if not extraction.summary or not extraction.findings or not extraction.evidence:
            raise ValueError("Research extraction omitted required substantive fields")
        return extraction


class PlayerResolver:
    """Resolves provider-supplied names to official IDs, then to persisted Players."""

    def __init__(self, players: list[FPLPlayer]):
        self.players = players

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9 ]", " ", value.lower()).split())

    def resolve(self, session: Session, names: list[str]) -> list[int]:
        aliases: dict[str, set[int]] = {}
        for player in self.players:
            for alias in {player.display_name, player.first_name + " " + player.second_name}:
                aliases.setdefault(self._normalize(alias), set()).add(player.id)
        candidates = []
        for name in names:
            matches = aliases.get(self._normalize(name), set())
            if len(matches) == 1:
                candidates.extend(matches)
        persisted = ResearchPersistenceService().repository.existing_players(session, candidates)
        return [player.id for player in persisted]


class TwoStageResearchService:
    def __init__(self, *, search_provider: SearchProvider, retriever: PageRetriever, extractor: ResearchExtractor):
        self.search_provider = search_provider
        self.retriever = retriever
        self.extractor = extractor
        self.persistence = ResearchPersistenceService()

    def collect(self, session: Session, *, thread_id: str, player_resolver: PlayerResolver, queries: list[str] | None = None) -> dict:
        thread = self.persistence.repository.get_thread(session, thread_id)
        if thread is None:
            raise LookupError("ResearchThread not found")
        executed = queries or [" ".join(filter(None, [thread.title, thread.question, f"FPL GW{thread.gameweek_id}" if thread.gameweek_id else None]))]
        summary = {"queries_executed": executed, "results_considered": 0, "links_accepted": 0, "links_added": 0, "duplicates_skipped": 0, "failures": []}
        domains = tuple(APPROVED_SOURCE_DOMAINS)
        for query in executed:
            try:
                results = self.search_provider.search(query, domains=domains)
            except Exception as exc:
                summary["failures"].append({"query": query, "error": str(exc)})
                continue
            summary["results_considered"] += len(results)
            for item in results:
                try:
                    from urllib.parse import urlsplit
                    domain = (urlsplit(item.url).hostname or "").lower()
                    source_type = APPROVED_SOURCE_DOMAINS.get(domain)
                    if source_type is None:
                        continue
                    summary["links_accepted"] += 1
                    player_ids = player_resolver.resolve(session, item.player_names)
                    self.persistence.add_collected_link(session, thread_id=thread.id, url=item.url, title=item.title, source_type=item.source_type or source_type, relevance_reason=item.snippet, player_ids=player_ids)
                    summary["links_added"] += 1
                except DuplicateResearchLinkError:
                    summary["duplicates_skipped"] += 1
                except Exception as exc:
                    summary["failures"].append({"url": item.url, "error": str(exc)})
        return summary

    def research(self, session: Session, *, thread_id: str, player_resolver: PlayerResolver, link_ids: list[str] | None = None, all_collected: bool = False) -> dict:
        thread = self.persistence.repository.get_thread(session, thread_id)
        if thread is None:
            raise LookupError("ResearchThread not found")
        if bool(link_ids) == bool(all_collected):
            raise ValueError("Provide selected link_ids or set all_collected=true")
        links = self.persistence.list_links(session, thread_id)
        if link_ids:
            requested = set(link_ids)
            links = [link for link in links if link.id in requested]
            missing = requested - {link.id for link in links}
            if missing:
                raise ValueError("One or more selected links do not belong to this thread")
        else:
            links = [link for link in links if link.status == ResearchLinkStatus.COLLECTED]
        summary = {"links_requested": len(links), "researched": 0, "failed": 0, "skipped": 0, "failures": [], "result_ids": []}
        for link in links:
            if link.status != ResearchLinkStatus.COLLECTED:
                summary["skipped"] += 1
                summary["failures"].append({"link_id": link.id, "error": f"Link state is {link.status}, expected collected"})
                continue
            try:
                page = self.retriever.retrieve(link.original_url)
                extraction = self.extractor.extract(thread=thread, link=link, page_content=page)
                resolved = player_resolver.resolve(session, extraction.referenced_players)
                combined = list(dict.fromkeys([*(player.id for player in link.players), *resolved]))
                if resolved:
                    self.persistence.associate_link_with_players(session, link, resolved)
                result = self.persistence.persist_result(session, link=link, summary=extraction.summary, findings=extraction.findings, evidence=extraction.evidence, uncertainty=extraction.uncertainties, player_ids=combined)
                summary["researched"] += 1
                summary["result_ids"].append(result.id)
            except Exception as exc:
                session.rollback()
                failed_link = self.persistence.repository.get_link(session, link.id)
                if failed_link is not None:
                    failed_link.status = ResearchLinkStatus.FAILED
                    session.commit()
                summary["failed"] += 1
                summary["failures"].append({"link_id": link.id, "error": str(exc)})
        return summary
