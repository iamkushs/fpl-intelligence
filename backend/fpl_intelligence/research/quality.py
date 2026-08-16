from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fpl_intelligence.models import (
    ResearchEvidence,
    ResearchLink,
    MonitoringTrigger,
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
        monitoring_condition: dict | None = None,
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
            monitoring_condition=monitoring_condition if outcome == "unresolved" else None,
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
        monitoring_condition: dict | None = None,
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
            if expected_stage == ResearchQualityStage.FRESHNESS and outcome == "unresolved" and monitoring_condition:
                self._ensure_freshness_monitoring_trigger(session, run, monitoring_condition)
            session.commit()
        except Exception:
            session.rollback()
            raise
        return self.repository.get_run(session, run_id)

    def get_run_detail(self, session: Session, run_id: str) -> dict:
        return quality_run_state(self.repository.get_run_detail(session, run_id))

    def list_runs_for_thread(self, session: Session, thread_id: str) -> list[dict]:
        return [quality_run_state(run) for run in self.repository.list_runs_for_thread(session, thread_id)]

    def list_runs_for_player(self, session: Session, player_id: int) -> list[dict]:
        return [quality_run_state(run) for run in self.repository.list_runs_for_player(session, player_id)]

    @staticmethod
    def _ensure_freshness_monitoring_trigger(session: Session, run: ResearchQualityRun, condition: dict) -> MonitoringTrigger:
        existing = session.scalar(
            select(MonitoringTrigger).where(
                MonitoringTrigger.player_id == run.player_id,
                MonitoringTrigger.category == "freshness",
                MonitoringTrigger.active.is_(True),
            )
        )
        for monitor in session.scalars(
            select(MonitoringTrigger).where(
                MonitoringTrigger.player_id == run.player_id,
                MonitoringTrigger.category == "freshness",
                MonitoringTrigger.active.is_(True),
            )
        ):
            if monitor.condition == condition:
                return monitor
        trigger = MonitoringTrigger(
            player_id=run.player_id,
            research_thread_id=run.thread_id,
            description="Recheck unresolved freshness evidence",
            category="freshness",
            condition=condition,
            active=True,
        )
        session.add(trigger)
        return trigger

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


def quality_run_state(run: ResearchQualityRun) -> dict:
    return {
        "id": run.id,
        "thread_id": run.thread_id,
        "player_id": run.player_id,
        "situation_id": run.situation_id,
        "stage": run.stage,
        "status": run.status,
        "target_evidence_id": run.target_evidence_id,
        "superseding_evidence_id": run.superseding_evidence_id,
        "research_cutoff": run.research_cutoff,
        "prompt_version": run.prompt_version,
        "challenged_claim": run.challenged_claim,
        "questions": run.questions,
        "outcome": run.outcome,
        "failure_reason": run.failure_reason,
        "checked_at": run.checked_at,
        "completed_at": run.completed_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "link_ids": [link.id for link in sorted(run.links, key=lambda item: item.id)],
        "evidence_ids": [evidence.id for evidence in sorted(run.evidence, key=lambda item: item.id)],
    }
