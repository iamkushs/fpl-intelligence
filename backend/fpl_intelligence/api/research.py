"""Research document and durable research-run HTTP endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from fpl_intelligence.models import (
    PlayerResearchTrigger,
    ResearchDocument,
    ResearchJob,
    ResearchRun,
    ResearchSection,
    ResearchSituation,
    ResearchThread,
    SituationHypothesis,
)
from fpl_intelligence.repositories.research_documents import ResearchDocumentRepository
from fpl_intelligence.repositories.research_jobs import ResearchJobRepository
from fpl_intelligence.research.execution import (
    ResearchExecutionService,
    ResearchJobExecutor,
    ResearchJobNotFoundError,
    ResearchJobNotReadyError,
)
from fpl_intelligence.research.service import ResearchRunService
from fpl_intelligence.research.situations import ResearchSituationService
from fpl_intelligence.research.two_stage import PlayerResolver
from fpl_intelligence.repositories.research_persistence import ResearchPersistenceRepository

router = APIRouter(prefix="/research", tags=["research"])


class ResearchDocumentRunRequest(BaseModel):
    question: str = Field(min_length=1)
    research_cutoff: datetime | None = None
    season_id: str | None = None
    gameweek_id: int | None = Field(default=None, ge=1)
    model: str | None = None
    reasoning_effort: str | None = None


class LinkCollectionRequest(BaseModel):
    queries: list[str] | None = None


class LinkResearchRequest(BaseModel):
    link_ids: list[str] | None = None
    all_collected: bool = False


class SituationCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    club_id: int | None = None
    context: str = Field(min_length=1)
    fpl_relevance: str = Field(min_length=1)
    status: str = "open"
    player_ids: list[int] = Field(default_factory=list)
    last_checked_at: datetime | None = None


class SituationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    club_id: int | None = None
    context: str | None = Field(default=None, min_length=1)
    fpl_relevance: str | None = Field(default=None, min_length=1)
    status: str | None = None
    last_checked_at: datetime | None = None
    touch_last_checked: bool = False


class SituationPlayersRequest(BaseModel):
    player_ids: list[int] = Field(min_length=1)


class SituationHypothesisRequest(BaseModel):
    statement: str = Field(min_length=1)
    active: bool = True


class SituationAttachTriggerRequest(BaseModel):
    trigger_id: str = Field(min_length=1)


class SituationAttachThreadRequest(BaseModel):
    thread_id: str = Field(min_length=1)


class SituationPlayerResponse(BaseModel):
    id: int


class SituationHypothesisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    situation_id: str
    statement: str
    active: bool
    created_at: datetime
    updated_at: datetime


class ResearchSituationResponse(BaseModel):
    id: str
    title: str
    club_id: int | None
    context: str
    fpl_relevance: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_checked_at: datetime | None
    players: list[SituationPlayerResponse]
    hypotheses: list[SituationHypothesisResponse]


class ResearchLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    research_thread_id: str
    original_url: str
    canonical_url: str
    title: str | None
    domain: str
    source_type: str | None
    relevance_reason: str | None
    status: str
    discovered_at: datetime
    player_ids: list[int]


class ThreadResearchResultResponse(BaseModel):
    id: str
    research_thread_id: str
    research_link_id: str
    source_url: str
    summary: str
    findings: str
    evidence: str
    uncertainty: str | None
    researched_at: datetime
    player_ids: list[int]


class ResearchDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    research_job_id: str | None
    research_run_id: str | None
    research_section_id: str | None
    question: str
    content: str
    created_at: datetime
    research_cutoff: datetime | None
    season_id: str | None
    gameweek_id: int | None
    codex_thread_id: str
    codex_turn_id: str | None
    model: str | None
    reasoning_effort: str | None
    status: str
    supersedes_id: str | None
    usage_metadata: dict | None
    execution_metadata: dict | None


class ResearchJobCreateRequest(BaseModel):
    key: str | None = None
    subject: str | None = Field(default=None, min_length=1)
    title: str | None = Field(default=None, min_length=1)
    question: str | None = Field(default=None, min_length=1)
    request: str | None = Field(default=None, min_length=1)
    ordering: int = Field(default=0, ge=0)
    order: int | None = Field(default=None, ge=0)
    dependencies: list[str] = Field(default_factory=list)
    model: str | None = None
    reasoning_effort: str | None = None


class ResearchSectionCreateRequest(BaseModel):
    key: str | None = None
    title: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    ordering: int = Field(default=0, ge=0)
    order: int | None = Field(default=None, ge=0)
    jobs: list[ResearchJobCreateRequest] = Field(default_factory=list)


class ResearchRunCreateRequest(BaseModel):
    season_id: str | None = None
    gameweek_id: int | None = Field(default=None, ge=1)
    mode: str = Field(default="STANDARD", min_length=1)
    research_cutoff: datetime | None = None
    sections: list[ResearchSectionCreateRequest] = Field(default_factory=list)


class ResearchJobResponse(BaseModel):
    id: str
    research_run_id: str
    research_section_id: str
    key: str | None
    subject: str
    title: str
    question: str
    ordering: int
    status: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    codex_thread_id: str | None
    codex_turn_id: str | None
    model: str | None
    reasoning_effort: str | None
    attempt_count: int
    error_message: str | None
    dependencies: list[str]
    documents: list[ResearchDocumentResponse]


class ResearchJobExecutionResponse(BaseModel):
    job: ResearchJobResponse
    document: ResearchDocumentResponse


class ResearchSectionResponse(BaseModel):
    id: str
    research_run_id: str
    key: str | None
    title: str
    ordering: int
    status: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    jobs: list[ResearchJobResponse]


class ResearchRunResponse(BaseModel):
    id: str
    season_id: str | None
    gameweek_id: int | None
    mode: str
    status: str
    research_cutoff: datetime | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    sections: list[ResearchSectionResponse]


def _document_response(document: ResearchDocument) -> ResearchDocumentResponse:
    return ResearchDocumentResponse.model_validate(document)


def _job_response(job: ResearchJob) -> ResearchJobResponse:
    return ResearchJobResponse(
        id=job.id,
        research_run_id=job.research_run_id,
        research_section_id=job.research_section_id,
        key=job.key,
        subject=job.subject,
        title=job.title,
        question=job.question,
        ordering=job.ordering,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        codex_thread_id=job.codex_thread_id,
        codex_turn_id=job.codex_turn_id,
        model=job.model,
        reasoning_effort=job.reasoning_effort,
        attempt_count=job.attempt_count,
        error_message=job.error_message,
        dependencies=[dependency.id for dependency in job.dependencies],
        documents=[_document_response(document) for document in job.documents],
    )


def _run_response(run: ResearchRun) -> ResearchRunResponse:
    return ResearchRunResponse(
        id=run.id,
        season_id=run.season_id,
        gameweek_id=run.gameweek_id,
        mode=run.mode,
        status=run.status,
        research_cutoff=run.research_cutoff,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        sections=[
            ResearchSectionResponse(
                id=section.id,
                research_run_id=section.research_run_id,
                key=section.key,
                title=section.title,
                ordering=section.ordering,
                status=section.status,
                created_at=section.created_at,
                started_at=section.started_at,
                completed_at=section.completed_at,
                jobs=[_job_response(job) for job in section.jobs],
            )
            for section in run.sections
        ],
    )


def _situation_response(situation: ResearchSituation) -> ResearchSituationResponse:
    return ResearchSituationResponse(
        id=situation.id,
        title=situation.title,
        club_id=situation.club_id,
        context=situation.context,
        fpl_relevance=situation.fpl_relevance,
        status=situation.status,
        created_at=situation.created_at,
        updated_at=situation.updated_at,
        last_checked_at=situation.last_checked_at,
        players=[SituationPlayerResponse(id=player.id) for player in sorted(situation.players, key=lambda item: item.id)],
        hypotheses=[SituationHypothesisResponse.model_validate(item) for item in situation.hypotheses],
    )


def get_session(request: Request):
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured",
        )
    session = database.session_factory()
    try:
        yield session
    finally:
        session.close()


def _player_resolver(request: Request) -> PlayerResolver:
    snapshot = request.app.state.fpl_snapshot_service.get_snapshot()
    return PlayerResolver(snapshot.players)


@router.post("/situations", response_model=ResearchSituationResponse, status_code=status.HTTP_201_CREATED)
def create_situation(payload: SituationCreateRequest, session: Session = Depends(get_session)):
    try:
        situation = ResearchSituationService().create_situation(
            session,
            title=payload.title,
            club_id=payload.club_id,
            context=payload.context,
            fpl_relevance=payload.fpl_relevance,
            status=payload.status,
            player_ids=payload.player_ids,
            last_checked_at=payload.last_checked_at,
        )
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _situation_response(situation)


@router.get("/situations/{situation_id}", response_model=ResearchSituationResponse)
def get_situation(situation_id: str, session: Session = Depends(get_session)):
    situation = ResearchSituationService().get_situation(session, situation_id)
    if situation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ResearchSituation not found")
    return _situation_response(situation)


@router.patch("/situations/{situation_id}", response_model=ResearchSituationResponse)
def update_situation(situation_id: str, payload: SituationUpdateRequest, session: Session = Depends(get_session)):
    try:
        situation = ResearchSituationService().update_situation(
            session,
            situation_id,
            title=payload.title,
            club_id=payload.club_id,
            context=payload.context,
            fpl_relevance=payload.fpl_relevance,
            status=payload.status,
            last_checked_at=payload.last_checked_at,
            touch_last_checked=payload.touch_last_checked,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _situation_response(situation)


@router.post("/situations/{situation_id}/players", response_model=ResearchSituationResponse)
def attach_situation_players(
    situation_id: str,
    payload: SituationPlayersRequest,
    session: Session = Depends(get_session),
):
    try:
        situation = ResearchSituationService().attach_players(session, situation_id, payload.player_ids)
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _situation_response(situation)


@router.post(
    "/situations/{situation_id}/hypotheses",
    response_model=SituationHypothesisResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_situation_hypothesis(
    situation_id: str,
    payload: SituationHypothesisRequest,
    session: Session = Depends(get_session),
):
    try:
        return ResearchSituationService().add_hypothesis(
            session, situation_id, statement=payload.statement, active=payload.active
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/situations/{situation_id}/triggers", response_model=dict)
def attach_situation_trigger(
    situation_id: str,
    payload: SituationAttachTriggerRequest,
    session: Session = Depends(get_session),
):
    try:
        trigger: PlayerResearchTrigger = ResearchSituationService().attach_trigger(session, situation_id, payload.trigger_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"trigger_id": trigger.id, "situation_id": trigger.situation_id}


@router.post("/situations/{situation_id}/threads", response_model=dict)
def attach_situation_thread(
    situation_id: str,
    payload: SituationAttachThreadRequest,
    session: Session = Depends(get_session),
):
    try:
        thread: ResearchThread = ResearchSituationService().attach_thread(session, situation_id, payload.thread_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"thread_id": thread.id, "situation_id": thread.situation_id}


@router.post("/threads/{thread_id}/collect")
def collect_thread_links(
    thread_id: str,
    payload: LinkCollectionRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    try:
        return request.app.state.two_stage_research_service.collect(
            session,
            thread_id=thread_id,
            player_resolver=_player_resolver(request),
            queries=payload.queries,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/threads/{thread_id}/links", response_model=list[ResearchLinkResponse])
def list_thread_links(thread_id: str, session: Session = Depends(get_session)):
    repository = ResearchPersistenceRepository()
    if repository.get_thread(session, thread_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ResearchThread not found")
    return [
        ResearchLinkResponse(
            id=link.id,
            research_thread_id=link.research_thread_id,
            original_url=link.original_url,
            canonical_url=link.canonical_url,
            title=link.title,
            domain=link.domain,
            source_type=link.source_type,
            relevance_reason=link.relevance_reason,
            status=link.status,
            discovered_at=link.discovered_at,
            player_ids=[player.id for player in link.players],
        )
        for link in repository.list_links(session, thread_id)
    ]


@router.post("/threads/{thread_id}/research")
def research_thread_links(
    thread_id: str,
    payload: LinkResearchRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    try:
        return request.app.state.two_stage_research_service.research(
            session,
            thread_id=thread_id,
            player_resolver=_player_resolver(request),
            link_ids=payload.link_ids,
            all_collected=payload.all_collected,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/threads/{thread_id}/discover-players")
def discover_players(thread_id: str, request: Request, session: Session = Depends(get_session)):
    """Synthesize existing results only; this stage performs no collection or retrieval."""
    try:
        snapshot = request.app.state.fpl_snapshot_service.get_snapshot()
        return request.app.state.discovery_service.generate(
            session, thread_id=thread_id, official_players=snapshot.players
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/threads/{thread_id}/results", response_model=list[ThreadResearchResultResponse])
def list_thread_results(thread_id: str, session: Session = Depends(get_session)):
    repository = ResearchPersistenceRepository()
    if repository.get_thread(session, thread_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ResearchThread not found")
    return [
        ThreadResearchResultResponse(
            id=result.id,
            research_thread_id=result.research_thread_id,
            research_link_id=result.research_link_id,
            source_url=result.research_link.original_url,
            summary=result.summary,
            findings=result.findings,
            evidence=result.evidence,
            uncertainty=result.uncertainty,
            researched_at=result.researched_at,
            player_ids=[player.id for player in result.players],
        )
        for result in repository.list_results(session, thread_id)
    ]


@router.post("/runs", response_model=ResearchRunResponse, status_code=status.HTTP_201_CREATED)
def create_research_run(
    payload: ResearchRunCreateRequest,
    session: Session = Depends(get_session),
):
    try:
        run = ResearchRunService().create_run(
            session,
            season_id=payload.season_id,
            gameweek_id=payload.gameweek_id,
            mode=payload.mode,
            research_cutoff=payload.research_cutoff,
            sections=[section.model_dump(exclude_none=True) for section in payload.sections],
        )
        run = ResearchRunService().get_run(session, run.id) or run
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _run_response(run)


@router.get("/runs/{run_id}", response_model=ResearchRunResponse)
def get_research_run(run_id: str, session: Session = Depends(get_session)):
    run = ResearchRunService().get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ResearchRun not found")
    return _run_response(run)


@router.get("/runs/{run_id}/jobs", response_model=list[ResearchJobResponse])
def list_research_run_jobs(run_id: str, session: Session = Depends(get_session)):
    run = ResearchRunService().get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ResearchRun not found")
    return [_job_response(job) for job in run.jobs]


@router.post(
    "/jobs/{job_id}/execute",
    response_model=ResearchJobExecutionResponse,
    status_code=status.HTTP_200_OK,
)
def execute_research_job(
    job_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    executor = ResearchJobExecutor(request.app.state.codex_service)
    try:
        execution = executor.execute(session, job_id)
    except ResearchJobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ResearchJobNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        request.app.state.logger.exception("research_job_execution_failed job_id=%s", job_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Codex research job execution failed",
        ) from exc

    persisted_job = ResearchJobRepository().get_by_id(session, execution.job.id)
    if persisted_job is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="ResearchJob disappeared")
    return ResearchJobExecutionResponse(
        job=_job_response(persisted_job),
        document=_document_response(execution.document),
    )


@router.post("/documents/run", response_model=ResearchDocumentResponse, status_code=status.HTTP_201_CREATED)
def run_research_document(
    request: Request,
    payload: ResearchDocumentRunRequest,
    session: Session = Depends(get_session),
):
    service = ResearchExecutionService(request.app.state.codex_service)
    try:
        document = service.run_once(
            session,
            question=payload.question,
            research_cutoff=payload.research_cutoff,
            season_id=payload.season_id,
            gameweek_id=payload.gameweek_id,
            model=payload.model,
            reasoning_effort=payload.reasoning_effort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        request.app.state.logger.exception("research_execution_failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Codex research execution failed",
        ) from exc
    return document


@router.get("/documents/{document_id}", response_model=ResearchDocumentResponse)
def get_research_document(document_id: str, session: Session = Depends(get_session)):
    document = ResearchDocumentRepository().get_by_id(session, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ResearchDocument not found")
    return document
