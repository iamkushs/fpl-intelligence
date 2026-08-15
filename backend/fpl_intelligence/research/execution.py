"""One-shot and durable ResearchJob execution through CodexService."""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.models import ResearchDocument, ResearchJob, ResearchJobStatus
from fpl_intelligence.repositories.research_documents import ResearchDocumentRepository
from fpl_intelligence.repositories.research_jobs import ResearchJobRepository
from fpl_intelligence.research.service import ResearchJobLifecycle, ResearchRunService

logger = logging.getLogger(__name__)


class ResearchJobNotFoundError(LookupError):
    """Raised when a requested durable job does not exist."""


class ResearchJobNotReadyError(ValueError):
    """Raised when a durable job is not eligible for execution."""


class ResearchJobExecutionResult:
    """The durable outputs of one successful job execution."""

    def __init__(self, job: ResearchJob, document: ResearchDocument):
        self.job = job
        self.document = document


class ResearchExecutionService:
    """Execute one bounded request and persist its free-form result."""

    def __init__(
        self,
        codex_service: CodexService,
        repository: ResearchDocumentRepository | None = None,
    ):
        self.codex_service = codex_service
        self.repository = repository or ResearchDocumentRepository()

    def run_once(
        self,
        session: Session,
        *,
        question: str,
        research_cutoff: datetime | None = None,
        season_id: str | None = None,
        gameweek_id: int | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ):
        if not question.strip():
            raise ValueError("Research question must not be empty")

        result = self.codex_service.execute(
            prompt=question,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        if not result.final_text.strip():
            raise RuntimeError("Codex returned no research content")

        document = self.repository.create(
            session,
            question=question,
            content=result.final_text,
            research_cutoff=research_cutoff,
            season_id=season_id,
            gameweek_id=gameweek_id,
            codex_thread_id=result.thread_id,
            codex_turn_id=result.turn_id,
            model=result.model,
            reasoning_effort=result.reasoning_effort,
            status="CURRENT",
            usage_metadata=result.usage,
            execution_metadata=result.execution_metadata,
        )
        logger.info(
            "research_document_persisted document_id=%s thread_id=%s turn_id=%s",
            document.id,
            result.thread_id,
            result.turn_id,
        )
        return document


class ResearchJobExecutor:
    """Execute one READY ResearchJob and persist its durable result."""

    def __init__(
        self,
        codex_service: CodexService,
        *,
        job_repository: ResearchJobRepository | None = None,
        document_repository: ResearchDocumentRepository | None = None,
        run_service: ResearchRunService | None = None,
    ):
        self.codex_service = codex_service
        self.job_repository = job_repository or ResearchJobRepository()
        self.document_repository = document_repository or ResearchDocumentRepository()
        self.run_service = run_service or ResearchRunService()

    def execute(self, session: Session, job_id: str) -> ResearchJobExecutionResult:
        job = self.job_repository.get_by_id(session, job_id)
        if job is None:
            raise ResearchJobNotFoundError("ResearchJob not found")
        if job.status != ResearchJobStatus.READY:
            raise ResearchJobNotReadyError(
                f"ResearchJob must be READY to execute; current status is {job.status}"
            )
        if not job.question.strip():
            raise ValueError("ResearchJob request must not be empty")

        ResearchJobLifecycle.transition(job, ResearchJobStatus.RUNNING)
        session.commit()
        logger.info("research_job_execution_start job_id=%s attempt=%s", job.id, job.attempt_count)

        try:
            result = self.codex_service.execute(
                prompt=job.question,
                model=job.model,
                reasoning_effort=job.reasoning_effort,
            )
            if result is None or not result.final_text or not result.final_text.strip():
                raise RuntimeError("Codex returned no research content")

            logger.info(
                "research_job_codex_complete job_id=%s thread_id=%s turn_id=%s",
                job.id,
                result.thread_id,
                result.turn_id,
            )
            document = self._persist_success(session, job_id, result)
            logger.info(
                "research_job_execution_success job_id=%s document_id=%s",
                job_id,
                document.id,
            )
            return ResearchJobExecutionResult(job, document)
        except Exception as exc:
            self._persist_failure(session, job_id, exc)
            logger.exception("research_job_execution_failure job_id=%s", job_id)
            raise

    def _persist_success(self, session: Session, job_id: str, result: Any) -> ResearchDocument:
        job = self.job_repository.get_by_id(session, job_id)
        if job is None:
            raise ResearchJobNotFoundError("ResearchJob disappeared during execution")
        if job.status != ResearchJobStatus.RUNNING:
            raise RuntimeError(f"ResearchJob is no longer RUNNING: {job.status}")

        job.codex_thread_id = result.thread_id
        job.codex_turn_id = result.turn_id
        job.model = result.model
        job.reasoning_effort = result.reasoning_effort
        job.error_message = None
        document = self.document_repository.add_for_job(
            session,
            job,
            question=job.question,
            content=result.final_text,
            research_cutoff=job.research_run.research_cutoff if job.research_run else None,
            season_id=job.research_run.season_id if job.research_run else None,
            gameweek_id=job.research_run.gameweek_id if job.research_run else None,
            codex_thread_id=result.thread_id,
            codex_turn_id=result.turn_id,
            model=result.model,
            reasoning_effort=result.reasoning_effort,
            status="CURRENT",
            usage_metadata=result.usage,
            execution_metadata=result.execution_metadata,
        )
        ResearchJobLifecycle.transition(job, ResearchJobStatus.COMPLETE)
        self.run_service.refresh_readiness(session, job.research_run_id)
        logger.info("research_job_readiness_refreshed job_id=%s run_id=%s", job.id, job.research_run_id)
        session.commit()
        session.refresh(document)
        return document

    def _persist_failure(self, session: Session, job_id: str, error: Exception) -> None:
        session.rollback()
        job = self.job_repository.get_by_id(session, job_id)
        if job is None or job.status != ResearchJobStatus.RUNNING:
            return
        job.error_message = str(error)[:4000]
        ResearchJobLifecycle.transition(job, ResearchJobStatus.FAILED_RETRYABLE)
        session.commit()
