"""Third-stage synthesis of persisted research into reviewable Watchlist suggestions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.models import (
    Player, ResearchResult, ResearchThread, ResearchThreadType, WatchlistEntry,
    WatchlistSuggestion, WatchlistSuggestionStatus,
)
from fpl_intelligence.watchlist.service import WatchlistService


@dataclass(frozen=True)
class DiscoveryCandidate:
    player_reference: str
    reason: str
    supporting_result_ids: list[str] = field(default_factory=list)


class DiscoveryAnalyzer(Protocol):
    def analyze(self, *, thread: ResearchThread, results: list[ResearchResult]) -> list[DiscoveryCandidate]: ...


class CodexDiscoveryAnalyzer:
    def __init__(self, codex: CodexService):
        self.codex = codex

    def analyze(self, *, thread: ResearchThread, results: list[ResearchResult]) -> list[DiscoveryCandidate]:
        evidence = "\n\n".join(
            f"RESULT {r.id}\nSummary: {r.summary}\nFindings: {r.findings}\nEvidence: {r.evidence}\nUncertainty: {r.uncertainty or ''}"
            for r in results
        )
        prompt = (
            "Using only the persisted researched results below, identify existing FPL players with a credible "
            "reason to begin monitoring. This is discovery, not transfer advice. Do not use URL titles as evidence. "
            "Return JSON only: {\"candidates\":[{\"player_reference\":str,\"reason\":str,"
            "\"supporting_result_ids\":[str]}]}. Reasons must be concise and evidence-grounded; result IDs must come "
            "from the supplied results. Do not invent player IDs. Omit weak candidates.\n"
            f"Thread: {thread.title}\nQuestion: {thread.question or ''}\n{evidence}"
        )
        text = self.codex.execute(prompt=prompt).final_text.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        else:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        payload = json.loads(text)
        return [
            DiscoveryCandidate(
                player_reference=str(item.get("player_reference", "")).strip(),
                reason=str(item.get("reason", "")).strip()[:500],
                supporting_result_ids=[str(value) for value in item.get("supporting_result_ids", [])],
            )
            for item in payload.get("candidates", []) if isinstance(item, dict)
        ]


class DiscoveryService:
    def __init__(self, analyzer: DiscoveryAnalyzer):
        self.analyzer = analyzer

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9 ]", " ", value.lower()).split())

    def _resolve(self, session: Session, reference: str, official_players) -> int | None:
        aliases: dict[str, set[int]] = {}
        persisted = set(session.scalars(select(Player.id)).all())
        for player in official_players:
            if player.id not in persisted:
                continue
            for alias in {player.display_name, f"{player.first_name} {player.second_name}"}:
                aliases.setdefault(self._normalize(alias), set()).add(player.id)
        matches = aliases.get(self._normalize(reference), set())
        return next(iter(matches)) if len(matches) == 1 else None

    def generate(self, session: Session, *, thread_id: str, official_players) -> dict:
        thread = session.get(ResearchThread, thread_id)
        if thread is None:
            raise LookupError("ResearchThread not found")
        if thread.thread_type != ResearchThreadType.DISCOVERY:
            raise ValueError("Player discovery is only available for discovery threads")
        results = list(session.scalars(
            select(ResearchResult).where(ResearchResult.research_thread_id == thread_id)
            .options(selectinload(ResearchResult.players))
        ).all())
        summary = {
            "research_results_considered": len(results), "candidate_players_identified": 0,
            "suggestions_created": 0, "duplicates_skipped": 0,
            "already_watchlisted_players_skipped": 0, "unresolved_player_references": [],
            "candidates_without_evidence_skipped": 0,
        }
        if not results:
            return summary
        by_id = {result.id: result for result in results}
        candidates = self.analyzer.analyze(thread=thread, results=results)
        summary["candidate_players_identified"] = len(candidates)
        for candidate in candidates:
            player_id = self._resolve(session, candidate.player_reference, official_players)
            if player_id is None:
                summary["unresolved_player_references"].append(candidate.player_reference)
                continue
            supporting = [by_id[value] for value in dict.fromkeys(candidate.supporting_result_ids) if value in by_id]
            if not candidate.reason or not supporting:
                summary["candidates_without_evidence_skipped"] += 1
                continue
            if session.scalar(select(WatchlistEntry.id).where(WatchlistEntry.player_id == player_id, WatchlistEntry.active.is_(True))):
                summary["already_watchlisted_players_skipped"] += 1
                continue
            existing = session.scalar(select(WatchlistSuggestion.id).where(
                WatchlistSuggestion.player_id == player_id,
                ((WatchlistSuggestion.status == WatchlistSuggestionStatus.PENDING) |
                 (WatchlistSuggestion.research_thread_id == thread_id)),
            ))
            if existing:
                summary["duplicates_skipped"] += 1
                continue
            suggestion = WatchlistSuggestion(
                player_id=player_id, research_thread_id=thread_id,
                reason=candidate.reason, status=WatchlistSuggestionStatus.PENDING,
                research_results=supporting,
            )
            session.add(suggestion)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                summary["duplicates_skipped"] += 1
            else:
                summary["suggestions_created"] += 1
        return summary

    def list_pending(self, session: Session) -> list[WatchlistSuggestion]:
        return list(session.scalars(
            select(WatchlistSuggestion)
            .where(WatchlistSuggestion.status == WatchlistSuggestionStatus.PENDING)
            .options(selectinload(WatchlistSuggestion.thread), selectinload(WatchlistSuggestion.research_results))
            .order_by(WatchlistSuggestion.created_at.desc())
        ).all())

    def get(self, session: Session, suggestion_id: str) -> WatchlistSuggestion | None:
        return session.scalar(select(WatchlistSuggestion).where(WatchlistSuggestion.id == suggestion_id).options(
            selectinload(WatchlistSuggestion.thread), selectinload(WatchlistSuggestion.research_results)
        ))

    def accept(self, session: Session, suggestion_id: str) -> WatchlistSuggestion:
        suggestion = self.get(session, suggestion_id)
        if suggestion is None:
            raise LookupError("Watchlist suggestion not found")
        if suggestion.status == WatchlistSuggestionStatus.ACCEPTED:
            return suggestion
        if suggestion.status != WatchlistSuggestionStatus.PENDING:
            raise ValueError("Only pending suggestions can be accepted")
        try:
            WatchlistService().add(
                session, suggestion.player_id, reason=suggestion.reason, source="research", commit=False
            )
            now = datetime.now(timezone.utc)
            suggestion.status = WatchlistSuggestionStatus.ACCEPTED
            suggestion.reviewed_at = suggestion.accepted_at = now
            session.commit()
        except IntegrityError:
            # A concurrent acceptance may have inserted the unique membership first.
            session.rollback()
            suggestion = self.get(session, suggestion_id)
            if suggestion is None:
                raise LookupError("Watchlist suggestion not found")
            if suggestion.status == WatchlistSuggestionStatus.ACCEPTED:
                return suggestion
            entry = WatchlistService().get(session, suggestion.player_id)
            if entry is None or not entry.active:
                raise
            now = datetime.now(timezone.utc)
            suggestion.status = WatchlistSuggestionStatus.ACCEPTED
            suggestion.reviewed_at = suggestion.accepted_at = now
            session.commit()
        session.refresh(suggestion)
        return suggestion

    def reject(self, session: Session, suggestion_id: str) -> WatchlistSuggestion:
        suggestion = self.get(session, suggestion_id)
        if suggestion is None:
            raise LookupError("Watchlist suggestion not found")
        if suggestion.status == WatchlistSuggestionStatus.REJECTED:
            return suggestion
        if suggestion.status != WatchlistSuggestionStatus.PENDING:
            raise ValueError("Only pending suggestions can be rejected")
        now = datetime.now(timezone.utc)
        suggestion.status = WatchlistSuggestionStatus.REJECTED
        suggestion.reviewed_at = suggestion.rejected_at = now
        session.commit()
        session.refresh(suggestion)
        return suggestion
