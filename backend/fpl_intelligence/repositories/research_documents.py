"""Persistence operations for ResearchDocument."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from fpl_intelligence.models import ResearchDocument, ResearchJob


class ResearchDocumentRepository:
    def create(self, session: Session, **values) -> ResearchDocument:
        document = ResearchDocument(**values)
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    def get_by_id(self, session: Session, document_id: str) -> ResearchDocument | None:
        return session.scalar(select(ResearchDocument).where(ResearchDocument.id == document_id))

    def create_for_job(self, session: Session, job: ResearchJob, **values) -> ResearchDocument:
        """Persist a document with the complete Run/Section/Job lineage."""
        values.setdefault("research_job_id", job.id)
        values.setdefault("research_run_id", job.research_run_id)
        values.setdefault("research_section_id", job.research_section_id)
        return self.create(session, **values)

    def add_for_job(self, session: Session, job: ResearchJob, **values) -> ResearchDocument:
        """Add a job document to the current transaction without committing it."""
        values.setdefault("research_job_id", job.id)
        values.setdefault("research_run_id", job.research_run_id)
        values.setdefault("research_section_id", job.research_section_id)
        document = ResearchDocument(**values)
        session.add(document)
        return document
