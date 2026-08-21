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
    ResearchEvidence,
    ResearchQualityStage,
    ResearchSourceCluster,
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
from fpl_intelligence.research.source_discovery import Eval2SourceDiscoveryService
from fpl_intelligence.research.quality import ResearchQualityService, quality_run_state
from fpl_intelligence.research.quality_execution import Eval2QualityExecutionService
from fpl_intelligence.repositories.research_persistence import ResearchPersistenceRepository
from fpl_intelligence.research.evidence import ResearchEvidenceService

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


class PlayerDiscoveryRequest(BaseModel):
    research_cutoff: datetime
    situation_id: str | None = None
    trigger_id: str | None = None
    gameweek_id: int | None = Field(default=None, ge=1)
    target_gameweek_id: int | None = Field(default=None, ge=1)
    known_missing_dimensions: list[str] = Field(default_factory=list)
    durable_context: dict | None = None


class ThreadDiscoveryRequest(PlayerDiscoveryRequest):
    player_id: int


class LinkResearchEval2Request(BaseModel):
    research_cutoff: datetime
    target_dimensions: list[str] = Field(default_factory=list)
    situation_id: str | None = None
    trigger_id: str | None = None
    durable_context: dict | None = None
    retry_failed: bool = False


class EvidenceExtractionEval2Request(BaseModel):
    research_cutoff: datetime
    situation_id: str | None = None
    trigger_id: str | None = None
    durable_context: dict | None = None


class QualityRedditStartRequest(BaseModel):
    player_id: int
    situation_id: str | None = None
    research_cutoff: datetime


class QualityCounterSearchStartRequest(BaseModel):
    player_id: int
    situation_id: str | None = None
    research_cutoff: datetime
    challenged_claim: str = Field(min_length=1)
    target_evidence_id: str | None = None


class QualityFreshnessStartRequest(BaseModel):
    player_id: int
    situation_id: str | None = None
    research_cutoff: datetime
    target_evidence_id: str = Field(min_length=1)


class QualityRunCompleteRequest(BaseModel):
    link_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    outcome: str | None = None
    superseding_evidence_id: str | None = None
    checked_at: datetime | None = None
    partial: bool = False
    failure_reason: str | None = None
    monitoring_condition: dict | None = None


class QualityRunResponse(BaseModel):
    id: str
    thread_id: str
    player_id: int
    situation_id: str | None
    stage: str
    status: str
    target_evidence_id: str | None
    superseding_evidence_id: str | None
    research_cutoff: datetime
    prompt_version: str
    challenged_claim: str | None
    questions: list | None
    outcome: str | None
    failure_reason: str | None
    checked_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    link_ids: list[str]
    evidence_ids: list[str]


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


class EvidenceCreateRequest(BaseModel):
    research_thread_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    claim_type: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    reliability: str = Field(min_length=1)
    relevance: str = Field(min_length=1)
    research_situation_id: str | None = None
    research_link_id: str | None = None
    research_result_id: str | None = None
    source_cluster_id: str | None = None
    player_ids: list[int] = Field(default_factory=list)
    published_at: datetime | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime | None = None
    season: str | None = None
    is_volatile: bool | None = None
    notes: str | None = None


class EvidencePlayersRequest(BaseModel):
    player_ids: list[int] = Field(min_length=1)


class EvidenceHypothesisRelationRequest(BaseModel):
    hypothesis_id: str = Field(min_length=1)
    relationship_type: str = Field(min_length=1)
    rationale: str | None = None


class EvidenceRelationRequest(BaseModel):
    to_evidence_id: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    rationale: str | None = None


class SourceClusterCreateRequest(BaseModel):
    research_thread_id: str = Field(min_length=1)
    narrative: str = Field(min_length=1)
    research_situation_id: str | None = None
    likely_original_research_link_id: str | None = None
    notes: str | None = None


class SourceClusterLinkRequest(BaseModel):
    research_link_id: str = Field(min_length=1)
    lineage_type: str = Field(min_length=1)
    notes: str | None = None


class EvidenceResponse(BaseModel):
    id: str
    research_thread_id: str
    research_situation_id: str | None
    claim: str
    claim_type: str
    evidence_type: str
    reliability: str
    relevance: str
    is_volatile: bool
    published_at: datetime | None
    observed_at: datetime | None
    retrieved_at: datetime | None
    season: str | None
    notes: str | None
    player_ids: list[int]
    source_provenance: dict | None
    source_cluster: dict | None
    hypothesis_relationships: list[dict]
    evidence_relationships: list[dict]


class SourceClusterResponse(BaseModel):
    id: str
    research_thread_id: str
    research_situation_id: str | None
    narrative: str
    likely_original_research_link_id: str | None
    notes: str | None
    independent_confirmation_count: int
    memberships: list[dict]


def _evidence_response(evidence: ResearchEvidence, service: ResearchEvidenceService, session: Session, *, relations=None) -> EvidenceResponse:
    provenance = None
    if evidence.research_link:
        provenance = {"research_link_id": evidence.research_link.id, "url": evidence.research_link.original_url,
                      "title": evidence.research_link.title, "source": evidence.research_link.domain,
                      "retrieval_status": evidence.research_link.status}
    if evidence.research_result:
        provenance = {**(provenance or {}), "research_result_id": evidence.research_result.id,
                      "result_retrieved_at": evidence.research_result.researched_at}
    cluster = None
    if evidence.source_cluster:
        cluster = {"id": evidence.source_cluster.id, "narrative": evidence.source_cluster.narrative}
    return EvidenceResponse(
        id=evidence.id, research_thread_id=evidence.research_thread_id, research_situation_id=evidence.research_situation_id,
        claim=evidence.claim, claim_type=evidence.claim_type, evidence_type=evidence.evidence_type,
        reliability=evidence.reliability, relevance=evidence.relevance, is_volatile=evidence.is_volatile,
        published_at=evidence.published_at, observed_at=evidence.observed_at, retrieved_at=evidence.retrieved_at,
        season=evidence.season, notes=evidence.notes, player_ids=sorted(player.id for player in evidence.players),
        source_provenance=provenance, source_cluster=cluster,
        hypothesis_relationships=[{"hypothesis_id": item.hypothesis_id, "relationship_type": item.relationship_type, "rationale": item.rationale}
                                  for item in evidence.hypothesis_relations],
        evidence_relationships=[{"from_evidence_id": item.from_evidence_id, "to_evidence_id": item.to_evidence_id,
                                 "relation_type": item.relation_type, "rationale": item.rationale}
                                for item in (service.relations_for(session, evidence.id) if relations is None else relations)],
    )


def _evidence_responses(evidence: list[ResearchEvidence], service: ResearchEvidenceService, session: Session) -> list[EvidenceResponse]:
    relations = service.relations_for_many(session, [item.id for item in evidence])
    return [_evidence_response(item, service, session, relations=relations[item.id]) for item in evidence]


def _cluster_response(cluster: ResearchSourceCluster, service: ResearchEvidenceService) -> SourceClusterResponse:
    return SourceClusterResponse(
        id=cluster.id, research_thread_id=cluster.research_thread_id, research_situation_id=cluster.research_situation_id,
        narrative=cluster.narrative, likely_original_research_link_id=cluster.likely_original_research_link_id, notes=cluster.notes,
        independent_confirmation_count=service.independent_confirmation_count(cluster),
        memberships=[{"research_link_id": item.research_link_id, "lineage_type": item.lineage_type, "notes": item.notes,
                      "url": item.research_link.original_url, "title": item.research_link.title, "source": item.research_link.domain}
                     for item in cluster.memberships],
    )


def _evidence_error(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    raise exc


@router.post("/evidence", response_model=EvidenceResponse, status_code=status.HTTP_201_CREATED)
def create_evidence(payload: EvidenceCreateRequest, session: Session = Depends(get_session)):
    service = ResearchEvidenceService()
    try:
        evidence = service.create_evidence(session, **payload.model_dump())
    except (LookupError, ValueError) as exc:
        session.rollback()
        _evidence_error(exc)
    return _evidence_response(evidence, service, session)


@router.get("/evidence/{evidence_id}", response_model=EvidenceResponse)
def get_evidence(evidence_id: str, session: Session = Depends(get_session)):
    service = ResearchEvidenceService()
    evidence = service.get_evidence(session, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ResearchEvidence not found")
    return _evidence_response(evidence, service, session)


@router.get("/threads/{thread_id}/evidence", response_model=list[EvidenceResponse])
def list_thread_evidence(thread_id: str, session: Session = Depends(get_session)):
    service = ResearchEvidenceService()
    if service.repository.get_thread(session, thread_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ResearchThread not found")
    return _evidence_responses(service.list_evidence(session, thread_id=thread_id), service, session)


@router.get("/situations/{situation_id}/evidence", response_model=list[EvidenceResponse])
def list_situation_evidence(situation_id: str, session: Session = Depends(get_session)):
    service = ResearchEvidenceService()
    if service.repository.get_situation(session, situation_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ResearchSituation not found")
    return _evidence_responses(service.list_evidence(session, situation_id=situation_id), service, session)


@router.post("/evidence/{evidence_id}/players", response_model=EvidenceResponse)
def attach_evidence_players(evidence_id: str, payload: EvidencePlayersRequest, session: Session = Depends(get_session)):
    service = ResearchEvidenceService()
    try:
        evidence = service.attach_players(session, evidence_id, payload.player_ids)
    except (LookupError, ValueError) as exc:
        session.rollback(); _evidence_error(exc)
    return _evidence_response(evidence, service, session)


@router.post("/evidence/{evidence_id}/hypothesis-relations", response_model=dict, status_code=status.HTTP_201_CREATED)
def add_evidence_hypothesis_relation(evidence_id: str, payload: EvidenceHypothesisRelationRequest, session: Session = Depends(get_session)):
    try:
        relation = ResearchEvidenceService().add_hypothesis_relation(session, evidence_id=evidence_id, **payload.model_dump())
    except (LookupError, ValueError) as exc:
        session.rollback(); _evidence_error(exc)
    return {"id": relation.id, "evidence_id": relation.evidence_id, "hypothesis_id": relation.hypothesis_id, "relationship_type": relation.relationship_type, "rationale": relation.rationale}


@router.post("/evidence/{evidence_id}/relations", response_model=dict, status_code=status.HTTP_201_CREATED)
def add_evidence_relation(evidence_id: str, payload: EvidenceRelationRequest, session: Session = Depends(get_session)):
    try:
        relation = ResearchEvidenceService().add_evidence_relation(session, from_evidence_id=evidence_id, **payload.model_dump())
    except (LookupError, ValueError) as exc:
        session.rollback(); _evidence_error(exc)
    return {"id": relation.id, "from_evidence_id": relation.from_evidence_id, "to_evidence_id": relation.to_evidence_id, "relation_type": relation.relation_type, "rationale": relation.rationale}


@router.post("/source-clusters", response_model=SourceClusterResponse, status_code=status.HTTP_201_CREATED)
def create_source_cluster(payload: SourceClusterCreateRequest, session: Session = Depends(get_session)):
    service = ResearchEvidenceService()
    try:
        cluster = service.create_cluster(session, **payload.model_dump())
    except (LookupError, ValueError) as exc:
        session.rollback(); _evidence_error(exc)
    return _cluster_response(cluster, service)


@router.get("/source-clusters/{cluster_id}", response_model=SourceClusterResponse)
def get_source_cluster(cluster_id: str, session: Session = Depends(get_session)):
    service = ResearchEvidenceService()
    cluster = service.get_cluster(session, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ResearchSourceCluster not found")
    return _cluster_response(cluster, service)


@router.post("/source-clusters/{cluster_id}/links", response_model=SourceClusterResponse)
def attach_source_cluster_link(cluster_id: str, payload: SourceClusterLinkRequest, session: Session = Depends(get_session)):
    service = ResearchEvidenceService()
    try:
        cluster = service.attach_cluster_link(session, cluster_id=cluster_id, **payload.model_dump())
    except (LookupError, ValueError) as exc:
        session.rollback(); _evidence_error(exc)
    return _cluster_response(cluster, service)


def _player_resolver(request: Request) -> PlayerResolver:
    snapshot = request.app.state.fpl_snapshot_service.get_snapshot()
    return PlayerResolver(snapshot.players)


def _eval2_service(request: Request) -> Eval2SourceDiscoveryService:
    return request.app.state.eval2_source_discovery_service


def _quality_service(request: Request) -> ResearchQualityService:
    return getattr(request.app.state, "research_quality_service", ResearchQualityService())


def _quality_execution_service(request: Request) -> Eval2QualityExecutionService:
    return request.app.state.eval2_quality_execution_service


def _quality_error(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    raise exc


@router.post("/threads/{thread_id}/quality/reddit", response_model=QualityRunResponse, status_code=status.HTTP_201_CREATED)
def start_reddit_quality_run(thread_id: str, payload: QualityRedditStartRequest, request: Request, session: Session = Depends(get_session)):
    try:
        run = _quality_service(request).start_reddit_run(session, thread_id=thread_id, **payload.model_dump())
    except (LookupError, ValueError) as exc:
        session.rollback(); _quality_error(exc)
    return quality_run_state(run)


@router.post("/threads/{thread_id}/quality/counter-search", response_model=QualityRunResponse, status_code=status.HTTP_201_CREATED)
def start_counter_search_quality_run(thread_id: str, payload: QualityCounterSearchStartRequest, request: Request, session: Session = Depends(get_session)):
    challenged_claim = payload.challenged_claim.strip()
    if not challenged_claim:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="challenged_claim is required")
    try:
        run = _quality_service(request).start_counter_search_run(
            session,
            thread_id=thread_id,
            player_id=payload.player_id,
            situation_id=payload.situation_id,
            research_cutoff=payload.research_cutoff,
            challenged_claim=challenged_claim,
            target_evidence_id=payload.target_evidence_id,
        )
    except (LookupError, ValueError) as exc:
        session.rollback(); _quality_error(exc)
    return quality_run_state(run)


@router.post("/threads/{thread_id}/quality/freshness", response_model=QualityRunResponse, status_code=status.HTTP_201_CREATED)
def start_freshness_quality_run(thread_id: str, payload: QualityFreshnessStartRequest, request: Request, session: Session = Depends(get_session)):
    try:
        run = _quality_service(request).start_freshness_run(session, thread_id=thread_id, **payload.model_dump())
    except (LookupError, ValueError) as exc:
        session.rollback(); _quality_error(exc)
    return quality_run_state(run)


@router.post("/quality-runs/{run_id}/complete", response_model=QualityRunResponse)
def complete_quality_run(run_id: str, payload: QualityRunCompleteRequest, request: Request, session: Session = Depends(get_session)):
    service = _quality_service(request)
    try:
        run = service.repository.get_run_detail(session, run_id)
        if run.stage == ResearchQualityStage.REDDIT:
            updated = service.complete_reddit_run(session, run_id=run_id, link_ids=payload.link_ids, evidence_ids=payload.evidence_ids, partial=payload.partial)
        elif run.stage == ResearchQualityStage.COUNTER_SEARCH:
            updated = service.complete_counter_search_run(session, run_id=run_id, outcome=payload.outcome or "", link_ids=payload.link_ids, evidence_ids=payload.evidence_ids, partial=payload.partial)
        elif run.stage == ResearchQualityStage.FRESHNESS:
            updated = service.complete_freshness_run(
                session,
                run_id=run_id,
                outcome=payload.outcome or "",
                link_ids=payload.link_ids,
                evidence_ids=payload.evidence_ids,
                checked_at=payload.checked_at,
                superseding_evidence_id=payload.superseding_evidence_id,
                monitoring_condition=payload.monitoring_condition,
                partial=payload.partial,
            )
        else:
            raise ValueError("Unknown quality run stage")
    except (LookupError, ValueError) as exc:
        session.rollback(); _quality_error(exc)
    return quality_run_state(updated)


@router.post("/quality-runs/{run_id}/execute-reddit", response_model=QualityRunResponse)
def execute_reddit_quality_run(run_id: str, request: Request, session: Session = Depends(get_session)):
    service = _quality_execution_service(request)
    try:
        result = service.execute_reddit(session, run_id)
        session.expire_all()
        updated = service.quality_service.repository.get_run_detail(session, result["run"].id)
    except (LookupError, ValueError) as exc:
        session.rollback(); _quality_error(exc)
    return quality_run_state(updated)


@router.post("/quality-runs/{run_id}/execute-counter-search", response_model=QualityRunResponse)
def execute_counter_search_quality_run(run_id: str, request: Request, session: Session = Depends(get_session)):
    service = _quality_execution_service(request)
    try:
        result = service.execute_counter_search(session, run_id)
        session.expire_all()
        updated = service.quality_service.repository.get_run_detail(session, result["run"].id)
    except (LookupError, ValueError) as exc:
        session.rollback(); _quality_error(exc)
    return quality_run_state(updated)


@router.post("/quality-runs/{run_id}/execute-freshness", response_model=QualityRunResponse)
def execute_freshness_quality_run(run_id: str, request: Request, session: Session = Depends(get_session)):
    service = _quality_execution_service(request)
    try:
        result = service.execute_freshness(session, run_id)
        session.expire_all()
        updated = service.quality_service.repository.get_run_detail(session, result["run"].id)
    except (LookupError, ValueError) as exc:
        session.rollback(); _quality_error(exc)
    return quality_run_state(updated)


@router.get("/quality-runs/{run_id}", response_model=QualityRunResponse)
def get_quality_run(run_id: str, request: Request, session: Session = Depends(get_session)):
    try:
        return _quality_service(request).get_run_detail(session, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/threads/{thread_id}/quality-runs", response_model=list[QualityRunResponse])
def list_thread_quality_runs(thread_id: str, request: Request, session: Session = Depends(get_session)):
    try:
        return _quality_service(request).list_runs_for_thread(session, thread_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


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


@router.post("/players/{player_id}/discover")
def discover_player_sources(
    player_id: int,
    payload: PlayerDiscoveryRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    try:
        return _eval2_service(request).start_player_discovery(
            session,
            player_id=player_id,
            research_cutoff=payload.research_cutoff,
            situation_id=payload.situation_id,
            trigger_id=payload.trigger_id,
            gameweek_id=payload.gameweek_id,
            target_gameweek_id=payload.target_gameweek_id,
            known_missing_dimensions=payload.known_missing_dimensions,
            durable_context=payload.durable_context,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/threads/{thread_id}/discover")
def discover_thread_sources(
    thread_id: str,
    payload: ThreadDiscoveryRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    try:
        return _eval2_service(request).discover_for_thread(
            session,
            thread_id=thread_id,
            player_id=payload.player_id,
            research_cutoff=payload.research_cutoff,
            situation_id=payload.situation_id,
            trigger_id=payload.trigger_id,
            gameweek_id=payload.gameweek_id,
            target_gameweek_id=payload.target_gameweek_id,
            known_missing_dimensions=payload.known_missing_dimensions,
            durable_context=payload.durable_context,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/threads/{thread_id}/discovery")
def get_thread_discovery(thread_id: str, request: Request, session: Session = Depends(get_session)):
    try:
        return _eval2_service(request).thread_execution_state(session, thread_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/threads/{thread_id}/execution")
def get_thread_execution(thread_id: str, request: Request, session: Session = Depends(get_session)):
    try:
        return _eval2_service(request).thread_execution_state(session, thread_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/links/{link_id}/research")
def research_collected_link(
    link_id: str,
    payload: LinkResearchEval2Request,
    request: Request,
    session: Session = Depends(get_session),
):
    try:
        return _eval2_service(request).research_link(
            session,
            link_id=link_id,
            player_resolver=_player_resolver(request),
            research_cutoff=payload.research_cutoff,
            target_dimensions=payload.target_dimensions,
            situation_id=payload.situation_id,
            trigger_id=payload.trigger_id,
            durable_context=payload.durable_context,
            retry_failed=payload.retry_failed,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/results/{result_id}/extract-evidence")
def extract_result_evidence(
    result_id: str,
    payload: EvidenceExtractionEval2Request,
    request: Request,
    session: Session = Depends(get_session),
):
    try:
        return _eval2_service(request).extract_atomic_evidence(
            session,
            result_id=result_id,
            research_cutoff=payload.research_cutoff,
            situation_id=payload.situation_id,
            trigger_id=payload.trigger_id,
            durable_context=payload.durable_context,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


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
