"""Persistence operations for ResearchSection."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from fpl_intelligence.models import ResearchSection


class ResearchSectionRepository:
    def create(self, session: Session, **values) -> ResearchSection:
        section = ResearchSection(**values)
        session.add(section)
        session.commit()
        session.refresh(section)
        return section

    def list_for_run(self, session: Session, run_id: str) -> list[ResearchSection]:
        return list(
            session.scalars(
                select(ResearchSection)
                .where(ResearchSection.research_run_id == run_id)
                .order_by(ResearchSection.ordering, ResearchSection.created_at)
            )
        )
