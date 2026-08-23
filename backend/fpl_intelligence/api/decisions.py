"""Explicit user-controlled Decision Center endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from fpl_intelligence.api.research import get_session
from fpl_intelligence.decisions.service import DecisionError, DecisionService
from fpl_intelligence.decisions.analysis import DecisionAnalysisError, DecisionAnalysisService
from fpl_intelligence.squads.service import PairSquadService

router = APIRouter(prefix="/fpl/decisions", tags=["decisions"])


class SessionInput(BaseModel):
    manager_id: int
    gameweek: int = Field(ge=1)


class MovementInput(BaseModel):
    outgoing_player_id: int
    incoming_player_id: int


class TransfersInput(BaseModel):
    movements: list[MovementInput]


class SelectInput(BaseModel):
    option_id: str
class ResearchQueueInput(BaseModel):
    player_ids: list[int] | None = None


def _players(request: Request) -> dict[int, object]:
    return {item.id: item for item in request.app.state.fpl_snapshot_service.get_snapshot().players}


def _option(option, players: dict[int, object]) -> dict:
    return {"id": option.id, "type": option.option_type, "is_legal": option.is_legal,
            "validation_errors": option.validation_errors, "budget_available": option.budget_available,
            "budget_required": option.budget_required,
            "movements": [{"outgoing_player_id": item.outgoing_player_id, "incoming_player_id": item.incoming_player_id,
                            "outgoing_player": _player(players.get(item.outgoing_player_id)), "incoming_player": _player(players.get(item.incoming_player_id)),
                            "outgoing_synthesis_id": item.outgoing_synthesis_id, "incoming_synthesis_id": item.incoming_synthesis_id}
                           for item in option.movements]}


def _player(player):
    return None if player is None else {"id": player.id, "display_name": player.display_name, "position": player.position,
                                         "club_id": player.club_id, "club_name": player.club_name, "price": player.price}


def _response(item, request: Request, session: Session) -> dict:
    players = _players(request)
    exposure = PairSquadService().get_pair_view(session, item.gameweek)
    relevant = {pick.player_id for pick in item.frozen_picks}
    for option in item.options:
        for movement in option.movements: relevant.update((movement.outgoing_player_id, movement.incoming_player_id))
    exposures = [] if exposure is None else [row for row in exposure["exposure"] if row["player"]["id"] in relevant]
    analysis=DecisionAnalysisService(); latest=analysis.latest(session,item.id); gaps=analysis.gaps(session,item.id)
    return {"id": item.id, "manager_id": item.manager_id, "manager_entry_id": item.manager.entry_id, "gameweek": item.gameweek,
            "snapshot_id": item.snapshot_id, "frozen_bank": item.frozen_bank, "status": item.status,
            "selected_option_id": item.selected_option_id, "finalized_option_id": item.finalized_option_id,
            "finalized_at": item.finalized_at, "frozen_squad": [{"player_id": pick.player_id, "squad_position": pick.squad_position,
            "selling_price": pick.selling_price, "player": _player(players.get(pick.player_id))} for pick in item.frozen_picks],
            "options": [_option(option, players) for option in item.options], "exposure": exposures, "research_gaps": gaps,
            "latest_analysis": _analysis(latest), "analysis_history": [_analysis(row) for row in analysis.history(session,item.id)]}

def _analysis(row):
    if row is None: return None
    return {"id":row.id,"status":row.status,"outcome":row.outcome,"recommended_option_id":row.recommended_option_id,"confidence":row.confidence,"executive_summary":row.executive_summary,"key_tradeoffs":row.key_tradeoffs,"key_risks":row.key_risks,"contradictions":row.contradictions,"missing_information":row.missing_information,"what_could_change_decision":row.what_could_change_decision,"research_cutoff":row.research_cutoff,"created_at":row.created_at,"failure_reason":row.failure_reason}


def _error(exc: Exception):
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(body: SessionInput, request: Request, session: Session = Depends(get_session)):
    try: item = DecisionService().create_or_reuse(session, manager_id=body.manager_id, gameweek=body.gameweek); session.commit(); item = DecisionService().get(session, item.id)
    except (DecisionError, LookupError) as exc: session.rollback(); _error(exc)
    return _response(item, request, session)


@router.get("/sessions")
def list_sessions(request: Request, manager_id: int | None = None, session: Session = Depends(get_session)):
    return [_response(item, request, session) for item in DecisionService().list(session, manager_id)]


@router.get("/sessions/{session_id}")
def get_decision_session(session_id: str, request: Request, session: Session = Depends(get_session)):
    try: return _response(DecisionService().get(session, session_id), request, session)
    except LookupError as exc: raise HTTPException(404, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/hold", status_code=status.HTTP_201_CREATED)
def add_hold(session_id: str, request: Request, session: Session = Depends(get_session)):
    try: DecisionService().add_hold(session, session_id, _players(request)); session.commit(); item = DecisionService().get(session, session_id)
    except (DecisionError, LookupError) as exc: session.rollback(); _error(exc)
    return _response(item, request, session)


@router.post("/sessions/{session_id}/transfers", status_code=status.HTTP_201_CREATED)
def add_transfers(session_id: str, body: TransfersInput, request: Request, session: Session = Depends(get_session)):
    try: DecisionService().add_transfers(session, session_id, [item.model_dump() for item in body.movements], _players(request)); session.commit(); item = DecisionService().get(session, session_id)
    except (DecisionError, LookupError) as exc: session.rollback(); _error(exc)
    return _response(item, request, session)


@router.post("/sessions/{session_id}/select")
def select_option(session_id: str, body: SelectInput, request: Request, session: Session = Depends(get_session)):
    try: item = DecisionService().select(session, session_id, body.option_id); session.commit(); item = DecisionService().get(session, item.id)
    except (DecisionError, LookupError) as exc: session.rollback(); _error(exc)
    return _response(item, request, session)


@router.post("/sessions/{session_id}/finalize")
def finalize(session_id: str, request: Request, session: Session = Depends(get_session)):
    try: item = DecisionService().finalize(session, session_id); session.commit(); item = DecisionService().get(session, item.id)
    except (DecisionError, LookupError) as exc: session.rollback(); _error(exc)
    return _response(item, request, session)

@router.post("/sessions/{session_id}/analyze")
def analyze_session(session_id: str, request: Request, session: Session = Depends(get_session)):
    try:
        DecisionAnalysisService().analyze(session,session_id,request.app.state.codex_service); session.commit(); return _response(DecisionService().get(session,session_id),request,session)
    except (DecisionAnalysisError, DecisionError, LookupError) as exc: session.rollback(); _error(exc)

@router.get("/sessions/{session_id}/analysis")
def latest_analysis(session_id: str, session: Session = Depends(get_session)):
    if DecisionService().get(session,session_id) is None: raise HTTPException(404,detail="decision_session_not_found")
    return _analysis(DecisionAnalysisService().latest(session,session_id))

@router.get("/sessions/{session_id}/analysis/history")
def analysis_history(session_id: str, session: Session = Depends(get_session)):
    DecisionService().get(session,session_id); return [_analysis(row) for row in DecisionAnalysisService().history(session,session_id)]

@router.post("/sessions/{session_id}/research-queue")
def queue_decision_research(session_id: str, body: ResearchQueueInput, request: Request, session: Session = Depends(get_session)):
    try: DecisionAnalysisService().queue_gaps(session,session_id,body.player_ids); session.commit(); return _response(DecisionService().get(session,session_id),request,session)
    except (DecisionAnalysisError, DecisionError, LookupError, ValueError) as exc: session.rollback(); _error(exc)
