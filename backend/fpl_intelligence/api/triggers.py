"""Research trigger queue and monitoring-trigger lifecycle endpoints."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from fpl_intelligence.api.research import get_session
from fpl_intelligence.watchlist.triggers import TriggerService

router = APIRouter(tags=["research triggers"])


class TriggerResponse(BaseModel):
    id: str
    player_id: int
    trigger_type: str
    source: str
    status: str
    priority: int
    description: str
    gameweek: int | None
    evidence: dict | None
    monitoring_trigger_id: str | None
    situation_id: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    dismissed_at: datetime | None


class MonitoringTriggerResponse(BaseModel):
    id: str
    player_id: int
    research_result_id: str | None
    research_thread_id: str | None
    description: str
    category: str
    active: bool
    condition: dict | None
    created_at: datetime
    satisfied_at: datetime | None
    retired_at: datetime | None


class PlayerTriggersResponse(BaseModel):
    research_triggers: list[TriggerResponse]
    monitoring_triggers: list[MonitoringTriggerResponse]


class ManualTriggerRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class MonitoringTriggerRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    category: Literal[
        "appearance", "minutes", "attacking_return", "set_piece", "availability",
        "team_selection", "transfer", "tactical_role", "fixture", "manager_comment", "other",
    ]
    condition: dict | None = None
    research_result_id: str | None = None
    research_thread_id: str | None = None


class EvaluationResponse(BaseModel):
    gameweek: int
    players_evaluated: int
    new_research_triggers: int
    resolved_triggers: int
    satisfied_monitoring_triggers: int
    duplicates_skipped: int
    insufficient_history_players: int
    failures: list[dict[str, object]]


class QueueSituationPlayerResponse(BaseModel):
    player_id: int
    player_name: str
    club: str
    position: str


class QueuePlayerResponse(BaseModel):
    player_id: int
    player_name: str
    club: str
    position: str
    triggers: list[TriggerResponse]
    primary_trigger: TriggerResponse
    from_previous_monitoring: bool
    most_recent_research_at: datetime | None
    situation_id: str | None = None
    situation_title: str | None = None
    other_involved_players: list[QueueSituationPlayerResponse] = Field(default_factory=list)


def trigger_response(item) -> TriggerResponse:
    return TriggerResponse.model_validate(item, from_attributes=True)


def monitoring_response(item) -> MonitoringTriggerResponse:
    return MonitoringTriggerResponse.model_validate(item, from_attributes=True)


@router.post("/watchlist/triggers/{gameweek}/evaluate", response_model=EvaluationResponse)
def evaluate(gameweek: int, session: Session = Depends(get_session)):
    try:
        return EvaluationResponse(**TriggerService().evaluate_watchlist_triggers(session, gameweek).to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/research/queue", response_model=list[QueuePlayerResponse])
def research_queue(request: Request, session: Session = Depends(get_session)):
    official = {player.id: player for player in request.app.state.fpl_snapshot_service.get_snapshot().players}
    response = []
    for item in TriggerService.queue(session):
        player = official.get(item.player_id)
        if player is None:
            continue
        other_players = []
        if item.situation is not None:
            for involved in item.situation.players:
                if involved.id == item.player_id:
                    continue
                official_involved = official.get(involved.id)
                if official_involved is None:
                    continue
                other_players.append(QueueSituationPlayerResponse(
                    player_id=official_involved.id,
                    player_name=official_involved.display_name,
                    club=official_involved.club_name,
                    position=official_involved.position,
                ))
        response.append(QueuePlayerResponse(
            player_id=item.player_id, player_name=player.display_name, club=player.club_name,
            position=player.position, triggers=[trigger_response(t) for t in item.triggers],
            primary_trigger=trigger_response(item.primary_trigger),
            from_previous_monitoring=item.from_previous_monitoring,
            most_recent_research_at=item.most_recent_research_at,
            situation_id=item.situation.id if item.situation is not None else None,
            situation_title=item.situation.title if item.situation is not None else None,
            other_involved_players=other_players,
        ))
    return response


@router.get("/fpl/players/{player_id}/triggers", response_model=PlayerTriggersResponse)
def player_triggers(player_id: int, session: Session = Depends(get_session)):
    triggers, monitors = TriggerService.player_triggers(session, player_id)
    return PlayerTriggersResponse(
        research_triggers=[trigger_response(t) for t in triggers],
        monitoring_triggers=[monitoring_response(t) for t in monitors],
    )


@router.post("/fpl/players/{player_id}/trigger-research", response_model=TriggerResponse)
def manual_trigger(player_id: int, payload: ManualTriggerRequest | None = None,
                   session: Session = Depends(get_session)):
    try:
        return trigger_response(TriggerService().manual_trigger(session, player_id, payload.reason if payload else None))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/research/triggers/{trigger_id}/dismiss", response_model=TriggerResponse)
def dismiss(trigger_id: str, session: Session = Depends(get_session)):
    try:
        return trigger_response(TriggerService.dismiss(session, trigger_id))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/fpl/players/{player_id}/monitoring-triggers", response_model=MonitoringTriggerResponse,
             status_code=status.HTTP_201_CREATED)
def create_monitoring(player_id: int, payload: MonitoringTriggerRequest,
                      session: Session = Depends(get_session)):
    try:
        return monitoring_response(TriggerService.create_monitoring(
            session, player_id=player_id, description=payload.description, category=payload.category,
            condition=payload.condition, research_result_id=payload.research_result_id,
            research_thread_id=payload.research_thread_id,
        ))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/research/monitoring-triggers/{monitor_id}/retire", response_model=MonitoringTriggerResponse)
def retire_monitoring(monitor_id: str, session: Session = Depends(get_session)):
    try:
        return monitoring_response(TriggerService.retire_monitoring(session, monitor_id))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
