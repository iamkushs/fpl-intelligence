from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.models import (
    Player,
    ResearchEvidence,
    ResearchLink,
    ResearchQualityRun,
    ResearchQualityStage,
    ResearchQualityStatus,
    ResearchSituation,
    ResearchThread,
    research_quality_run_evidence,
    research_quality_run_links,
)

COUNTER_SEARCH_OUTCOMES = frozenset({"contradicted", "qualified", "superseded", "unresolved", "no_credible_counter_evidence"})
FRESHNESS_OUTCOMES = frozenset({"still_current", "changed", "unresolved", "superseded"})
QUALITY_STAGES = frozenset({ResearchQualityStage.REDDIT, ResearchQualityStage.COUNTER_SEARCH, ResearchQualityStage.FRESHNESS})
QUALITY_STATUSES = frozenset({
    ResearchQualityStatus.PENDING,
    ResearchQualityStatus.RUNNING,
    ResearchQualityStatus.COMPLETED,
    ResearchQualityStatus.PARTIAL,
    ResearchQualityStatus.FAILED,
})


class ResearchQualityRepository:
    def create_run(
        self,
        session: Session,
        *,
        thread_id: str,
        player_id: int,
        stage: str,
        status: str,
        research_cutoff: datetime,
        prompt_version: str,
        situation_id: str | None = None,
        target_evidence_id: str | None = None,
        challenged_claim: str | None = None,
        questions: list | None = None,
    ) -> ResearchQualityRun:
        self._validate_stage_rules(stage, challenged_claim=challenged_claim, target_evidence_id=target_evidence_id)
        self._require(session, ResearchThread, thread_id, "ResearchThread")
        self._require(session, Player, player_id, "Player")
        if situation_id is not None:
            self._require(session, ResearchSituation, situation_id, "ResearchSituation")
        if target_evidence_id is not None:
            self._require(session, ResearchEvidence, target_evidence_id, "ResearchEvidence")
        run = ResearchQualityRun(
            thread_id=thread_id,
            player_id=player_id,
            situation_id=situation_id,
            stage=stage,
            status=self._validate_status(status),
            target_evidence_id=target_evidence_id,
            research_cutoff=self._require_cutoff(research_cutoff),
            prompt_version=prompt_version,
            challenged_claim=challenged_claim,
            questions=questions,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run

    def get_run(self, session: Session, run_id: str) -> ResearchQualityRun:
        return self._require_run(session, run_id)

    def get_run_detail(self, session: Session, run_id: str) -> ResearchQualityRun:
        return self._require_run(session, run_id)

    def list_runs_for_thread(self, session: Session, thread_id: str) -> list[ResearchQualityRun]:
        self._require(session, ResearchThread, thread_id, "ResearchThread")
        return list(session.scalars(self._list_statement().where(ResearchQualityRun.thread_id == thread_id).order_by(ResearchQualityRun.created_at.desc(), ResearchQualityRun.id.desc())).unique())

    def list_runs_for_player(self, session: Session, player_id: int) -> list[ResearchQualityRun]:
        self._require(session, Player, player_id, "Player")
        return list(session.scalars(self._list_statement().where(ResearchQualityRun.player_id == player_id).order_by(ResearchQualityRun.created_at.desc(), ResearchQualityRun.id.desc())).unique())

    def update_status(self, session: Session, run_id: str, status: str, failure_reason: str | None = None) -> ResearchQualityRun:
        run = self._require_run(session, run_id)
        run.status = self._validate_status(status)
        run.failure_reason = failure_reason
        if status in {ResearchQualityStatus.COMPLETED, ResearchQualityStatus.PARTIAL, ResearchQualityStatus.FAILED}:
            run.completed_at = run.completed_at or datetime.now(timezone.utc)
        session.commit()
        session.refresh(run)
        return run

    def set_outcome(
        self,
        session: Session,
        run_id: str,
        outcome: str,
        checked_at: datetime | None = None,
        superseding_evidence_id: str | None = None,
    ) -> ResearchQualityRun:
        run = self._require_run(session, run_id)
        self._validate_outcome(run.stage, outcome)
        if superseding_evidence_id is not None:
            self._require(session, ResearchEvidence, superseding_evidence_id, "ResearchEvidence")
        run.outcome = outcome
        run.checked_at = checked_at
        run.superseding_evidence_id = superseding_evidence_id
        session.commit()
        session.refresh(run)
        return run

    def attach_link(self, session: Session, run_id: str, research_link_id: str) -> None:
        self._require_run(session, run_id)
        self._require(session, ResearchLink, research_link_id, "ResearchLink")
        self._insert_idempotent(session, research_quality_run_links, quality_run_id=run_id, research_link_id=research_link_id)

    def attach_evidence(self, session: Session, run_id: str, research_evidence_id: str) -> None:
        self._require_run(session, run_id)
        self._require(session, ResearchEvidence, research_evidence_id, "ResearchEvidence")
        self._insert_idempotent(session, research_quality_run_evidence, quality_run_id=run_id, research_evidence_id=research_evidence_id)

    def _require_run(self, session: Session, run_id: str) -> ResearchQualityRun:
        run = session.scalar(self._list_statement().where(ResearchQualityRun.id == run_id))
        if run is None:
            raise LookupError("ResearchQualityRun not found")
        return run

    @staticmethod
    def _list_statement():
        return select(ResearchQualityRun).options(selectinload(ResearchQualityRun.links), selectinload(ResearchQualityRun.evidence))

    @staticmethod
    def _require(session: Session, model, value, label: str):
        item = session.get(model, value)
        if item is None:
            raise LookupError(f"{label} not found")
        return item

    @staticmethod
    def _require_cutoff(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("research_cutoff must include timezone information")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_status(status: str) -> str:
        if status not in QUALITY_STATUSES:
            raise ValueError("Unknown quality run status")
        return status

    @staticmethod
    def _validate_stage_rules(stage: str, *, challenged_claim: str | None, target_evidence_id: str | None) -> None:
        if stage not in QUALITY_STAGES:
            raise ValueError("Unknown quality run stage")
        if stage == ResearchQualityStage.COUNTER_SEARCH and not challenged_claim:
            raise ValueError("counter_search requires challenged_claim")
        if stage == ResearchQualityStage.FRESHNESS and target_evidence_id is None:
            raise ValueError("freshness requires target_evidence_id")

    @staticmethod
    def _validate_outcome(stage: str, outcome: str) -> None:
        if stage == ResearchQualityStage.COUNTER_SEARCH and outcome not in COUNTER_SEARCH_OUTCOMES:
            raise ValueError("Invalid counter_search outcome")
        if stage == ResearchQualityStage.FRESHNESS and outcome not in FRESHNESS_OUTCOMES:
            raise ValueError("Invalid freshness outcome")
        if stage == ResearchQualityStage.REDDIT and outcome:
            raise ValueError("reddit outcome must be null")

    @staticmethod
    def _insert_idempotent(session: Session, table, **values) -> None:
        try:
            session.execute(insert(table).values(**values))
            session.commit()
        except IntegrityError:
            session.rollback()
