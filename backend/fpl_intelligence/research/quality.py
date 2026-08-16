from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fpl_intelligence.models import (
    ResearchEvidence,
    ResearchLink,
    ResearchQualityRun,
    ResearchQualityStage,
    ResearchQualityStatus,
    research_quality_run_evidence,
    research_quality_run_links,
)
from fpl_intelligence.repositories.research_quality import (
    COUNTER_SEARCH_OUTCOMES,
    FRESHNESS_OUTCOMES,
    ResearchQualityRepository,
)
from fpl_intelligence.research.eval2_prompts import (
    EVAL2_COUNTER_SEARCH_PROMPT_VERSION,
    EVAL2_FRESHNESS_PROMPT_VERSION,
    EVAL2_REDDIT_RESEARCH_PROMPT_VERSION,
)


class ResearchQualityService:
    def __init__(self, repository: ResearchQualityRepository | None = None):
        self.repository = repository or ResearchQualityRepository()

    def start_reddit_run(self, session: Session, *, thread_id: str, player_id: int, research_cutoff: datetime, situation_id: str | None = None) -> ResearchQualityRun:
        return self.repository.create_run(
            session,
            thread_id=thread_id,
            player_id=player_id,
            situation_id=situation_id,
            stage=ResearchQualityStage.REDDIT,
            status=ResearchQualityStatus.RUNNING,
            research_cutoff=research_cutoff,
            prompt_version=EVAL2_REDDIT_RESEARCH_PROMPT_VERSION,
        )

    def start_counter_search_run(
        self,
        session: Session,
        *,
        thread_id: str,
        player_id: int,
        challenged_claim: str,
        research_cutoff: datetime,
        situation_id: str | None = None,
        target_evidence_id: str | None = None,
        questions: list | None = None,
    ) -> ResearchQualityRun:
        return self.repository.create_run(
            session,
            thread_id=thread_id,
            player_id=player_id,
            situation_id=situation_id,
            stage=ResearchQualityStage.COUNTER_SEARCH,
            status=ResearchQualityStatus.RUNNING,
            research_cutoff=research_cutoff,
            prompt_version=EVAL2_COUNTER_SEARCH_PROMPT_VERSION,
            challenged_claim=challenged_claim,
            questions=questions,
            target_evidence_id=target_evidence_id,
        )

    def start_freshness_run(
        self,
        session: Session,
        *,
        thread_id: str,
        player_id: int,
        target_evidence_id: str,
        research_cutoff: datetime,
        situation_id: str | None = None,
    ) -> ResearchQualityRun:
        return self.repository.create_run(
            session,
            thread_id=thread_id,
            player_id=player_id,
            situation_id=situation_id,
            stage=ResearchQualityStage.FRESHNESS,
            status=ResearchQualityStatus.RUNNING,
            research_cutoff=research_cutoff,
            prompt_version=EVAL2_FRESHNESS_PROMPT_VERSION,
            target_evidence_id=target_evidence_id,
        )

    def complete_reddit_run(self, session: Session, *, run_id: str, link_ids: list[str] | None = None, evidence_ids: list[str] | None = None, partial: bool = False) -> ResearchQualityRun:
        return self._complete(session, run_id=run_id, expected_stage=ResearchQualityStage.REDDIT, link_ids=link_ids, evidence_ids=evidence_ids, status=ResearchQualityStatus.PARTIAL if partial else ResearchQualityStatus.COMPLETED)

    def complete_counter_search_run(
        self,
        session: Session,
        *,
        run_id: str,
        outcome: str,
        link_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        partial: bool = False,
    ) -> ResearchQualityRun:
        if outcome not in COUNTER_SEARCH_OUTCOMES:
            raise ValueError("Invalid counter_search outcome")
        return self._complete(session, run_id=run_id, expected_stage=ResearchQualityStage.COUNTER_SEARCH, outcome=outcome, link_ids=link_ids, evidence_ids=evidence_ids, status=ResearchQualityStatus.PARTIAL if partial else ResearchQualityStatus.COMPLETED)

    def complete_freshness_run(
        self,
        session: Session,
        *,
        run_id: str,
        outcome: str,
        link_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        checked_at: datetime | None = None,
        superseding_evidence_id: str | None = None,
        partial: bool = False,
    ) -> ResearchQualityRun:
        if outcome not in FRESHNESS_OUTCOMES:
            raise ValueError("Invalid freshness outcome")
        if outcome == "superseded" and superseding_evidence_id is None:
            raise ValueError("superseded freshness outcome requires superseding_evidence_id")
        return self._complete(
            session,
            run_id=run_id,
            expected_stage=ResearchQualityStage.FRESHNESS,
            outcome=outcome,
            link_ids=link_ids,
            evidence_ids=evidence_ids,
            status=ResearchQualityStatus.PARTIAL if partial else ResearchQualityStatus.COMPLETED,
            checked_at=checked_at or datetime.now(timezone.utc),
            superseding_evidence_id=superseding_evidence_id,
        )

    def _complete(
        self,
        session: Session,
        *,
        run_id: str,
        expected_stage: str,
        status: str,
        outcome: str | None = None,
        link_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        checked_at: datetime | None = None,
        superseding_evidence_id: str | None = None,
    ) -> ResearchQualityRun:
        try:
            run = self.repository.get_run(session, run_id)
            if run.stage != expected_stage:
                raise ValueError("Quality run stage mismatch")
            for link_id in dict.fromkeys(link_ids or []):
                self._require(session, ResearchLink, link_id, "ResearchLink")
                self._insert_idempotent(session, research_quality_run_links, quality_run_id=run_id, research_link_id=link_id)
            for evidence_id in dict.fromkeys(evidence_ids or []):
                self._require(session, ResearchEvidence, evidence_id, "ResearchEvidence")
                self._insert_idempotent(session, research_quality_run_evidence, quality_run_id=run_id, research_evidence_id=evidence_id)
            if superseding_evidence_id is not None:
                self._require(session, ResearchEvidence, superseding_evidence_id, "ResearchEvidence")
            run.outcome = outcome
            run.checked_at = checked_at
            run.superseding_evidence_id = superseding_evidence_id
            run.status = status
            run.completed_at = datetime.now(timezone.utc)
            session.commit()
        except Exception:
            session.rollback()
            raise
        return self.repository.get_run(session, run_id)

    @staticmethod
    def _require(session: Session, model, value, label: str):
        item = session.get(model, value)
        if item is None:
            raise LookupError(f"{label} not found")
        return item

    @staticmethod
    def _insert_idempotent(session: Session, table, **values) -> None:
        exists = session.execute(select(table).where(*(table.c[name] == value for name, value in values.items()))).first()
        if exists is not None:
            return
        try:
            session.execute(insert(table).values(**values))
        except IntegrityError:
            session.rollback()
