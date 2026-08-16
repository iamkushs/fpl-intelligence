"""Eval 2 source discovery and atomic evidence extraction foundation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.models import (
    EvidenceRelationshipType,
    Player,
    PlayerResearchTrigger,
    ResearchDiscoveryExecution,
    ResearchDiscoveryPhase,
    ResearchDiscoveryStatus,
    ResearchEvidence,
    ResearchLink,
    ResearchLinkStatus,
    ResearchPageResearchAttempt,
    ResearchPageResearchAttemptStatus,
    ResearchResult,
    ResearchSituation,
    ResearchSourceCandidate,
    ResearchSourceCandidateStatus,
    ResearchSourceClusterMembership,
    ResearchThread,
    ResearchThreadType,
    SourceClusterMembershipType,
)
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.research.eval2_prompts import (
    EVAL2_ATOMIC_EXTRACTION_PROMPT_VERSION,
    EVAL2_PAGE_RESEARCH_PROMPT_VERSION,
    EVAL2_SOURCE_DISCOVERY_PROMPT_VERSION,
    atomic_extraction_prompt,
    discovery_prompt,
    page_research_prompt,
)
from fpl_intelligence.research.evidence import CLAIM_TYPES, EVIDENCE_TYPES, LINEAGE_TYPES, ResearchEvidenceService
from fpl_intelligence.research.persistence import (
    DuplicateResearchLinkError,
    ResearchPersistenceService,
    canonicalize_url,
)
from fpl_intelligence.research.two_stage import PageRetriever, PlayerResolver, ResearchExtraction, _json_object


SOURCE_CATEGORIES = {
    "official_primary",
    "specialist_direct",
    "reputable_media",
    "supporter_reddit",
    "credible_general",
}
LEVELS = {"high", "medium", "low"}


@dataclass(frozen=True)
class SourceCandidatePayload:
    url: str
    target_dimensions: list[str]
    usefulness: str
    source_category: str
    expected_relevance: str
    source: str | None = None
    publisher: str | None = None
    title: str | None = None
    published_at: datetime | None = None
    recency: str | None = None
    lineage_type: str = SourceClusterMembershipType.UNCLEAR
    lineage_notes: str | None = None
    query: str | None = None
    provenance: dict | None = None


@dataclass(frozen=True)
class DiscoveryOutput:
    candidates: list[SourceCandidatePayload] = field(default_factory=list)
    known_gaps: list[str] = field(default_factory=list)
    model_id: str | None = None


@dataclass(frozen=True)
class AtomicEvidencePayload:
    claim: str
    claim_type: str
    evidence_type: str
    player_ids: list[int]
    reliability: str
    relevance: str
    published_at: datetime | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime | None = None
    season: str | None = None
    is_volatile: bool | None = None
    source_cluster_id: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class EvidenceRelationPayload:
    relation_type: str
    rationale: str | None = None
    from_index: int | None = None
    to_index: int | None = None
    from_evidence_id: str | None = None
    to_evidence_id: str | None = None


@dataclass(frozen=True)
class HypothesisRelationPayload:
    evidence_index: int
    hypothesis_id: str
    relationship_type: str
    rationale: str | None = None


@dataclass(frozen=True)
class EvidenceExtractionOutput:
    evidence: list[AtomicEvidencePayload] = field(default_factory=list)
    relationships: list[EvidenceRelationPayload] = field(default_factory=list)
    hypothesis_relationships: list[HypothesisRelationPayload] = field(default_factory=list)
    model_id: str | None = None


class Eval2DiscoveryProvider(Protocol):
    def discover(self, *, prompt: str, prompt_version: str, phase: str) -> DiscoveryOutput: ...


class Eval2PageResearchProvider(Protocol):
    def extract(self, *, prompt: str, prompt_version: str, thread, link, page_content: str) -> ResearchExtraction: ...


class Eval2AtomicEvidenceProvider(Protocol):
    def extract(self, *, prompt: str, prompt_version: str, result) -> EvidenceExtractionOutput: ...


class CodexEval2DiscoveryProvider:
    def __init__(self, codex: CodexService):
        self.codex = codex

    def discover(self, *, prompt: str, prompt_version: str, phase: str) -> DiscoveryOutput:
        response = self.codex.execute(prompt=prompt)
        payload = _json_object(response.final_text)
        candidates = _required_list(payload, "candidates")
        known_gaps = _required_list(payload, "known_gaps")
        return DiscoveryOutput(
            candidates=[_candidate_payload(_required_object(item, "candidate")) for item in candidates],
            known_gaps=[_required_string(item, "known_gaps entry") for item in known_gaps],
            model_id=_response_model_id(response),
        )


class CodexEval2PageResearchProvider:
    def __init__(self, codex: CodexService):
        self.codex = codex

    def extract(self, *, prompt: str, prompt_version: str, thread, link, page_content: str) -> ResearchExtraction:
        response = self.codex.execute(prompt=prompt)
        payload = _json_object(response.final_text)
        extraction = ResearchExtraction(
            summary=_required_string(payload.get("summary"), "summary").strip(),
            findings=_required_string(payload.get("findings"), "findings").strip(),
            evidence=_required_string(payload.get("evidence"), "evidence").strip(),
            uncertainties=_optional_string(payload.get("uncertainties"), "uncertainties"),
            referenced_players=[_required_string(name, "referenced_players entry") for name in _required_list(payload, "referenced_players")],
        )
        if not extraction.summary or not extraction.findings or not extraction.evidence:
            raise ValueError("Page research omitted required substantive fields")
        model_id = _response_model_id(response)
        if model_id:
            link.discovery_metadata = {**(link.discovery_metadata or {}), "page_research_model_id": model_id}
        return extraction


class CodexEval2AtomicEvidenceProvider:
    def __init__(self, codex: CodexService):
        self.codex = codex

    def extract(self, *, prompt: str, prompt_version: str, result) -> EvidenceExtractionOutput:
        response = self.codex.execute(prompt=prompt)
        payload = _json_object(response.final_text)
        evidence = _required_list(payload, "evidence")
        relationships = _required_list(payload, "relationships")
        hypothesis_relationships = _required_list(payload, "hypothesis_relationships")
        return EvidenceExtractionOutput(
            evidence=[_atomic_payload(_required_object(item, "evidence")) for item in evidence],
            relationships=[_relation_payload(_required_object(item, "relationship")) for item in relationships],
            hypothesis_relationships=[_hypothesis_relation_payload(_required_object(item, "hypothesis_relationship")) for item in hypothesis_relationships],
            model_id=_response_model_id(response),
        )


class Eval2SourceDiscoveryService:
    def __init__(
        self,
        *,
        discovery_provider: Eval2DiscoveryProvider,
        retriever: PageRetriever,
        page_research_provider: Eval2PageResearchProvider,
        atomic_provider: Eval2AtomicEvidenceProvider,
        persistence: ResearchPersistenceService | None = None,
        evidence_service: ResearchEvidenceService | None = None,
    ):
        self.discovery_provider = discovery_provider
        self.retriever = retriever
        self.page_research_provider = page_research_provider
        self.atomic_provider = atomic_provider
        self.persistence = persistence or ResearchPersistenceService()
        self.evidence_service = evidence_service or ResearchEvidenceService()

    def start_player_discovery(
        self,
        session: Session,
        *,
        player_id: int,
        research_cutoff: datetime,
        thread_id: str | None = None,
        situation_id: str | None = None,
        trigger_id: str | None = None,
        gameweek_id: int | None = None,
        target_gameweek_id: int | None = None,
        known_missing_dimensions: list[str] | None = None,
        durable_context: dict | None = None,
    ) -> dict:
        cutoff = _require_cutoff(research_cutoff)
        player = self._require_player(session, player_id)
        situation = self._require_situation(session, situation_id) if situation_id else None
        trigger = self._require_trigger(session, trigger_id) if trigger_id else None
        if trigger and trigger.player_id != player.id:
            raise ValueError("Trigger must belong to the researched Player")
        if situation and player.id not in {item.id for item in situation.players}:
            raise ValueError("ResearchSituation must include the researched Player")
        if thread_id:
            thread = self._require_thread(session, thread_id)
            if situation and thread.situation_id not in (None, situation.id):
                raise ValueError("ResearchThread has incompatible ResearchSituation")
            if situation and thread.situation_id is None:
                thread.situation_id = situation.id
                session.commit()
        else:
            thread = self.persistence.create_thread(
                session,
                title=f"Eval 2 source discovery: Player {player.id}",
                thread_type=ResearchThreadType.PLAYER,
                gameweek_id=gameweek_id,
                question=f"Discover FPL-relevant sources for Player {player.id}.",
            )
            if situation:
                thread.situation_id = situation.id
                session.commit()
        execution = ResearchDiscoveryExecution(
            research_thread_id=thread.id,
            player_id=player.id,
            research_situation_id=situation.id if situation else None,
            trigger_id=trigger.id if trigger else None,
            gameweek_id=gameweek_id,
            target_gameweek_id=target_gameweek_id,
            research_cutoff=cutoff,
            discovery_prompt_version=EVAL2_SOURCE_DISCOVERY_PROMPT_VERSION,
            page_research_prompt_version=EVAL2_PAGE_RESEARCH_PROMPT_VERSION,
            extraction_prompt_version=EVAL2_ATOMIC_EXTRACTION_PROMPT_VERSION,
            status=ResearchDiscoveryStatus.RUNNING,
            known_missing_dimensions=known_missing_dimensions or [],
            durable_context=durable_context or {},
        )
        session.add(execution)
        session.commit()
        session.refresh(execution)
        failures: list[dict] = []
        broad = self._run_discovery_phase(
            session,
            execution=execution,
            phase=ResearchDiscoveryPhase.BROAD,
            known_missing_dimensions=known_missing_dimensions or [],
            durable_context=durable_context or {},
            failures=failures,
        )
        targeted_dimensions = list(dict.fromkeys([*(known_missing_dimensions or []), *broad.known_gaps]))
        self._run_discovery_phase(
            session,
            execution=execution,
            phase=ResearchDiscoveryPhase.TARGETED,
            known_missing_dimensions=targeted_dimensions,
            durable_context=durable_context or {},
            failures=failures,
        )
        candidates = self.list_execution_candidates(session, execution.id)
        collected = [item for item in candidates if item.status in {ResearchSourceCandidateStatus.COLLECTED, ResearchSourceCandidateStatus.DUPLICATE}]
        execution.status = ResearchDiscoveryStatus.PARTIAL if collected else ResearchDiscoveryStatus.FAILED
        execution.failure_reason = json.dumps(failures, sort_keys=True) if failures else None
        execution.completed_at = datetime.now(timezone.utc)
        session.commit()
        self._refresh_execution_status_for_thread(session, thread.id)
        return self.execution_state(session, execution.id)

    def discover_for_thread(self, session: Session, *, thread_id: str, player_id: int, research_cutoff: datetime, **kwargs) -> dict:
        return self.start_player_discovery(session, player_id=player_id, thread_id=thread_id, research_cutoff=research_cutoff, **kwargs)

    def research_link(
        self,
        session: Session,
        *,
        link_id: str,
        player_resolver: PlayerResolver,
        research_cutoff: datetime,
        target_dimensions: list[str] | None = None,
        situation_id: str | None = None,
        trigger_id: str | None = None,
        durable_context: dict | None = None,
        retry_failed: bool = False,
    ) -> dict:
        cutoff = _require_cutoff(research_cutoff)
        link = self._require_link(session, link_id)
        matching_results = _matching_results(link, cutoff, EVAL2_PAGE_RESEARCH_PROMPT_VERSION)
        if matching_results:
            return {"researched": 0, "failed": 0, "skipped": 1, "result_ids": [result.id for result in matching_results], "failures": []}
        failed_attempt = _matching_attempt(link, cutoff, EVAL2_PAGE_RESEARCH_PROMPT_VERSION)
        if failed_attempt is not None and failed_attempt.status == ResearchPageResearchAttemptStatus.FAILED and not retry_failed:
            return {"researched": 0, "failed": 0, "skipped": 1, "result_ids": [], "failures": [{"link_id": link.id, "error": failed_attempt.failure_reason or "Previous page research failed"}]}
        if link.status == ResearchLinkStatus.FAILED and retry_failed:
            if link.failure_reason:
                history = list((link.discovery_metadata or {}).get("failure_history", []))
                history.append({"stage": "page_research", "error": link.failure_reason, "retried_at": datetime.now(timezone.utc).isoformat()})
                link.discovery_metadata = {**(link.discovery_metadata or {}), "failure_history": history}
            link.status = ResearchLinkStatus.COLLECTED
            link.failure_reason = None
            session.commit()
            link = self._require_link(session, link_id)
        if link.status not in {ResearchLinkStatus.COLLECTED, ResearchLinkStatus.RESEARCHED, ResearchLinkStatus.FAILED}:
            return {"researched": 0, "failed": 0, "skipped": 1, "result_ids": [], "failures": [{"link_id": link.id, "error": f"Link state is {link.status}, expected collected"}]}
        thread = self._require_thread(session, link.research_thread_id)
        player_payload = _primary_player_payload(link.players)
        situation = self._require_situation(session, situation_id) if situation_id else thread.situation
        trigger = self._require_trigger(session, trigger_id) if trigger_id else None
        try:
            page = self.retriever.retrieve(link.original_url)
            envelope = page_research_prompt(
                player_payload=player_payload,
                situation_payload=_situation_payload(situation),
                trigger_payload=_trigger_payload(trigger),
                link_payload=_link_payload(link),
                page_content=page,
                research_cutoff=cutoff.isoformat(),
                target_dimensions=target_dimensions or _candidate_dimensions(session, link.id),
                durable_context=durable_context or {},
            )
            extraction = self.page_research_provider.extract(
                prompt=envelope.prompt,
                prompt_version=envelope.version,
                thread=thread,
                link=link,
                page_content=page,
            )
            resolved = player_resolver.resolve(session, extraction.referenced_players)
            combined = list(dict.fromkeys([*(player.id for player in link.players), *resolved]))
            if resolved:
                self.persistence.associate_link_with_players(session, link, resolved)
            result = self.persistence.persist_result(
                session,
                link=link,
                summary=extraction.summary,
                findings=extraction.findings,
                evidence=extraction.evidence,
                uncertainty=extraction.uncertainties,
                player_ids=combined,
                prompt_version=envelope.version,
                research_cutoff=cutoff,
                source_metadata={
                    "target_dimensions": target_dimensions or _candidate_dimensions(session, link.id),
                    "page_research_model_id": (link.discovery_metadata or {}).get("page_research_model_id"),
                },
            )
            self._upsert_page_research_attempt(
                session,
                link=link,
                cutoff=cutoff,
                prompt_version=envelope.version,
                status=ResearchPageResearchAttemptStatus.RESEARCHED,
                result_id=result.id,
                failure_reason=None,
                page_research_model_id=(link.discovery_metadata or {}).get("page_research_model_id"),
                model_metadata={"page_research_model_id": (link.discovery_metadata or {}).get("page_research_model_id")},
            )
            self._refresh_execution_status_for_thread(session, link.research_thread_id)
            return {"researched": 1, "failed": 0, "skipped": 0, "result_ids": [result.id], "failures": []}
        except Exception as exc:
            session.rollback()
            failed_link = session.get(ResearchLink, link_id)
            if failed_link is not None:
                matching_results = _matching_results(failed_link, cutoff, EVAL2_PAGE_RESEARCH_PROMPT_VERSION)
                if matching_results:
                    failed_link.status = ResearchLinkStatus.RESEARCHED
                    failed_link.failure_reason = None
                    session.commit()
                    return {"researched": 0, "failed": 0, "skipped": 1, "result_ids": [result.id for result in matching_results], "failures": []}
                if failed_link.results:
                    failed_link.status = ResearchLinkStatus.RESEARCHED
                    failed_link.failure_reason = None
                    session.commit()
                    self._record_page_research_failure(session, link=failed_link, cutoff=cutoff, error=str(exc))
                else:
                    failed_link.status = ResearchLinkStatus.FAILED
                    failed_link.failure_reason = str(exc)
                    session.commit()
                    self._record_page_research_failure(session, link=failed_link, cutoff=cutoff, error=str(exc))
            return {"researched": 0, "failed": 1, "skipped": 0, "result_ids": [], "failures": [{"link_id": link_id, "error": str(exc)}]}

    def extract_atomic_evidence(
        self,
        session: Session,
        *,
        result_id: str,
        research_cutoff: datetime,
        situation_id: str | None = None,
        trigger_id: str | None = None,
        durable_context: dict | None = None,
    ) -> dict:
        cutoff = _require_cutoff(research_cutoff)
        result = self._require_result(session, result_id)
        if result.research_cutoff is not None and _normalize_stored_instant(result.research_cutoff) != cutoff:
            raise ValueError("Extraction research_cutoff must match the ResearchResult cutoff")
        link = result.research_link
        situation = self._require_situation(session, situation_id) if situation_id else result.thread.situation
        trigger = self._require_trigger(session, trigger_id) if trigger_id else None
        try:
            player_payload = _primary_player_payload(result.players or link.players)
            envelope = atomic_extraction_prompt(
                player_payload=player_payload,
                situation_payload=_situation_payload(situation),
                trigger_payload=_trigger_payload(trigger),
                result_payload=_result_payload(result),
                research_cutoff=cutoff.isoformat(),
                durable_context=durable_context or {},
            )
            output = self.atomic_provider.extract(prompt=envelope.prompt, prompt_version=envelope.version, result=result)
            self._validate_evidence_units(session, output.evidence, cutoff)
            self._validate_relationships(session, result=result, situation=situation, output=output)
            created_by_index: dict[int, ResearchEvidence] = {}
            skipped_post_cutoff = 0
            reused = 0
            created = 0
            for index, item in enumerate(output.evidence):
                if _after_cutoff(item.published_at, cutoff) or _after_cutoff(item.observed_at, cutoff) or _after_cutoff(item.retrieved_at, cutoff):
                    skipped_post_cutoff += 1
                    continue
                fingerprint = _evidence_fingerprint(result.id, envelope.version, item)
                existing = self._existing_extracted_evidence(session, result.id, envelope.version, fingerprint)
                if existing is not None:
                    created_by_index[index] = existing
                    reused += 1
                    continue
                cluster_id = item.source_cluster_id or _single_cluster_for_link(session, link.id)
                evidence = self.evidence_service.create_evidence(
                    session,
                    research_thread_id=result.research_thread_id,
                    research_situation_id=situation.id if situation else None,
                    research_link_id=link.id,
                    research_result_id=result.id,
                    source_cluster_id=cluster_id,
                    claim=item.claim,
                    claim_type=item.claim_type,
                    evidence_type=item.evidence_type,
                    player_ids=item.player_ids,
                    published_at=item.published_at,
                    observed_at=item.observed_at,
                    retrieved_at=item.retrieved_at,
                    season=item.season,
                    reliability=item.reliability,
                    relevance=item.relevance,
                    is_volatile=item.is_volatile,
                    notes=item.notes,
                    extraction_prompt_version=envelope.version,
                    extraction_fingerprint=fingerprint,
                    commit=False,
                )
                created_by_index[index] = evidence
                created += 1
            relations = self._create_relations(session, output.relationships, created_by_index, result=result, situation=situation)
            hypothesis_relations = self._create_hypothesis_relations(session, output.hypothesis_relationships, created_by_index, situation=situation)
            result.source_metadata = {
                **(result.source_metadata or {}),
                "extraction_status": "complete",
                "extraction_prompt_version": envelope.version,
                "extraction_model_id": output.model_id,
                "extraction_failure": None,
            }
            session.commit()
            self._refresh_execution_status_for_thread(session, result.research_thread_id)
            return {
                "result_id": result.id,
                "extraction_prompt_version": envelope.version,
                "created": created,
                "reused": reused,
                "skipped_post_cutoff": skipped_post_cutoff,
                "evidence_ids": [item.id for item in created_by_index.values()],
                "relations": relations,
                "hypothesis_relations": hypothesis_relations,
            }
        except Exception as exc:
            session.rollback()
            failed = session.get(ResearchResult, result_id)
            if failed is not None:
                failed.source_metadata = {
                    **(failed.source_metadata or {}),
                    "extraction_status": "failed",
                    "extraction_failure": str(exc),
                }
                session.commit()
                self._refresh_execution_status_for_thread(session, failed.research_thread_id)
            raise

    def execution_state(self, session: Session, execution_id: str) -> dict:
        execution = session.scalar(
            select(ResearchDiscoveryExecution)
            .where(ResearchDiscoveryExecution.id == execution_id)
            .options(
                selectinload(ResearchDiscoveryExecution.candidates).selectinload(ResearchSourceCandidate.research_link).selectinload(ResearchLink.page_research_attempts),
                selectinload(ResearchDiscoveryExecution.candidates).selectinload(ResearchSourceCandidate.research_link).selectinload(ResearchLink.results),
                selectinload(ResearchDiscoveryExecution.thread).selectinload(ResearchThread.links).selectinload(ResearchLink.results),
                selectinload(ResearchDiscoveryExecution.thread).selectinload(ResearchThread.links).selectinload(ResearchLink.page_research_attempts),
            )
        )
        if execution is None:
            raise LookupError("ResearchDiscoveryExecution not found")
        links = _execution_links(execution)
        evidence_by_result = self._evidence_by_result(session, [result.id for link in links for result in link.results])
        return self._execution_state(execution, evidence_by_result)

    def thread_execution_state(self, session: Session, thread_id: str) -> dict:
        thread = session.scalar(
            select(ResearchThread)
            .where(ResearchThread.id == thread_id)
            .options(
                selectinload(ResearchThread.discovery_executions).selectinload(ResearchDiscoveryExecution.candidates).selectinload(ResearchSourceCandidate.research_link).selectinload(ResearchLink.results),
                selectinload(ResearchThread.discovery_executions).selectinload(ResearchDiscoveryExecution.candidates).selectinload(ResearchSourceCandidate.research_link).selectinload(ResearchLink.page_research_attempts),
                selectinload(ResearchThread.links).selectinload(ResearchLink.results),
                selectinload(ResearchThread.links).selectinload(ResearchLink.page_research_attempts),
            )
        )
        if thread is None:
            raise LookupError("ResearchThread not found")
        result_ids = [result.id for link in thread.links for result in link.results]
        evidence_by_result = self._evidence_by_result(session, result_ids)
        return {
            "research_thread_id": thread.id,
            "status": thread.status,
            "executions": [self._execution_state(execution, evidence_by_result) for execution in thread.discovery_executions],
            "links": [_link_state(link, evidence_by_result) for link in thread.links],
        }

    def _execution_state(self, execution: ResearchDiscoveryExecution, evidence_by_result: dict[str, list[ResearchEvidence]]) -> dict:
        scoped_links = _execution_links(execution)
        scoped_result_ids = {result.id for link in scoped_links for result in _execution_results_for_link(execution, link)}
        scoped_evidence = {
            result_id: items
            for result_id, items in evidence_by_result.items()
            if result_id in scoped_result_ids
        }
        links = [_link_state(item, scoped_evidence, execution=execution) for item in scoped_links]
        return {
            "id": execution.id,
            "research_thread_id": execution.research_thread_id,
            "player_id": execution.player_id,
            "research_situation_id": execution.research_situation_id,
            "trigger_id": execution.trigger_id,
            "gameweek_id": execution.gameweek_id,
            "target_gameweek_id": execution.target_gameweek_id,
            "research_cutoff": execution.research_cutoff,
            "discovery_prompt_version": execution.discovery_prompt_version,
            "page_research_prompt_version": execution.page_research_prompt_version,
            "extraction_prompt_version": execution.extraction_prompt_version,
            "model_metadata": execution.model_metadata or {},
            "status": execution.status,
            "failure_reason": execution.failure_reason,
            "candidate_count": len(execution.candidates),
            "candidates": [_candidate_state(item) for item in execution.candidates],
            "links": links,
            "evidence_ids": [evidence.id for items in scoped_evidence.values() for evidence in items],
        }

    @staticmethod
    def _evidence_by_result(session: Session, result_ids: list[str]) -> dict[str, list[ResearchEvidence]]:
        if not result_ids:
            return {}
        evidence = list(session.scalars(select(ResearchEvidence).where(ResearchEvidence.research_result_id.in_(set(result_ids))).options(selectinload(ResearchEvidence.players))).unique())
        grouped = {result_id: [] for result_id in result_ids}
        for item in evidence:
            grouped.setdefault(item.research_result_id, []).append(item)
        return grouped

    def list_execution_candidates(self, session: Session, execution_id: str) -> list[ResearchSourceCandidate]:
        return list(session.scalars(select(ResearchSourceCandidate).where(ResearchSourceCandidate.discovery_execution_id == execution_id)))

    def _run_discovery_phase(
        self,
        session: Session,
        *,
        execution: ResearchDiscoveryExecution,
        phase: str,
        known_missing_dimensions: list[str],
        durable_context: dict,
        failures: list[dict],
    ) -> DiscoveryOutput:
        existing = [_candidate_state(item) for item in self.list_execution_candidates(session, execution.id)]
        envelope = discovery_prompt(
            phase=phase,
            player_payload={"id": execution.player_id},
            situation_payload=_situation_payload(execution.situation),
            trigger_payload=_trigger_payload(execution.trigger),
            gameweek_id=execution.gameweek_id,
            target_gameweek_id=execution.target_gameweek_id,
            research_cutoff=execution.research_cutoff.isoformat(),
            known_missing_dimensions=known_missing_dimensions,
            durable_context=durable_context,
            existing_candidates=existing,
        )
        try:
            output = self.discovery_provider.discover(prompt=envelope.prompt, prompt_version=envelope.version, phase=phase)
            if output.model_id:
                execution.model_metadata = {
                    **(execution.model_metadata or {}),
                    f"{phase}_model_id": output.model_id,
                }
                session.commit()
        except Exception as exc:
            failures.append({"phase": phase, "error": str(exc)})
            return DiscoveryOutput()
        for item in output.candidates:
            try:
                self._persist_candidate(session, execution=execution, phase=phase, prompt_version=envelope.version, item=item)
            except Exception as exc:
                session.rollback()
                failures.append({"phase": phase, "url": item.url, "error": str(exc)})
        return output

    def _persist_candidate(self, session: Session, *, execution: ResearchDiscoveryExecution, phase: str, prompt_version: str, item: SourceCandidatePayload) -> ResearchSourceCandidate:
        self._validate_candidate(item)
        if _after_cutoff(item.published_at, execution.research_cutoff):
            raise ValueError("Candidate publication date is after the research cutoff")
        canonical_url, _ = canonicalize_url(item.url)
        existing_candidate = session.scalar(select(ResearchSourceCandidate).where(
            ResearchSourceCandidate.discovery_execution_id == execution.id,
            ResearchSourceCandidate.canonical_url == canonical_url,
        ))
        if existing_candidate is not None:
            self._record_duplicate_candidate_phase(session, existing_candidate, phase=phase, prompt_version=prompt_version, item=item)
            return existing_candidate
        link = self.persistence.get_link_by_canonical_url(session, thread_id=execution.research_thread_id, canonical_url=canonical_url)
        duplicate_link = link is not None
        if link is None:
            try:
                link = self.persistence.add_collected_link(
                    session,
                    thread_id=execution.research_thread_id,
                    url=item.url,
                    title=item.title,
                    source_type=item.source_category,
                    relevance_reason=item.usefulness,
                    player_ids=[execution.player_id],
                )
            except DuplicateResearchLinkError:
                link = self.persistence.get_link_by_canonical_url(session, thread_id=execution.research_thread_id, canonical_url=canonical_url)
                duplicate_link = True
        if link is None:
            raise ValueError("Could not persist or reuse ResearchLink")
        link.discovery_metadata = {
            **(link.discovery_metadata or {}),
            "latest_discovery_execution_id": execution.id,
            "latest_discovery_phase": phase,
            "latest_discovery_prompt_version": prompt_version,
            "discovery_phases": _append_unique((link.discovery_metadata or {}).get("discovery_phases"), phase),
            "lineage_type": item.lineage_type,
            "lineage_notes": item.lineage_notes,
        }
        candidate = ResearchSourceCandidate(
            discovery_execution_id=execution.id,
            research_thread_id=execution.research_thread_id,
            research_link_id=link.id,
            original_url=item.url.strip(),
            canonical_url=canonical_url,
            source=item.source,
            publisher=item.publisher,
            title=item.title,
            target_dimensions=item.target_dimensions,
            usefulness=item.usefulness.strip(),
            source_category=item.source_category,
            expected_relevance=item.expected_relevance,
            published_at=item.published_at,
            recency=item.recency,
            lineage_type=item.lineage_type,
            lineage_notes=item.lineage_notes,
            query=item.query,
            discovery_phase=phase,
            discovery_prompt_version=prompt_version,
            research_cutoff=execution.research_cutoff,
            status=ResearchSourceCandidateStatus.DUPLICATE if duplicate_link else ResearchSourceCandidateStatus.COLLECTED,
            provenance={
                **(item.provenance or {}),
                "phase": phase,
                "phases": _append_unique((item.provenance or {}).get("phases"), phase),
                "prompt_version": prompt_version,
            },
        )
        try:
            session.add(candidate)
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(select(ResearchSourceCandidate).where(
                ResearchSourceCandidate.discovery_execution_id == execution.id,
                ResearchSourceCandidate.canonical_url == canonical_url,
            ))
            if existing is not None:
                self._record_duplicate_candidate_phase(session, existing, phase=phase, prompt_version=prompt_version, item=item)
            return existing
        return candidate

    @staticmethod
    def _record_duplicate_candidate_phase(session: Session, candidate: ResearchSourceCandidate, *, phase: str, prompt_version: str, item: SourceCandidatePayload) -> None:
        candidate.provenance = {
            **(candidate.provenance or {}),
            "phases": _append_unique((candidate.provenance or {}).get("phases"), phase),
            "latest_phase": phase,
            "latest_prompt_version": prompt_version,
        }
        if item.lineage_notes:
            candidate.lineage_notes = item.lineage_notes
        if candidate.research_link is not None:
            candidate.research_link.discovery_metadata = {
                **(candidate.research_link.discovery_metadata or {}),
                "latest_discovery_phase": phase,
                "latest_discovery_prompt_version": prompt_version,
                "discovery_phases": _append_unique((candidate.research_link.discovery_metadata or {}).get("discovery_phases"), phase),
                "lineage_type": item.lineage_type,
                "lineage_notes": item.lineage_notes,
            }
        session.commit()

    @staticmethod
    def _validate_candidate(item: SourceCandidatePayload) -> None:
        if not item.url.strip():
            raise ValueError("Candidate URL is required")
        if not item.target_dimensions:
            raise ValueError("Candidate target_dimensions are required")
        if not item.usefulness.strip():
            raise ValueError("Candidate usefulness is required")
        if item.source_category not in SOURCE_CATEGORIES:
            raise ValueError("Unknown source category")
        if item.expected_relevance not in LEVELS:
            raise ValueError("Expected relevance must be high, medium, or low")
        if item.lineage_type not in LINEAGE_TYPES:
            raise ValueError("Unknown lineage type")

    def _validate_evidence_units(self, session: Session, units: list[AtomicEvidencePayload], cutoff: datetime) -> None:
        for item in units:
            if not item.claim.strip():
                raise ValueError("Evidence claim is required")
            if item.claim_type not in CLAIM_TYPES:
                raise ValueError("Unknown claim type")
            if item.evidence_type not in EVIDENCE_TYPES:
                raise ValueError("Unknown evidence type")
            if item.reliability not in LEVELS or item.relevance not in LEVELS:
                raise ValueError("Reliability and relevance must be high, medium, or low")
            if not item.player_ids:
                raise ValueError("Evidence must include at least one canonical Player association")
            missing = [player_id for player_id in dict.fromkeys(item.player_ids) if session.get(Player, player_id) is None]
            if missing:
                raise LookupError(f"Player not found: {missing[0]}")
            if item.source_cluster_id and self.evidence_service.get_cluster(session, item.source_cluster_id) is None:
                raise LookupError("ResearchSourceCluster not found")
            if _after_cutoff(item.published_at, cutoff) or _after_cutoff(item.observed_at, cutoff) or _after_cutoff(item.retrieved_at, cutoff):
                continue

    def _existing_extracted_evidence(self, session: Session, result_id: str, prompt_version: str, fingerprint: str) -> ResearchEvidence | None:
        return session.scalar(select(ResearchEvidence).where(
            ResearchEvidence.research_result_id == result_id,
            ResearchEvidence.extraction_prompt_version == prompt_version,
            ResearchEvidence.extraction_fingerprint == fingerprint,
        ).options(selectinload(ResearchEvidence.players)))

    def _validate_relationships(self, session: Session, *, result: ResearchResult, situation, output: EvidenceExtractionOutput) -> None:
        for relation in output.relationships:
            if relation.relation_type not in {EvidenceRelationshipType.SUPPORTS, EvidenceRelationshipType.CONTRADICTS, EvidenceRelationshipType.SUPERSEDES}:
                continue
            if not relation.rationale or not relation.rationale.strip():
                continue
            for evidence_id in (relation.from_evidence_id, relation.to_evidence_id):
                if evidence_id is None:
                    continue
                evidence = session.get(ResearchEvidence, evidence_id)
                if evidence is None:
                    raise LookupError("Related ResearchEvidence not found")
                if evidence.research_thread_id != result.research_thread_id:
                    raise ValueError("Related ResearchEvidence must belong to the result thread")
                if situation and evidence.research_situation_id and evidence.research_situation_id != situation.id:
                    raise ValueError("Related ResearchEvidence has incompatible situation")
        for relation in output.hypothesis_relationships:
            if not relation.rationale or not relation.rationale.strip():
                continue
            hypothesis = self.evidence_service.repository.get_hypothesis(session, relation.hypothesis_id)
            if hypothesis is None:
                raise LookupError("SituationHypothesis not found")
            if situation is None or hypothesis.situation_id != situation.id:
                raise ValueError("Hypothesis relationship must belong to the extraction situation")

    def _create_relations(self, session: Session, relations: list[EvidenceRelationPayload], created_by_index: dict[int, ResearchEvidence], *, result: ResearchResult, situation) -> list[dict]:
        created = []
        for relation in relations:
            if relation.relation_type not in {EvidenceRelationshipType.SUPPORTS, EvidenceRelationshipType.CONTRADICTS, EvidenceRelationshipType.SUPERSEDES}:
                continue
            if not relation.rationale or not relation.rationale.strip():
                continue
            from_id = relation.from_evidence_id or (created_by_index.get(relation.from_index).id if relation.from_index is not None and relation.from_index in created_by_index else None)
            to_id = relation.to_evidence_id or (created_by_index.get(relation.to_index).id if relation.to_index is not None and relation.to_index in created_by_index else None)
            if not from_id or not to_id or from_id == to_id:
                continue
            from_evidence = session.get(ResearchEvidence, from_id)
            to_evidence = session.get(ResearchEvidence, to_id)
            if from_evidence is None or to_evidence is None:
                raise LookupError("Related ResearchEvidence not found")
            if from_evidence.research_thread_id != result.research_thread_id or to_evidence.research_thread_id != result.research_thread_id:
                raise ValueError("Related ResearchEvidence must belong to the result thread")
            if situation and any(item.research_situation_id and item.research_situation_id != situation.id for item in (from_evidence, to_evidence)):
                raise ValueError("Related ResearchEvidence has incompatible situation")
            row = self.evidence_service.add_evidence_relation(session, from_evidence_id=from_id, to_evidence_id=to_id, relation_type=relation.relation_type, rationale=relation.rationale, commit=False)
            created.append({"id": row.id, "from_evidence_id": row.from_evidence_id, "to_evidence_id": row.to_evidence_id, "relation_type": row.relation_type})
        return created

    def _create_hypothesis_relations(self, session: Session, relations: list[HypothesisRelationPayload], created_by_index: dict[int, ResearchEvidence], *, situation) -> list[dict]:
        created = []
        for relation in relations:
            if not relation.rationale or not relation.rationale.strip():
                continue
            evidence = created_by_index.get(relation.evidence_index)
            if evidence is None:
                continue
            hypothesis = self.evidence_service.repository.get_hypothesis(session, relation.hypothesis_id)
            if hypothesis is None:
                raise LookupError("SituationHypothesis not found")
            if situation is None or hypothesis.situation_id != situation.id:
                raise ValueError("Hypothesis relationship must belong to the extraction situation")
            row = self.evidence_service.add_hypothesis_relation(session, evidence_id=evidence.id, hypothesis_id=relation.hypothesis_id, relationship_type=relation.relationship_type, rationale=relation.rationale, commit=False)
            created.append({"id": row.id, "evidence_id": row.evidence_id, "hypothesis_id": row.hypothesis_id, "relationship_type": row.relationship_type})
        return created

    def _refresh_execution_status_for_thread(self, session: Session, thread_id: str) -> None:
        executions = list(session.scalars(
            select(ResearchDiscoveryExecution)
            .where(ResearchDiscoveryExecution.research_thread_id == thread_id)
            .options(
                selectinload(ResearchDiscoveryExecution.candidates).selectinload(ResearchSourceCandidate.research_link).selectinload(ResearchLink.results),
                selectinload(ResearchDiscoveryExecution.candidates).selectinload(ResearchSourceCandidate.research_link).selectinload(ResearchLink.page_research_attempts),
                selectinload(ResearchDiscoveryExecution.thread).selectinload(ResearchThread.links).selectinload(ResearchLink.results),
                selectinload(ResearchDiscoveryExecution.thread).selectinload(ResearchThread.links).selectinload(ResearchLink.page_research_attempts),
            )
        ))
        if not executions:
            return
        result_ids = [result.id for execution in executions for link in _execution_links(execution) for result in _execution_results_for_link(execution, link)]
        evidence_by_result = self._evidence_by_result(session, result_ids)
        for execution in executions:
            links = _execution_links(execution)
            failures = []
            if execution.failure_reason:
                try:
                    for item in json.loads(execution.failure_reason):
                        if item.get("stage") == "extraction":
                            continue
                        if item.get("stage") == "page_research":
                            failed_link = next((link for link in links if link.id == item.get("link_id")), None)
                            if failed_link is not None and _execution_results_for_link(execution, failed_link):
                                continue
                        failures.append(item)
                except (TypeError, ValueError):
                    failures.append({"stage": "discovery", "error": execution.failure_reason})
            failures.extend(
                {"stage": "page_research", "link_id": link.id, "error": attempt.failure_reason}
                for link in links
                for attempt in [_matching_attempt(link, _normalize_stored_instant(execution.research_cutoff), execution.page_research_prompt_version)]
                if attempt is not None
                and attempt.status == ResearchPageResearchAttemptStatus.FAILED
                and not _execution_results_for_link(execution, link)
                and {"stage": "page_research", "link_id": link.id, "error": attempt.failure_reason} not in failures
            )
            failures.extend(
                {"stage": "extraction", "result_id": result.id, "error": (result.source_metadata or {}).get("extraction_failure")}
                for link in links
                for result in _execution_results_for_link(execution, link)
                if (result.source_metadata or {}).get("extraction_status") == "failed"
            )
            researched_links = [link for link in links if _execution_results_for_link(execution, link)]
            completed_results = [
                result for link in researched_links for result in _execution_results_for_link(execution, link)
                if (result.source_metadata or {}).get("extraction_status") == "complete"
            ]
            if not links:
                execution.status = ResearchDiscoveryStatus.FAILED if failures else ResearchDiscoveryStatus.RUNNING
            elif failures:
                execution.status = ResearchDiscoveryStatus.PARTIAL
            elif len(researched_links) == len(links) and all(
                _execution_results_for_link(execution, link)
                and all((result.source_metadata or {}).get("extraction_status") == "complete" for result in _execution_results_for_link(execution, link))
                for link in researched_links
            ):
                execution.status = ResearchDiscoveryStatus.COMPLETE
            elif researched_links or completed_results:
                execution.status = ResearchDiscoveryStatus.PARTIAL
            else:
                execution.status = ResearchDiscoveryStatus.PARTIAL
            execution.failure_reason = json.dumps(failures, sort_keys=True) if failures else None
            if execution.status in {ResearchDiscoveryStatus.COMPLETE, ResearchDiscoveryStatus.PARTIAL, ResearchDiscoveryStatus.FAILED}:
                execution.completed_at = execution.completed_at or datetime.now(timezone.utc)
        session.commit()

    def _record_page_research_failure(self, session: Session, *, link: ResearchLink, cutoff: datetime, error: str) -> None:
        self._upsert_page_research_attempt(
            session,
            link=link,
            cutoff=cutoff,
            prompt_version=EVAL2_PAGE_RESEARCH_PROMPT_VERSION,
            status=ResearchPageResearchAttemptStatus.FAILED,
            result_id=None,
            failure_reason=error,
            page_research_model_id=None,
            model_metadata=None,
        )
        executions = list(session.scalars(
            select(ResearchDiscoveryExecution)
            .join(ResearchSourceCandidate, ResearchSourceCandidate.discovery_execution_id == ResearchDiscoveryExecution.id)
            .where(
                ResearchSourceCandidate.research_link_id == link.id,
            )
        ))
        executions = [execution for execution in executions if _normalize_stored_instant(execution.research_cutoff) == cutoff]
        failure = {"stage": "page_research", "link_id": link.id, "error": error}
        for execution in executions:
            existing = []
            if execution.failure_reason:
                try:
                    existing = list(json.loads(execution.failure_reason))
                except (TypeError, ValueError):
                    existing = [{"stage": "discovery", "error": execution.failure_reason}]
            if failure not in existing:
                existing.append(failure)
            execution.failure_reason = json.dumps(existing, sort_keys=True)
            execution.status = ResearchDiscoveryStatus.PARTIAL
            execution.completed_at = execution.completed_at or datetime.now(timezone.utc)
        session.commit()
        self._refresh_execution_status_for_thread(session, link.research_thread_id)

    def _upsert_page_research_attempt(
        self,
        session: Session,
        *,
        link: ResearchLink,
        cutoff: datetime,
        prompt_version: str,
        status: str,
        result_id: str | None,
        failure_reason: str | None,
        page_research_model_id: str | None,
        model_metadata: dict | None,
    ) -> ResearchPageResearchAttempt:
        attempt = next(
            (
                item
                for item in session.scalars(
                    select(ResearchPageResearchAttempt).where(
                        ResearchPageResearchAttempt.research_link_id == link.id,
                        ResearchPageResearchAttempt.prompt_version == prompt_version,
                    )
                )
                if _normalize_stored_instant(item.research_cutoff) == cutoff
            ),
            None,
        )
        if attempt is None:
            attempt = ResearchPageResearchAttempt(
                research_thread_id=link.research_thread_id,
                research_link_id=link.id,
                prompt_version=prompt_version,
                research_cutoff=cutoff,
                status=status,
            )
            session.add(attempt)
        attempt.research_result_id = result_id
        attempt.status = status
        attempt.failure_reason = failure_reason
        attempt.page_research_model_id = page_research_model_id
        attempt.model_metadata = model_metadata
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            attempt = next(
                item
                for item in session.scalars(
                    select(ResearchPageResearchAttempt).where(
                        ResearchPageResearchAttempt.research_link_id == link.id,
                        ResearchPageResearchAttempt.prompt_version == prompt_version,
                    )
                )
                if _normalize_stored_instant(item.research_cutoff) == cutoff
            )
            attempt.research_result_id = result_id
            attempt.status = status
            attempt.failure_reason = failure_reason
            attempt.page_research_model_id = page_research_model_id
            attempt.model_metadata = model_metadata
            session.commit()
        return attempt

    @staticmethod
    def _require_player(session: Session, player_id: int) -> Player:
        player = session.get(Player, player_id)
        if player is None:
            raise LookupError("Player not found")
        return player

    @staticmethod
    def _require_thread(session: Session, thread_id: str) -> ResearchThread:
        thread = session.get(ResearchThread, thread_id)
        if thread is None:
            raise LookupError("ResearchThread not found")
        return thread

    @staticmethod
    def _require_link(session: Session, link_id: str) -> ResearchLink:
        link = session.scalar(select(ResearchLink).where(ResearchLink.id == link_id).options(
            selectinload(ResearchLink.players),
            selectinload(ResearchLink.results),
            selectinload(ResearchLink.page_research_attempts),
        ))
        if link is None:
            raise LookupError("ResearchLink not found")
        return link

    @staticmethod
    def _require_result(session: Session, result_id: str) -> ResearchResult:
        result = session.scalar(select(ResearchResult).where(ResearchResult.id == result_id).options(
            selectinload(ResearchResult.players),
            selectinload(ResearchResult.thread),
            selectinload(ResearchResult.research_link).selectinload(ResearchLink.players),
        ))
        if result is None:
            raise LookupError("ResearchResult not found")
        return result

    @staticmethod
    def _require_situation(session: Session, situation_id: str) -> ResearchSituation:
        situation = session.scalar(select(ResearchSituation).where(ResearchSituation.id == situation_id).options(selectinload(ResearchSituation.players)))
        if situation is None:
            raise LookupError("ResearchSituation not found")
        return situation

    @staticmethod
    def _require_trigger(session: Session, trigger_id: str) -> PlayerResearchTrigger:
        trigger = session.get(PlayerResearchTrigger, trigger_id)
        if trigger is None:
            raise LookupError("PlayerResearchTrigger not found")
        return trigger


def _require_cutoff(value: datetime) -> datetime:
    if value is None:
        raise ValueError("research_cutoff is required")
    return _normalize_instant(value)


def _after_cutoff(value: datetime | None, cutoff: datetime) -> bool:
    if value is None:
        return False
    return _normalize_instant(value) > _normalize_stored_instant(cutoff)


def _normalize_instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("research_cutoff and compared evidence timestamps must include timezone information")
    return value.astimezone(timezone.utc)


def _normalize_stored_instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _candidate_dimensions(session: Session, link_id: str) -> list[str]:
    dimensions = []
    rows = session.scalars(select(ResearchSourceCandidate).where(ResearchSourceCandidate.research_link_id == link_id))
    for row in rows:
        dimensions.extend(row.target_dimensions or [])
    return list(dict.fromkeys(dimensions))


def _single_cluster_for_link(session: Session, link_id: str) -> str | None:
    memberships = list(session.scalars(select(ResearchSourceClusterMembership).where(ResearchSourceClusterMembership.research_link_id == link_id)))
    if len(memberships) == 1:
        return memberships[0].source_cluster_id
    return None


def _execution_links(execution: ResearchDiscoveryExecution) -> list[ResearchLink]:
    links = []
    seen = set()
    for candidate in execution.candidates:
        link = candidate.research_link
        if link is None or link.id in seen:
            continue
        links.append(link)
        seen.add(link.id)
    return links


def _execution_results_for_link(execution: ResearchDiscoveryExecution, link: ResearchLink) -> list[ResearchResult]:
    return _matching_results(link, _normalize_stored_instant(execution.research_cutoff), execution.page_research_prompt_version)


def _matching_results(link: ResearchLink, cutoff: datetime, prompt_version: str) -> list[ResearchResult]:
    normalized_cutoff = _normalize_stored_instant(cutoff)
    return [
        result
        for result in link.results
        if result.prompt_version == prompt_version
        and result.research_cutoff is not None
        and _normalize_stored_instant(result.research_cutoff) == normalized_cutoff
    ]


def _matching_attempt(link: ResearchLink, cutoff: datetime, prompt_version: str) -> ResearchPageResearchAttempt | None:
    normalized_cutoff = _normalize_stored_instant(cutoff)
    for attempt in link.page_research_attempts:
        if attempt.prompt_version == prompt_version and _normalize_stored_instant(attempt.research_cutoff) == normalized_cutoff:
            return attempt
    return None


def _evidence_fingerprint(result_id: str, prompt_version: str, item: AtomicEvidencePayload) -> str:
    payload = {
        "result_id": result_id,
        "prompt_version": prompt_version,
        "claim": _normalize_text(item.claim),
        "claim_type": item.claim_type,
        "evidence_type": item.evidence_type,
        "player_ids": sorted(set(item.player_ids)),
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "observed_at": item.observed_at.isoformat() if item.observed_at else None,
        "season": item.season,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _normalize_text(value: str) -> str:
    return " ".join(re.sub(r"\s+", " ", value.strip().lower()).split())


def _primary_player_payload(players) -> dict:
    ids = sorted({player.id for player in players})
    return {"id": ids[0] if ids else None, "associated_player_ids": ids}


def _situation_payload(situation) -> dict | None:
    if situation is None:
        return None
    return {"id": situation.id, "title": situation.title, "context": situation.context, "fpl_relevance": situation.fpl_relevance}


def _trigger_payload(trigger) -> dict | None:
    if trigger is None:
        return None
    return {"id": trigger.id, "player_id": trigger.player_id, "trigger_type": trigger.trigger_type, "description": trigger.description, "gameweek": trigger.gameweek}


def _link_payload(link: ResearchLink) -> dict:
    return {
        "id": link.id,
        "url": link.original_url,
        "canonical_url": link.canonical_url,
        "title": link.title,
        "source_type": link.source_type,
        "relevance_reason": link.relevance_reason,
        "status": link.status,
    }


def _result_payload(result: ResearchResult) -> dict:
    return {
        "id": result.id,
        "research_thread_id": result.research_thread_id,
        "research_link_id": result.research_link_id,
        "source_url": result.research_link.original_url,
        "summary": result.summary,
        "findings": result.findings,
        "evidence": result.evidence,
        "uncertainty": result.uncertainty,
        "researched_at": result.researched_at.isoformat(),
        "prompt_version": result.prompt_version,
        "research_cutoff": result.research_cutoff.isoformat() if result.research_cutoff else None,
    }


def _candidate_state(candidate: ResearchSourceCandidate) -> dict:
    return {
        "id": candidate.id,
        "research_link_id": candidate.research_link_id,
        "url": candidate.original_url,
        "canonical_url": candidate.canonical_url,
        "title": candidate.title,
        "target_dimensions": candidate.target_dimensions,
        "source_category": candidate.source_category,
        "expected_relevance": candidate.expected_relevance,
        "lineage_type": candidate.lineage_type,
        "discovery_phase": candidate.discovery_phase,
        "discovery_phases": (candidate.provenance or {}).get("phases") or [candidate.discovery_phase],
        "discovery_prompt_version": candidate.discovery_prompt_version,
        "research_cutoff": candidate.research_cutoff,
        "status": candidate.status,
        "failure_reason": candidate.failure_reason,
    }


def _link_state(link: ResearchLink, evidence_by_result: dict[str, list[ResearchEvidence]] | None = None, *, execution: ResearchDiscoveryExecution | None = None) -> dict:
    evidence_by_result = evidence_by_result or {}
    results = _execution_results_for_link(execution, link) if execution is not None else list(link.results)
    attempt = _matching_attempt(link, _normalize_stored_instant(execution.research_cutoff), execution.page_research_prompt_version) if execution is not None else None
    effective_status = link.status
    effective_failure = link.failure_reason
    if execution is not None:
        if results:
            effective_status = ResearchLinkStatus.RESEARCHED
            effective_failure = None
        elif attempt is not None and attempt.status == ResearchPageResearchAttemptStatus.FAILED:
            effective_status = ResearchLinkStatus.FAILED
            effective_failure = attempt.failure_reason
        else:
            effective_status = ResearchLinkStatus.COLLECTED
            effective_failure = None
    return {
        "id": link.id,
        "url": link.original_url,
        "canonical_url": link.canonical_url,
        "status": effective_status,
        "failure_reason": effective_failure,
        "result_ids": [result.id for result in results],
        "results": [
            {
                "id": result.id,
                "prompt_version": result.prompt_version,
                "research_cutoff": result.research_cutoff,
                "page_research_model_id": (result.source_metadata or {}).get("page_research_model_id"),
                "extraction_status": (result.source_metadata or {}).get("extraction_status"),
                "extraction_model_id": (result.source_metadata or {}).get("extraction_model_id"),
                "extraction_failure": (result.source_metadata or {}).get("extraction_failure"),
                "evidence_ids": [evidence.id for evidence in evidence_by_result.get(result.id, [])],
            }
            for result in results
        ],
    }


def _append_unique(values, value: str) -> list[str]:
    items = [str(item) for item in values] if isinstance(values, list) else []
    if value not in items:
        items.append(value)
    return items


def _required_list(payload: dict, key: str) -> list:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Structured output field {key} must be a list")
    return value


def _required_object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"Structured output {label} entry must be an object")
    return value


def _optional_object(value, label: str) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Structured output field {label} must be an object or null")
    return value


def _required_string(value, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Structured output field {label} must be a string")
    return value


def _optional_string(value, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Structured output field {label} must be a string or null")
    stripped = value.strip()
    return stripped or None


def _required_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Structured output field {label} must be an integer")
    return value


def _optional_bool(value, label: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"Structured output field {label} must be a boolean or null")
    return value


def _response_model_id(response) -> str | None:
    value = getattr(response, "model", None) or getattr(response, "model_id", None)
    return str(value) if value else None


def _parse_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("Structured output datetime fields must be ISO strings or null")
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _candidate_payload(item: dict) -> SourceCandidatePayload:
    return SourceCandidatePayload(
        url=_required_string(item.get("url"), "url").strip(),
        source=_optional_string(item.get("source"), "source"),
        publisher=_optional_string(item.get("publisher"), "publisher"),
        title=_optional_string(item.get("title"), "title"),
        target_dimensions=[_required_string(value, "target_dimensions entry") for value in _required_list(item, "target_dimensions")],
        usefulness=_required_string(item.get("usefulness"), "usefulness").strip(),
        source_category=_required_string(item.get("source_category"), "source_category").strip(),
        expected_relevance=_required_string(item.get("expected_relevance"), "expected_relevance").strip(),
        published_at=_parse_datetime(item.get("published_at")),
        recency=_optional_string(item.get("recency"), "recency"),
        lineage_type=(_optional_string(item.get("lineage_type"), "lineage_type") or SourceClusterMembershipType.UNCLEAR).strip(),
        lineage_notes=_optional_string(item.get("lineage_notes"), "lineage_notes"),
        query=_optional_string(item.get("query"), "query"),
        provenance=_optional_object(item.get("provenance"), "provenance"),
    )


def _atomic_payload(item: dict) -> AtomicEvidencePayload:
    return AtomicEvidencePayload(
        claim=_required_string(item.get("claim"), "claim").strip(),
        claim_type=_required_string(item.get("claim_type"), "claim_type").strip(),
        evidence_type=_required_string(item.get("evidence_type"), "evidence_type").strip(),
        player_ids=[_required_int(value, "player_ids entry") for value in _required_list(item, "player_ids")],
        published_at=_parse_datetime(item.get("published_at")),
        observed_at=_parse_datetime(item.get("observed_at")),
        retrieved_at=_parse_datetime(item.get("retrieved_at")),
        season=_optional_string(item.get("season"), "season"),
        reliability=_required_string(item.get("reliability"), "reliability").strip(),
        relevance=_required_string(item.get("relevance"), "relevance").strip(),
        is_volatile=_optional_bool(item.get("is_volatile"), "is_volatile"),
        source_cluster_id=_optional_string(item.get("source_cluster_id"), "source_cluster_id"),
        notes=_optional_string(item.get("notes"), "notes"),
    )


def _optional_int(item: dict, key: str) -> int | None:
    return _required_int(item[key], key) if item.get(key) is not None else None


def _relation_payload(item: dict) -> EvidenceRelationPayload:
    return EvidenceRelationPayload(
        from_index=_optional_int(item, "from_index"),
        from_evidence_id=_optional_string(item.get("from_evidence_id"), "from_evidence_id"),
        to_index=_optional_int(item, "to_index"),
        to_evidence_id=_optional_string(item.get("to_evidence_id"), "to_evidence_id"),
        relation_type=_required_string(item.get("relation_type"), "relation_type").strip(),
        rationale=_optional_string(item.get("rationale"), "rationale"),
    )


def _hypothesis_relation_payload(item: dict) -> HypothesisRelationPayload:
    return HypothesisRelationPayload(
        evidence_index=_required_int(item.get("evidence_index"), "evidence_index"),
        hypothesis_id=_required_string(item.get("hypothesis_id"), "hypothesis_id").strip(),
        relationship_type=_required_string(item.get("relationship_type"), "relationship_type").strip(),
        rationale=_optional_string(item.get("rationale"), "rationale"),
    )
