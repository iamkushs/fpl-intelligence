"""Canonical official FPL state and player research views."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fpl_intelligence.api.research import EvidenceResponse, _evidence_responses, get_session
from fpl_intelligence.integrations.fpl.errors import OfficialFPLError
from fpl_intelligence.integrations.fpl.snapshot import FPLSnapshotService
from fpl_intelligence.models import Player, WatchlistEntry
from fpl_intelligence.research.persistence import ResearchPersistenceService
from fpl_intelligence.research.situations import ResearchSituationService
from fpl_intelligence.research.evidence import ResearchEvidenceService
from fpl_intelligence.watchlist.service import WatchlistService
from fpl_intelligence.watchlist.pulse import PlayerPulseService
from fpl_intelligence.watchlist.triggers import TriggerService
from fpl_intelligence.api.triggers import (
    MonitoringTriggerResponse, TriggerResponse, monitoring_response, trigger_response,
)

router = APIRouter(prefix="/fpl", tags=["fpl"])


@router.get("/players/{player_id}/evidence", response_model=list[EvidenceResponse])
def get_player_evidence(player_id: int, session: Session = Depends(get_session)):
    if session.get(Player, player_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    service = ResearchEvidenceService()
    return _evidence_responses(service.list_evidence(session, player_id=player_id), service, session)


class PlayerIdentityResponse(BaseModel):
    id: int
    first_name: str
    second_name: str
    display_name: str
    club_id: int
    club_name: str
    club_short_name: str
    position: str
    price: float
    ownership_percent: float | None
    availability_status: str
    chance_of_playing_next_round: int | None
    news: str | None


class PlayerSearchResponse(BaseModel):
    player_id: int
    player_name: str
    club: str
    position: str
    price: float
    ownership_percent: float | None
    watchlisted: bool


class PlayerResearchResultResponse(BaseModel):
    id: str
    summary: str
    findings: str
    uncertainty: str | None
    researched_at: datetime
    source_url: str
    source_title: str | None
    source_domain: str
    source_type: str | None
    thread_id: str
    thread_title: str
    thread_type: str


class PlayerCollectedLinkResponse(BaseModel):
    id: str
    url: str
    title: str | None
    domain: str
    source_type: str | None
    relevance_reason: str | None
    status: str
    discovered_at: datetime
    thread_id: str
    thread_title: str
    thread_type: str


class PlayerDetailsResponse(BaseModel):
    player: PlayerIdentityResponse
    watchlist: "PlayerWatchlistResponse"
    current_research_context: list["PlayerSituationContextResponse"]
    completed_research: list[PlayerResearchResultResponse]
    collected_sources: list[PlayerCollectedLinkResponse]
    recent_pulses: list["PlayerGameweekPulseResponse"]
    recent_pulse_summary: "PlayerPulseSummaryResponse"
    research_triggers: list[TriggerResponse]
    monitoring_triggers: list[MonitoringTriggerResponse]


class PlayerGameweekPulseResponse(BaseModel):
    gameweek: int
    minutes: int | None
    starts: int | None
    total_points: int | None
    goals_scored: int | None
    assists: int | None
    clean_sheets: int | None
    goals_conceded: int | None
    own_goals: int | None
    penalties_saved: int | None
    penalties_missed: int | None
    yellow_cards: int | None
    red_cards: int | None
    saves: int | None
    bonus: int | None
    bps: int | None
    expected_goals: float | None
    expected_assists: float | None
    expected_goal_involvements: float | None
    expected_goals_conceded: float | None
    captured_at: datetime
    updated_at: datetime


class PlayerPulseSummaryResponse(BaseModel):
    gameweeks: int
    appearances: int
    attacking_blank_streak: int
    total_goals: int
    total_assists: int
    total_points: int
    average_minutes: float
    total_bonus: int
    total_expected_goals: float
    total_expected_assists: float
    total_expected_goal_involvements: float


class PlayerWatchlistResponse(BaseModel):
    active: bool
    pinned: bool
    added_source: str | None
    addition_reason: str | None
    added_at: datetime | None


class SituationInvolvedPlayerResponse(BaseModel):
    player_id: int
    player_name: str
    club: str
    position: str


class SituationActiveHypothesisResponse(BaseModel):
    id: str
    statement: str


class PlayerSituationContextResponse(BaseModel):
    situation_id: str
    title: str
    status: str
    involved_players: list[SituationInvolvedPlayerResponse]
    active_hypotheses: list[SituationActiveHypothesisResponse]


class PlayerResearchThreadResponse(BaseModel):
    thread_id: str
    title: str
    status: str
    collect_url: str


@router.get("/snapshot")
def get_fpl_snapshot(
    request: Request,
    season_id: str | None = None,
    horizon_start: int | None = Query(default=None, ge=1),
    horizon_end: int | None = Query(default=None, ge=1),
):
    service: FPLSnapshotService = request.app.state.fpl_snapshot_service
    try:
        return service.get_snapshot(
            season_id=season_id,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except OfficialFPLError as exc:
        request.app.state.logger.exception("official_fpl_snapshot_failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


def _snapshot(request: Request):
    service: FPLSnapshotService = request.app.state.fpl_snapshot_service
    try:
        return service.get_snapshot()
    except OfficialFPLError as exc:
        request.app.state.logger.exception("official_fpl_snapshot_failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


def _situation_context_response(situations, request: Request) -> list[PlayerSituationContextResponse]:
    official = {player.id: player for player in _snapshot(request).players}
    response = []
    for situation in situations:
        involved = []
        for player in sorted(situation.players, key=lambda item: item.id):
            official_player = official.get(player.id)
            if official_player is None:
                continue
            involved.append(SituationInvolvedPlayerResponse(
                player_id=official_player.id,
                player_name=official_player.display_name,
                club=official_player.club_name,
                position=official_player.position,
            ))
        response.append(PlayerSituationContextResponse(
            situation_id=situation.id,
            title=situation.title,
            status=situation.status,
            involved_players=involved,
            active_hypotheses=[
                SituationActiveHypothesisResponse(id=item.id, statement=item.statement)
                for item in situation.hypotheses
                if item.active
            ],
        ))
    return response


@router.get("/players", response_model=list[PlayerSearchResponse])
def search_persisted_players(
    request: Request,
    search: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    session: Session = Depends(get_session),
):
    """Search persisted players without creating or synchronizing player rows."""
    term = search.strip().casefold()
    if not term:
        return []
    persisted = dict(session.execute(
        select(Player.id, WatchlistEntry.active)
        .outerjoin(WatchlistEntry, WatchlistEntry.player_id == Player.id)
    ).all())
    matches = []
    for official in _snapshot(request).players:
        if official.id not in persisted:
            continue
        haystack = f"{official.first_name} {official.second_name} {official.display_name} {official.club_name}".casefold()
        if term not in haystack:
            continue
        matches.append(PlayerSearchResponse(
            player_id=official.id,
            player_name=official.display_name,
            club=official.club_name,
            position=official.position,
            price=official.price,
            ownership_percent=official.ownership_percent,
            watchlisted=bool(persisted[official.id]),
        ))
    matches.sort(key=lambda player: (player.player_name.casefold(), player.player_id))
    return matches[:limit]


@router.get("/players/{player_id}", response_model=PlayerDetailsResponse)
def get_player_details(
    player_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    details = ResearchPersistenceService().get_player_details(session, player_id)
    official = next((player for player in _snapshot(request).players if player.id == player_id), None)
    if details is None or official is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    _, results, links = details
    watchlist = WatchlistService().get(session, player_id)
    pulses = PlayerPulseService.recent_history(session, player_id, limit=5)
    triggers, monitors = TriggerService.player_triggers(session, player_id)
    situations = ResearchSituationService().list_for_player(session, player_id)
    return PlayerDetailsResponse(
        player=PlayerIdentityResponse.model_validate(official, from_attributes=True),
        watchlist=PlayerWatchlistResponse(
            active=bool(watchlist and watchlist.active),
            pinned=bool(watchlist and watchlist.active and watchlist.pinned),
            added_source=watchlist.added_source if watchlist and watchlist.active else None,
            addition_reason=watchlist.addition_reason if watchlist and watchlist.active else None,
            added_at=watchlist.added_at if watchlist and watchlist.active else None,
        ),
        current_research_context=_situation_context_response(situations, request),
        completed_research=[
            PlayerResearchResultResponse(
                id=result.id,
                summary=result.summary,
                findings=result.findings,
                uncertainty=result.uncertainty,
                researched_at=result.researched_at,
                source_url=result.research_link.original_url,
                source_title=result.research_link.title,
                source_domain=result.research_link.domain,
                source_type=result.research_link.source_type,
                thread_id=result.thread.id,
                thread_title=result.thread.title,
                thread_type=result.thread.thread_type,
            )
            for result in results
        ],
        collected_sources=[
            PlayerCollectedLinkResponse(
                id=link.id,
                url=link.original_url,
                title=link.title,
                domain=link.domain,
                source_type=link.source_type,
                relevance_reason=link.relevance_reason,
                status=link.status,
                discovered_at=link.discovered_at,
                thread_id=link.thread.id,
                thread_title=link.thread.title,
                thread_type=link.thread.thread_type,
            )
            for link in links
        ],
        recent_pulses=[PlayerGameweekPulseResponse.model_validate(pulse, from_attributes=True) for pulse in pulses],
        recent_pulse_summary=PlayerPulseSummaryResponse(**PlayerPulseService.aggregates(pulses)),
        research_triggers=[trigger_response(item) for item in triggers],
        monitoring_triggers=[monitoring_response(item) for item in monitors],
    )


@router.get("/players/{player_id}/situations", response_model=list[PlayerSituationContextResponse])
def get_player_situations(
    player_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    if session.get(Player, player_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    return _situation_context_response(ResearchSituationService().list_for_player(session, player_id), request)


@router.post(
    "/players/{player_id}/research",
    response_model=PlayerResearchThreadResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_player_research(
    player_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    snapshot = _snapshot(request)
    official = next((player for player in snapshot.players if player.id == player_id), None)
    if official is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    current_gameweek = snapshot.current_gameweek.number if snapshot.current_gameweek else None
    try:
        thread = ResearchPersistenceService().create_player_research_thread(
            session,
            player_id=player_id,
            player_name=f"{official.first_name} {official.second_name}",
            gameweek_id=current_gameweek,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PlayerResearchThreadResponse(
        thread_id=thread.id,
        title=thread.title,
        status=thread.status,
        collect_url=f"/research/threads/{thread.id}/collect",
    )
