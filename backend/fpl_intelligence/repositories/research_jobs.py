"""Persistence operations for ResearchJob."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.models import ResearchJob
from fpl_intelligence.research.service import ResearchJobLifecycle


class ResearchJobRepository:
    def create(self, session: Session, **values) -> ResearchJob:
        job = ResearchJob(**values)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job

    def get_by_id(self, session: Session, job_id: str) -> ResearchJob | None:
        return session.scalar(
            select(ResearchJob)
            .where(ResearchJob.id == job_id)
            .options(selectinload(ResearchJob.dependencies), selectinload(ResearchJob.documents))
        )

    def list_for_run(self, session: Session, run_id: str) -> list[ResearchJob]:
        return list(
            session.scalars(
                select(ResearchJob)
                .where(ResearchJob.research_run_id == run_id)
                .options(selectinload(ResearchJob.dependencies), selectinload(ResearchJob.documents))
                .order_by(ResearchJob.ordering, ResearchJob.created_at)
            )
        )

    def list_for_section(self, session: Session, section_id: str) -> list[ResearchJob]:
        return list(
            session.scalars(
                select(ResearchJob)
                .where(ResearchJob.research_section_id == section_id)
                .options(selectinload(ResearchJob.dependencies), selectinload(ResearchJob.documents))
                .order_by(ResearchJob.ordering, ResearchJob.created_at)
            )
        )

    def update_status(self, session: Session, job_id: str, new_status: str) -> ResearchJob:
        job = self.get_by_id(session, job_id)
        if job is None:
            raise ValueError("ResearchJob not found")
        ResearchJobLifecycle.transition(job, new_status)
        session.commit()
        session.refresh(job)
        return job
