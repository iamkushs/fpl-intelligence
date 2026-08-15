"""Persistence operations for the durable research hierarchy."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.models import ResearchJob, ResearchRun, ResearchSection


class ResearchRunRepository:
    def create(self, session: Session, **values) -> ResearchRun:
        run = ResearchRun(**values)
        session.add(run)
        session.commit()
        session.refresh(run)
        return run

    def get_by_id(self, session: Session, run_id: str) -> ResearchRun | None:
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
