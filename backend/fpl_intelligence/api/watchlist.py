"""Manual Watchlist management endpoints."""

from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fpl_intelligence.api.research import get_session
from fpl_intelligence.watchlist.service import WatchlistService
from fpl_intelligence.models import Player, WatchlistEntry
from fpl_intelligence.watchlist.discovery import DiscoveryService
from fpl_intelligence.watchlist.pulse import PlayerPulseService
from fpl_intelligence.integrations.fpl.errors import OfficialFPLError
from fpl_intelligence.models import PlayerResearchTrigger, ResearchTriggerStatus

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class AddRequest(BaseModel):
    reason: str | None = None
    pinned: bool = False


class RemoveRequest(BaseModel):
    reason: str | None = None


class PinRequest(BaseModel):
    pinned: bool


class MembershipResponse(BaseModel):
    player_id: int
    active: bool
    pinned: bool
    added_source: str
    addition_reason: str | None
    added_at: datetime
    removed_at: datetime | None
    removal_reason: str | None


class WatchlistPlayerResponse(MembershipResponse):
    player_name: str
    club: str
    position: str
    price: float
    ownership_percent: float | None
    last_research_at: datetime | None
    latest_pulse_gameweek: int | None = None
    latest_pulse_points: int | None = None
    latest_pulse_minutes: int | None = None
    attacking_blank_streak: int = 0
    research_needed: bool = False
    open_trigger_count: int = 0
    primary_trigger_reason: str | None = None
    primary_trigger_source: str | None = None


class PulseRunResponse(BaseModel):
    gameweek: int
    active_watchlist_players_considered: int
    pulses_created: int
    pulses_updated: int
    players_with_no_usable_gameweek_data: list[int]
    failures: list[dict[str, object]]


class PlayerOptionResponse(BaseModel):
    player_id: int
    player_name: str
    club: str
    position: str
    price: float
    watchlisted: bool


class SuggestionEvidenceResponse(BaseModel):
    research_result_id: str
    source_url: str
    summary: str


class SuggestionResponse(BaseModel):
    id: str
    player_id: int
    player_name: str
    club: str
    position: str
    price: float
    reason: str
    status: str
    created_at: datetime
    reviewed_at: datetime | None
    research_thread_id: str
    research_thread_title: str
    research_thread_question: str | None
    evidence: list[SuggestionEvidenceResponse]


class AcceptedSuggestionResponse(BaseModel):
    suggestion: SuggestionResponse
    watchlist: WatchlistPlayerResponse


def membership(entry) -> MembershipResponse:
    return MembershipResponse(
        player_id=entry.player_id, active=entry.active, pinned=entry.pinned,
        added_source=entry.added_source, addition_reason=entry.addition_reason,
        added_at=entry.added_at, removed_at=entry.removed_at, removal_reason=entry.removal_reason,
    )


def suggestion_response(suggestion, official) -> SuggestionResponse:
    return SuggestionResponse(
        id=suggestion.id, player_id=suggestion.player_id, player_name=official.display_name,
        club=official.club_name, position=official.position, price=official.price,
        reason=suggestion.reason, status=suggestion.status, created_at=suggestion.created_at,
        reviewed_at=suggestion.reviewed_at, research_thread_id=suggestion.research_thread_id,
        research_thread_title=suggestion.thread.title, research_thread_question=suggestion.thread.question,
        evidence=[SuggestionEvidenceResponse(
            research_result_id=result.id, source_url=result.research_link.original_url,
            summary=result.summary,
        ) for result in suggestion.research_results],
    )


@router.get("", response_model=list[WatchlistPlayerResponse])
def list_watchlist(request: Request, session: Session = Depends(get_session)):
    players = {player.id: player for player in request.app.state.fpl_snapshot_service.get_snapshot().players}
    rows = WatchlistService().list_active(session)
    histories = PlayerPulseService.recent_histories(session, [entry.player_id for entry, _ in rows])
    active_ids = [entry.player_id for entry, _ in rows]
    trigger_rows = list(session.scalars(select(PlayerResearchTrigger).where(
        PlayerResearchTrigger.player_id.in_(active_ids),
        PlayerResearchTrigger.status.in_(ResearchTriggerStatus.ACTIVE),
    ).order_by(PlayerResearchTrigger.player_id, PlayerResearchTrigger.priority.desc(),
               PlayerResearchTrigger.created_at.asc()))) if active_ids else []
    triggers_by_player = {player_id: [] for player_id in active_ids}
    for trigger in trigger_rows:
        triggers_by_player[trigger.player_id].append(trigger)
    response = []
    for entry, last_research_at in rows:
        player = players.get(entry.player_id)
        if player is None:
            continue
        pulses = histories[entry.player_id]
        latest = pulses[0] if pulses else None
        triggers = triggers_by_player[entry.player_id]
        primary = triggers[0] if triggers else None
        response.append(WatchlistPlayerResponse(
            **membership(entry).model_dump(), player_name=player.display_name,
            club=player.club_name, position=player.position, price=player.price,
            ownership_percent=player.ownership_percent, last_research_at=last_research_at,
            latest_pulse_gameweek=latest.gameweek if latest else None,
            latest_pulse_points=latest.total_points if latest else None,
            latest_pulse_minutes=latest.minutes if latest else None,
            attacking_blank_streak=PlayerPulseService.aggregates(pulses)["attacking_blank_streak"],
            research_needed=bool(triggers), open_trigger_count=len(triggers),
            primary_trigger_reason=primary.description if primary else None,
            primary_trigger_source=primary.source if primary else None,
        ))
    return response


@router.post("/pulse/{gameweek}", response_model=PulseRunResponse)
def run_watchlist_pulse(gameweek: int, request: Request, session: Session = Depends(get_session)):
    try:
        summary = PlayerPulseService(request.app.state.fpl_adapter).run_watchlist_pulse(session, gameweek)
        return PulseRunResponse(**summary.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except OfficialFPLError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/players", response_model=list[PlayerOptionResponse])
def list_player_options(request: Request, session: Session = Depends(get_session)):
    """Return persisted players enriched from one current FPL snapshot."""
    persisted = session.execute(
        select(Player.id, WatchlistEntry.active)
        .outerjoin(WatchlistEntry, WatchlistEntry.player_id == Player.id)
    ).all()
    players = {player.id: player for player in request.app.state.fpl_snapshot_service.get_snapshot().players}
    return [
        PlayerOptionResponse(
            player_id=player_id, player_name=official.display_name, club=official.club_name,
            position=official.position, price=official.price, watchlisted=bool(active),
        )
        for player_id, active in persisted
        if (official := players.get(player_id)) is not None
    ]


@router.get("/suggestions", response_model=list[SuggestionResponse])
def list_suggestions(request: Request, session: Session = Depends(get_session)):
    players = {player.id: player for player in request.app.state.fpl_snapshot_service.get_snapshot().players}
    return [suggestion_response(item, players[item.player_id])
            for item in request.app.state.discovery_service.list_pending(session)
            if item.player_id in players]


@router.post("/suggestions/{suggestion_id}/accept", response_model=AcceptedSuggestionResponse)
def accept_suggestion(suggestion_id: str, request: Request, session: Session = Depends(get_session)):
    try:
        suggestion = request.app.state.discovery_service.accept(session, suggestion_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    official = next((p for p in request.app.state.fpl_snapshot_service.get_snapshot().players
                     if p.id == suggestion.player_id), None)
    if official is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Official player data unavailable")
    entry = WatchlistService().get(session, suggestion.player_id)
    return AcceptedSuggestionResponse(
        suggestion=suggestion_response(suggestion, official),
        watchlist=WatchlistPlayerResponse(
            **membership(entry).model_dump(), player_name=official.display_name,
            club=official.club_name, position=official.position, price=official.price,
            ownership_percent=official.ownership_percent, last_research_at=max(
                (result.researched_at for result in suggestion.research_results), default=None
            ),
        ),
    )


@router.post("/suggestions/{suggestion_id}/reject", response_model=SuggestionResponse)
def reject_suggestion(suggestion_id: str, request: Request, session: Session = Depends(get_session)):
    try:
        suggestion = request.app.state.discovery_service.reject(session, suggestion_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    official = next((p for p in request.app.state.fpl_snapshot_service.get_snapshot().players
                     if p.id == suggestion.player_id), None)
    if official is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Official player data unavailable")
    return suggestion_response(suggestion, official)


@router.post("/{player_id}", response_model=MembershipResponse)
def add_player(player_id: int, payload: AddRequest | None = Body(default=None), session: Session = Depends(get_session)):
    payload = payload or AddRequest()
    try:
        return membership(WatchlistService().add(session, player_id, reason=payload.reason, pinned=payload.pinned))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{player_id}", response_model=MembershipResponse)
def remove_player(player_id: int, payload: RemoveRequest | None = Body(default=None), session: Session = Depends(get_session)):
    try:
        return membership(WatchlistService().remove(session, player_id, reason=payload.reason if payload else None))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{player_id}/pin", response_model=MembershipResponse)
def pin_player(player_id: int, payload: PinRequest, session: Session = Depends(get_session)):
    try:
        return membership(WatchlistService().set_pinned(session, player_id, payload.pinned))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
