import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fpl_intelligence.db.base import Base
from fpl_intelligence.models import ResearchJobStatus
from fpl_intelligence.repositories.research_documents import ResearchDocumentRepository
from fpl_intelligence.research.service import ResearchJobService, ResearchRunService


@pytest.fixture
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


def _plan(with_dependency=True):
    jobs = [
        {"key": "first", "subject": "First subject", "question": "First question"},
        {"key": "second", "subject": "Second subject", "question": "Second question"},
    ]
    if with_dependency:
        jobs[1]["dependencies"] = ["first"]
    return [{"key": "section", "title": "A section", "jobs": jobs}]


def test_create_run_with_ordered_hierarchy_and_initial_readiness(session):
    run = ResearchRunService().create_run(
        session, season_id="2026-27", gameweek_id=1, sections=_plan()
    )
    retrieved = ResearchRunService().get_run(session, run.id)

    assert retrieved is not None
    assert retrieved.gameweek_id == 1
    assert retrieved.sections[0].ordering == 0
    assert [job.key for job in retrieved.sections[0].jobs] == ["first", "second"]
    assert retrieved.sections[0].jobs[0].status == ResearchJobStatus.READY
    assert retrieved.sections[0].jobs[1].status == ResearchJobStatus.PENDING


def test_completing_dependency_makes_downstream_job_ready(session):
    run = ResearchRunService().create_run(session, sections=_plan())
    first, second = ResearchRunService().get_run(session, run.id).jobs
    service = ResearchRunService()

    service.transition_job(session, first.id, ResearchJobStatus.RUNNING)
    service.transition_job(session, first.id, ResearchJobStatus.COMPLETE)
    changed = service.refresh_readiness(session, run.id)

    assert second in changed
    assert second.status == ResearchJobStatus.READY


def test_complete_with_gaps_unblocks_dependency_but_failure_does_not(session):
    service = ResearchRunService()
    run = service.create_run(session, sections=_plan())
    first, second = service.get_run(session, run.id).jobs
    service.transition_job(session, first.id, ResearchJobStatus.RUNNING)
    service.transition_job(session, first.id, ResearchJobStatus.COMPLETE_WITH_GAPS)
    service.refresh_readiness(session, run.id)
    assert second.status == ResearchJobStatus.READY

    failed_run = service.create_run(session, sections=_plan())
    failed_first, failed_second = service.get_run(session, failed_run.id).jobs
    service.transition_job(session, failed_first.id, ResearchJobStatus.RUNNING)
    service.transition_job(session, failed_first.id, ResearchJobStatus.FAILED)
    service.refresh_readiness(session, failed_run.id)
    assert failed_second.status == ResearchJobStatus.PENDING


def test_document_can_be_associated_with_job_and_hierarchy_retrieved(session, execution_result):
    service = ResearchRunService()
    run = service.create_run(session, sections=_plan(with_dependency=False))
    job = service.get_run(session, run.id).jobs[0]
    document = ResearchDocumentRepository().create_for_job(
        session,
        job,
        question=job.question,
        content="A persisted result",
        codex_thread_id=execution_result.thread_id,
    )

    retrieved = service.get_run(session, run.id)
    assert retrieved.sections[0].jobs[0].documents[0].id == document.id
    assert document.research_job_id == job.id
    assert document.research_run_id == run.id
    assert document.research_section_id == retrieved.sections[0].id


def test_terminal_job_cannot_return_to_running(session):
    service = ResearchRunService()
    run = service.create_run(session, sections=_plan(with_dependency=False))
    job = service.get_run(session, run.id).jobs[0]
    service.transition_job(session, job.id, ResearchJobStatus.RUNNING)
    service.transition_job(session, job.id, ResearchJobStatus.COMPLETE)

    with pytest.raises(ValueError, match="Invalid ResearchJob status transition"):
        service.transition_job(session, job.id, ResearchJobStatus.RUNNING)
