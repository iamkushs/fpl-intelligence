"""Durable research orchestration without worker or Codex execution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.models import (
    ResearchJob,
    ResearchJobStatus,
    ResearchRun,
    ResearchRunStatus,
    ResearchSection,
    ResearchSectionStatus,
)


class ResearchJobLifecycle:
    """Small explicit transition policy for durable job state."""

    ALLOWED_TRANSITIONS = {
        ResearchJobStatus.PENDING: {ResearchJobStatus.READY, ResearchJobStatus.SUPERSEDED},
        ResearchJobStatus.READY: {ResearchJobStatus.RUNNING, ResearchJobStatus.SUPERSEDED},
        ResearchJobStatus.RUNNING: {
            ResearchJobStatus.COMPLETE,
            ResearchJobStatus.COMPLETE_WITH_GAPS,
            ResearchJobStatus.PAUSED_RATE_LIMIT,
            ResearchJobStatus.FAILED_RETRYABLE,
            ResearchJobStatus.FAILED,
            ResearchJobStatus.SUPERSEDED,
        },
        ResearchJobStatus.PAUSED_RATE_LIMIT: {
            ResearchJobStatus.READY,
            ResearchJobStatus.FAILED,
            ResearchJobStatus.SUPERSEDED,
        },
        ResearchJobStatus.FAILED_RETRYABLE: {
            ResearchJobStatus.READY,
            ResearchJobStatus.FAILED,
            ResearchJobStatus.SUPERSEDED,
        },
        ResearchJobStatus.COMPLETE: {ResearchJobStatus.SUPERSEDED},
        ResearchJobStatus.COMPLETE_WITH_GAPS: {ResearchJobStatus.SUPERSEDED},
        ResearchJobStatus.FAILED: {ResearchJobStatus.SUPERSEDED},
        ResearchJobStatus.SUPERSEDED: set(),
    }

    @classmethod
    def transition(cls, job: ResearchJob, new_status: str) -> ResearchJob:
        allowed = cls.ALLOWED_TRANSITIONS.get(job.status, set())
        if new_status not in allowed:
            raise ValueError(f"Invalid ResearchJob status transition: {job.status} -> {new_status}")
        job.status = new_status
        now = datetime.now(timezone.utc)
        if new_status == ResearchJobStatus.RUNNING:
            job.started_at = job.started_at or now
            job.attempt_count += 1
        if new_status in ResearchJobStatus.TERMINAL:
            job.completed_at = job.completed_at or now
        return job


class ResearchRunService:
    """Create and inspect configurable research plans."""

    def create_run(
        self,
        session: Session,
        *,
        season_id: str | None = None,
        gameweek_id: int | None = None,
        mode: str = "STANDARD",
        research_cutoff: datetime | None = None,
        sections: list[dict[str, Any]] | None = None,
    ) -> ResearchRun:
        run = ResearchRun(
            season_id=season_id,
            gameweek_id=gameweek_id,
            mode=mode,
            status=ResearchRunStatus.RUNNING,
            research_cutoff=research_cutoff,
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.flush()

        jobs_by_reference: dict[str, ResearchJob] = {}
        job_specs: list[tuple[ResearchJob, list[str]]] = []
        for section_index, section_spec in enumerate(sections or []):
            title = section_spec.get("title") or section_spec.get("name") or section_spec.get("key")
            if not title or not str(title).strip():
                raise ValueError("Each research section requires a title or name")
            section = ResearchSection(
                research_run_id=run.id,
                key=section_spec.get("key"),
                title=str(title),
                ordering=section_spec.get("ordering", section_spec.get("order", section_index)),
                status=ResearchSectionStatus.READY,
            )
            session.add(section)
            session.flush()
            for job_index, job_spec in enumerate(section_spec.get("jobs") or []):
                subject = (
                    job_spec.get("subject")
                    or job_spec.get("title")
                    or job_spec.get("key")
                )
                question = job_spec.get("question") or job_spec.get("request")
                if not subject or not str(subject).strip():
                    raise ValueError("Each research job requires a subject or title")
                if not question or not str(question).strip():
                    raise ValueError("Each research job requires a research question or request")
                job = ResearchJob(
                    research_run_id=run.id,
                    research_section_id=section.id,
                    key=job_spec.get("key"),
                    subject=str(subject),
                    question=str(question),
                    ordering=job_spec.get("ordering", job_spec.get("order", job_index)),
                    status=ResearchJobStatus.PENDING,
                    model=job_spec.get("model"),
                    reasoning_effort=job_spec.get("reasoning_effort"),
                )
                session.add(job)
                session.flush()
                references = list(job_spec.get("dependencies") or job_spec.get("depends_on") or [])
                job_specs.append((job, references))
                for reference in (job.key, job.id):
                    if reference:
                        if reference in jobs_by_reference:
                            raise ValueError(f"Duplicate research job key: {reference}")
                        jobs_by_reference[reference] = job

        for job, references in job_specs:
            for reference in references:
                dependency = jobs_by_reference.get(reference)
                if dependency is None:
                    raise ValueError(f"Unknown research job dependency: {reference}")
                if dependency is job:
                    raise ValueError("A research job cannot depend on itself")
                job.dependencies.append(dependency)

        session.flush()
        self.refresh_readiness(session, run.id, run=run)
        session.commit()
        session.refresh(run)
        return run

    def get_run(self, session: Session, run_id: str) -> ResearchRun | None:
        statement = (
            select(ResearchRun)
            .where(ResearchRun.id == run_id)
            .options(
                selectinload(ResearchRun.sections).selectinload(ResearchSection.jobs),
                selectinload(ResearchRun.jobs).selectinload(ResearchJob.dependencies),
                selectinload(ResearchRun.jobs).selectinload(ResearchJob.documents),
            )
        )
        return session.scalar(statement)

    def refresh_readiness(
        self, session: Session, run_id: str, *, run: ResearchRun | None = None
    ) -> list[ResearchJob]:
        run = run or self.get_run(session, run_id)
        if run is None:
            raise ValueError("ResearchRun not found")
        if run.status not in {ResearchRunStatus.PENDING, ResearchRunStatus.RUNNING}:
            return list(run.jobs)

        changed: list[ResearchJob] = []
        for job in run.jobs:
            section_eligible = job.research_section.status in {
                ResearchSectionStatus.PENDING,
                ResearchSectionStatus.READY,
                ResearchSectionStatus.RUNNING,
            }
            dependencies_complete = all(
                dependency.status in ResearchJobStatus.SUCCESSFUL_TERMINAL
                for dependency in job.dependencies
            )
            if job.status == ResearchJobStatus.PENDING and section_eligible and dependencies_complete:
                ResearchJobLifecycle.transition(job, ResearchJobStatus.READY)
                changed.append(job)
        session.flush()
        return changed

    def transition_job(self, session: Session, job_id: str, new_status: str) -> ResearchJob:
        job = session.get(ResearchJob, job_id)
        if job is None:
            raise ValueError("ResearchJob not found")
        ResearchJobLifecycle.transition(job, new_status)
        session.commit()
        session.refresh(job)
        return job


class ResearchJobService:
    """Job-focused facade used by future workers and current API tests."""

    def __init__(self, run_service: ResearchRunService | None = None):
        self.run_service = run_service or ResearchRunService()

    def refresh_readiness(self, session: Session, run_id: str) -> list[ResearchJob]:
        return self.run_service.refresh_readiness(session, run_id)

    def transition(self, session: Session, job_id: str, new_status: str) -> ResearchJob:
        return self.run_service.transition_job(session, job_id, new_status)
