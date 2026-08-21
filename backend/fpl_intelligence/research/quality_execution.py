from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
from typing import Protocol
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.models import (
    EvidenceRelationshipType,
    ResearchEvidence,
    ResearchQualityRun,
    ResearchQualityStage,
    ResearchQualityStatus,
    ResearchThread,
)
from fpl_intelligence.research.evidence import ResearchEvidenceService
from fpl_intelligence.research.eval2_prompts import counter_search_prompt, freshness_prompt, reddit_research_prompt
from fpl_intelligence.research.persistence import DuplicateResearchLinkError, ResearchPersistenceService
from fpl_intelligence.research.quality import ResearchQualityService
from fpl_intelligence.research.source_discovery import (
    AtomicEvidencePayload,
    Eval2AtomicEvidenceProvider,
    Eval2SourceDiscoveryService,
    SourceCandidatePayload,
)
from fpl_intelligence.research.two_stage import PlayerResolver
from fpl_intelligence.repositories.research_quality import COUNTER_SEARCH_OUTCOMES, FRESHNESS_OUTCOMES


@dataclass(frozen=True)
class RedditQualityOutput:
    candidates: list[SourceCandidatePayload] = field(default_factory=list)


@dataclass(frozen=True)
class CounterSearchQualityOutput:
    candidates: list[SourceCandidatePayload] = field(default_factory=list)
    outcome: str = "unresolved"


@dataclass(frozen=True)
class FreshnessQualityOutput:
    candidates: list[SourceCandidatePayload] = field(default_factory=list)
    outcome: str = "unresolved"
    superseding_candidate_index: int | None = None
    monitoring_condition: dict | None = None


class RedditQualityProvider(Protocol):
    def research(self, *, prompt: str, prompt_version: str, run: ResearchQualityRun, player, situation, thread: ResearchThread) -> RedditQualityOutput: ...


class CounterSearchQualityProvider(Protocol):
    def research(self, *, prompt: str, prompt_version: str, run: ResearchQualityRun, player, situation, thread: ResearchThread) -> CounterSearchQualityOutput: ...


class FreshnessQualityProvider(Protocol):
    def research(self, *, prompt: str, prompt_version: str, run: ResearchQualityRun, player, situation, thread: ResearchThread, target_evidence: ResearchEvidence) -> FreshnessQualityOutput: ...


class Eval2QualityExecutionService:
    def __init__(
        self,
        *,
        source_service: Eval2SourceDiscoveryService,
        player_resolver: PlayerResolver,
        reddit_provider: RedditQualityProvider | None = None,
        counter_provider: CounterSearchQualityProvider | None = None,
        freshness_provider: FreshnessQualityProvider | None = None,
        quality_service: ResearchQualityService | None = None,
        persistence: ResearchPersistenceService | None = None,
        evidence_service: ResearchEvidenceService | None = None,
    ):
        self.source_service = source_service
        self.player_resolver = player_resolver
        self.reddit_provider = reddit_provider
        self.counter_provider = counter_provider
        self.freshness_provider = freshness_provider
        self.quality_service = quality_service or ResearchQualityService()
        self.persistence = persistence or ResearchPersistenceService()
        self.evidence_service = evidence_service or ResearchEvidenceService()

    def execute_reddit(self, session: Session, run_id: str) -> dict:
        run = self._load_run(session, run_id, ResearchQualityStage.REDDIT)
        cutoff = _stored_utc(run.research_cutoff)
        envelope = reddit_research_prompt(player_payload={"id": run.player_id}, situation_payload=self._situation_payload(run), research_cutoff=cutoff.isoformat())
        try:
            output = self._require_provider(self.reddit_provider).research(prompt=envelope.prompt, prompt_version=run.prompt_version, run=run, player=run.player, situation=run.situation, thread=run.thread)
            candidates = [item for item in self._require_candidates(output.candidates) if self._is_reddit_url(item.url)]
            original_provider = self.source_service.atomic_provider
            self.source_service.atomic_provider = _SupporterOnlyAtomicProvider(original_provider)
            try:
                result = self._process_candidates(session, run, candidates)
            finally:
                self.source_service.atomic_provider = original_provider
            return self._finish_sources(session, run, result)
        except Exception as exc:
            return self._fail_run(session, run.id, str(exc))

    def execute_counter_search(self, session: Session, run_id: str) -> dict:
        run = self._load_run(session, run_id, ResearchQualityStage.COUNTER_SEARCH)
        if not run.challenged_claim:
            raise ValueError("counter_search requires challenged_claim")
        cutoff = _stored_utc(run.research_cutoff)
        envelope = counter_search_prompt(challenged_claim=run.challenged_claim, questions=run.questions, research_cutoff=cutoff.isoformat())
        try:
            output = self._require_provider(self.counter_provider).research(prompt=envelope.prompt, prompt_version=run.prompt_version, run=run, player=run.player, situation=run.situation, thread=run.thread)
            if output.outcome not in COUNTER_SEARCH_OUTCOMES:
                raise ValueError("Invalid counter_search outcome")
            result = self._process_candidates(session, run, self._require_candidates(output.candidates))
            status = self._source_status(result)
            completed = self.quality_service.complete_counter_search_run(session, run_id=run.id, outcome=output.outcome, link_ids=result["link_ids"], evidence_ids=result["evidence_ids"], partial=status == ResearchQualityStatus.PARTIAL)
            if status == ResearchQualityStatus.FAILED:
                completed = self.quality_service.repository.update_status(session, run.id, ResearchQualityStatus.FAILED, "All counter-search sources failed")
            return {"run": completed, **result}
        except Exception as exc:
            return self._fail_run(session, run.id, str(exc))

    def execute_freshness(self, session: Session, run_id: str) -> dict:
        run = self._load_run(session, run_id, ResearchQualityStage.FRESHNESS)
        target = run.target_evidence
        if target is None:
            raise ValueError("freshness requires target_evidence_id")
        cutoff = _stored_utc(run.research_cutoff)
        envelope = freshness_prompt(evidence_payload=self._evidence_payload(target), research_cutoff=cutoff.isoformat())
        try:
            output = self._require_provider(self.freshness_provider).research(prompt=envelope.prompt, prompt_version=run.prompt_version, run=run, player=run.player, situation=run.situation, thread=run.thread, target_evidence=target)
            if output.outcome not in FRESHNESS_OUTCOMES:
                raise ValueError("Invalid freshness outcome")
            result = self._process_candidates(session, run, self._require_candidates(output.candidates))
            superseding_id = None
            if output.outcome == "changed" and not result["evidence_ids"]:
                raise ValueError("changed freshness outcome requires new evidence")
            if output.outcome == "superseded":
                if output.superseding_candidate_index is None or output.superseding_candidate_index >= len(result["evidence_by_candidate"]):
                    raise ValueError("superseded freshness outcome requires newly extracted superseding evidence")
                candidate_evidence = result["evidence_by_candidate"][output.superseding_candidate_index]
                if len(candidate_evidence) != 1:
                    raise ValueError("superseded freshness outcome requires exactly one superseding evidence item")
                superseding_id = candidate_evidence[0]
            status = self._source_status(result)
            completed = self.quality_service.complete_freshness_run(
                session,
                run_id=run.id,
                outcome=output.outcome,
                link_ids=result["link_ids"],
                evidence_ids=result["evidence_ids"],
                superseding_evidence_id=superseding_id,
                monitoring_condition=output.monitoring_condition,
                partial=status == ResearchQualityStatus.PARTIAL,
            )
            if superseding_id:
                self.evidence_service.add_evidence_relation(session, from_evidence_id=superseding_id, to_evidence_id=target.id, relation_type=EvidenceRelationshipType.SUPERSEDES)
            if status == ResearchQualityStatus.FAILED:
                completed = self.quality_service.repository.update_status(session, run.id, ResearchQualityStatus.FAILED, "All freshness sources failed")
            return {"run": completed, **result}
        except Exception as exc:
            return self._fail_run(session, run.id, str(exc))

    def _process_candidates(self, session: Session, run: ResearchQualityRun, candidates: list[SourceCandidatePayload]) -> dict:
        link_ids: list[str] = []
        evidence_ids: list[str] = []
        evidence_by_candidate: list[list[str]] = []
        failures: list[dict] = []
        for candidate in candidates:
            try:
                link = self._persist_link(session, run, candidate)
                response = self.source_service.research_link(session, link_id=link.id, player_resolver=self.player_resolver, research_cutoff=_stored_utc(run.research_cutoff), retry_failed=True)
                if response["failed"]:
                    failures.extend(response["failures"])
                    evidence_by_candidate.append([])
                    continue
                link_ids.append(link.id)
                candidate_evidence: list[str] = []
                for result_id in response["result_ids"]:
                    extracted = self.source_service.extract_atomic_evidence(session, result_id=result_id, research_cutoff=_stored_utc(run.research_cutoff))
                    candidate_evidence.extend(extracted["evidence_ids"])
                evidence_ids.extend(candidate_evidence)
                evidence_by_candidate.append(candidate_evidence)
            except Exception as exc:
                failures.append({"url": candidate.url, "error": str(exc)})
                evidence_by_candidate.append([])
        return {"link_ids": list(dict.fromkeys(link_ids)), "evidence_ids": list(dict.fromkeys(evidence_ids)), "evidence_by_candidate": evidence_by_candidate, "failures": failures, "candidate_count": len(candidates)}

    def _persist_link(self, session: Session, run: ResearchQualityRun, candidate: SourceCandidatePayload):
        from fpl_intelligence.research.persistence import canonicalize_url

        canonical_url, _ = canonicalize_url(candidate.url)
        existing = self.persistence.get_link_by_canonical_url(session, thread_id=run.thread_id, canonical_url=canonical_url)
        if existing is not None:
            return existing
        try:
            return self.persistence.add_collected_link(session, thread_id=run.thread_id, url=candidate.url, title=candidate.title, source_type=candidate.source_category, relevance_reason=candidate.usefulness, player_ids=[run.player_id])
        except DuplicateResearchLinkError:
            return self.persistence.get_link_by_canonical_url(session, thread_id=run.thread_id, canonical_url=canonical_url)

    def _load_run(self, session: Session, run_id: str, stage: str) -> ResearchQualityRun:
        run = session.scalar(
            select(ResearchQualityRun)
            .where(ResearchQualityRun.id == run_id)
            .options(
                selectinload(ResearchQualityRun.thread),
                selectinload(ResearchQualityRun.player),
                selectinload(ResearchQualityRun.situation),
                selectinload(ResearchQualityRun.target_evidence).selectinload(ResearchEvidence.research_link),
            )
        )
        if run is None:
            raise LookupError("ResearchQualityRun not found")
        if run.stage != stage:
            raise ValueError("Quality run stage mismatch")
        if run.status == ResearchQualityStatus.COMPLETED:
            raise ValueError("Completed quality run cannot execute again")
        if run.status not in {ResearchQualityStatus.RUNNING, ResearchQualityStatus.PARTIAL, ResearchQualityStatus.FAILED}:
            raise ValueError("Quality run is not executable")
        return run

    def _finish_sources(self, session: Session, run: ResearchQualityRun, result: dict) -> dict:
        status = self._source_status(result)
        if status == ResearchQualityStatus.FAILED:
            completed = self.quality_service.repository.update_status(session, run.id, ResearchQualityStatus.FAILED, "All Reddit sources failed")
        else:
            completed = self.quality_service.complete_reddit_run(session, run_id=run.id, link_ids=result["link_ids"], evidence_ids=result["evidence_ids"], partial=status == ResearchQualityStatus.PARTIAL)
        return {"run": completed, **result}

    @staticmethod
    def _source_status(result: dict) -> str:
        if result["candidate_count"] and result["failures"] and not result["link_ids"]:
            return ResearchQualityStatus.FAILED
        if result["failures"]:
            return ResearchQualityStatus.PARTIAL
        return ResearchQualityStatus.COMPLETED

    def _fail_run(self, session: Session, run_id: str, reason: str) -> dict:
        run = self.quality_service.repository.update_status(session, run_id, ResearchQualityStatus.FAILED, reason)
        return {"run": run, "link_ids": [], "evidence_ids": [], "evidence_by_candidate": [], "failures": [{"error": reason}], "candidate_count": 0}

    @staticmethod
    def _require_provider(provider):
        if provider is None:
            raise ValueError("Quality execution provider is required")
        return provider

    @staticmethod
    def _require_candidates(candidates):
        if not isinstance(candidates, list):
            raise ValueError("Provider candidates must be a list")
        return candidates

    @staticmethod
    def _is_reddit_url(url: str) -> bool:
        host = urlparse(url).hostname or ""
        return host.lower() in {"reddit.com", "www.reddit.com", "old.reddit.com"}

    @staticmethod
    def _situation_payload(run: ResearchQualityRun) -> dict | None:
        return {"id": run.situation.id, "title": run.situation.title, "context": run.situation.context, "fpl_relevance": run.situation.fpl_relevance} if run.situation else None

    @staticmethod
    def _evidence_payload(evidence: ResearchEvidence) -> dict:
        return {
            "claim": evidence.claim,
            "claim_type": evidence.claim_type,
            "evidence_type": evidence.evidence_type,
            "published_at": evidence.published_at,
            "observed_at": evidence.observed_at,
            "retrieved_at": evidence.retrieved_at,
            "research_link_id": evidence.research_link_id,
            "source_url": evidence.research_link.original_url if evidence.research_link else None,
        }


class _SupporterOnlyAtomicProvider:
    def __init__(self, delegate: Eval2AtomicEvidenceProvider):
        self.delegate = delegate

    def extract(self, *, prompt: str, prompt_version: str, result):
        output = self.delegate.extract(prompt=prompt, prompt_version=prompt_version, result=result)
        output.evidence[:] = [item for item in output.evidence if item.evidence_type == "supporter_observation"]
        return output


def _stored_utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
