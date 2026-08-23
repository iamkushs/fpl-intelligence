from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from fpl_intelligence.api.research import get_session
from fpl_intelligence.briefing.service import GameweekBriefingService

router = APIRouter(prefix="/fpl", tags=["briefing"])
class RefreshInput(BaseModel): gameweek: int | None = Field(default=None, ge=1)

@router.get("/briefing")
def briefing(session: Session = Depends(get_session)):
    return GameweekBriefingService(None).briefing(session)

@router.post("/gameweek/refresh")
def refresh_gameweek(body: RefreshInput, request: Request, session: Session = Depends(get_session)):
    try: return GameweekBriefingService(request.app.state.fpl_adapter).refresh(session, body.gameweek).to_dict()
    except ValueError as exc: raise HTTPException(422, detail=str(exc)) from exc
