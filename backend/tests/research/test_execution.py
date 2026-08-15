from dataclasses import replace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.db.base import Base
from fpl_intelligence.models import ResearchDocument, ResearchJobStatus
from fpl_intelligence.research.execution import (
    ResearchExecutionService,
    ResearchJobExecutor,
    ResearchJobNotReadyError,
)
from fpl_intelligence.research.service import ResearchRunService


class FakeCodexClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, prompt, config):
        self.calls.append((prompt, config))
        return self.result


def test_run_once_persists_free_form_document(execution_result):
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    client = FakeCodexClient(execution_result)
    service = ResearchExecutionService(CodexService(client=client))

    document = service.run_once(
        session,
        question="Which question should be researched?",
        season_id="2026-27",
        gameweek_id=1,
        model="explicit-model",
        reasoning_effort="high",
    )

    assert document.question == "Which question should be researched?"
    assert document.content.startswith("# Research")
    assert document.codex_thread_id == "thread-test"
    assert document.codex_turn_id == "turn-test"
    assert document.model == "codex-test"
    assert document.reasoning_effort == "medium"
    assert session.scalar(select(ResearchDocument).where(ResearchDocument.id == document.id))
    assert len(client.calls) == 1


def test_failed_codex_execution_does_not_persist_document():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    class FailingClient:
        def execute(self, prompt, config):
            raise RuntimeError("App Server unavailable")

    service = ResearchExecutionService(CodexService(client=FailingClient()))
    try:
        service.run_once(session, question="question")
    except RuntimeError as exc:
        assert "unavailable" in str(exc)
    else:
        raise AssertionError("failed execution should be surfaced")
    assert session.scalar(select(ResearchDocument)) is None


class FakeCodexService:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def _job_session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    return engine, session


def _job_plan():
    return [{
        "key": "context",
        "title": "Context",
        "jobs": [
            {"key": "first", "subject": "First", "question": "First request", "model": "job-model", "reasoning_effort": "high"},
            {"key": "second", "subject": "Second", "question": "Second request", "dependencies": ["first"]},
        ],
    }]


def test_ready_job_executor_persists_document_provenance_and_unlocks_dependents(execution_result):
    engine, session = _job_session()
    try:
        run = ResearchRunService().create_run(session, season_id="2026-27", gameweek_id=1, sections=_job_plan())
        first, second = ResearchRunService().get_run(session, run.id).jobs
        codex = FakeCodexService(execution_result)

        execution = ResearchJobExecutor(codex).execute(session, first.id)

        assert execution.document.research_job_id == first.id
        assert execution.document.research_run_id == run.id
        assert execution.document.codex_thread_id == execution_result.thread_id
        assert execution.document.codex_turn_id == execution_result.turn_id
        assert execution.document.model == execution_result.model
        assert execution.document.reasoning_effort == execution_result.reasoning_effort
        assert execution.document.usage_metadata == execution_result.usage
        assert first.status == ResearchJobStatus.COMPLETE
        assert first.codex_thread_id == execution_result.thread_id
        assert first.codex_turn_id == execution_result.turn_id
        assert first.attempt_count == 1
        assert second.status == ResearchJobStatus.READY
        assert codex.calls == [{
            "prompt": "First request",
            "model": "job-model",
            "reasoning_effort": "high",
        }]
    finally:
        session.close()
        engine.dispose()


def test_non_ready_job_cannot_execute(execution_result):
    engine, session = _job_session()
    try:
        run = ResearchRunService().create_run(session, sections=_job_plan())
        _, pending = ResearchRunService().get_run(session, run.id).jobs
        codex = FakeCodexService(execution_result)

        with pytest.raises(ResearchJobNotReadyError):
            ResearchJobExecutor(codex).execute(session, pending.id)

        assert pending.status == ResearchJobStatus.PENDING
        assert codex.calls == []
    finally:
        session.close()
        engine.dispose()


def test_codex_failure_is_retryable_and_creates_no_document(execution_result):
    engine, session = _job_session()
    try:
        run = ResearchRunService().create_run(session, sections=_job_plan())
        first = ResearchRunService().get_run(session, run.id).jobs[0]

        with pytest.raises(RuntimeError, match="App Server unavailable"):
            ResearchJobExecutor(FakeCodexService(error=RuntimeError("App Server unavailable"))).execute(
                session, first.id
            )

        assert first.status == ResearchJobStatus.FAILED_RETRYABLE
        assert first.error_message == "App Server unavailable"
        assert session.scalar(select(ResearchDocument).where(ResearchDocument.research_job_id == first.id)) is None
    finally:
        session.close()
        engine.dispose()


def test_empty_codex_result_is_not_complete(execution_result):
    engine, session = _job_session()
    try:
        run = ResearchRunService().create_run(session, sections=_job_plan())
        first = ResearchRunService().get_run(session, run.id).jobs[0]
        empty_result = replace(execution_result, final_text="  ")

        with pytest.raises(RuntimeError, match="no research content"):
            ResearchJobExecutor(FakeCodexService(empty_result)).execute(session, first.id)

        assert first.status == ResearchJobStatus.FAILED_RETRYABLE
        assert session.scalar(select(ResearchDocument).where(ResearchDocument.research_job_id == first.id)) is None
    finally:
        session.close()
        engine.dispose()


def test_document_persistence_failure_is_retryable(execution_result):
    class FailingDocumentRepository:
        def add_for_job(self, session, job, **values):
            raise RuntimeError("document store unavailable")

    engine, session = _job_session()
    try:
        run = ResearchRunService().create_run(session, sections=_job_plan())
        first = ResearchRunService().get_run(session, run.id).jobs[0]

        with pytest.raises(RuntimeError, match="document store unavailable"):
            ResearchJobExecutor(
                FakeCodexService(execution_result),
                document_repository=FailingDocumentRepository(),
            ).execute(session, first.id)

        assert first.status == ResearchJobStatus.FAILED_RETRYABLE
        assert session.scalar(select(ResearchDocument).where(ResearchDocument.research_job_id == first.id)) is None
    finally:
        session.close()
        engine.dispose()
