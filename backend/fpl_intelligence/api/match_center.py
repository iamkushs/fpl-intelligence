from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from fpl_intelligence.api.research import get_session
from fpl_intelligence.match_center.service import MatchCenterService, MatchCenterConfigurationError

router=APIRouter(prefix="/fpl/match-center",tags=["match-center"])
class RefreshInput(BaseModel): gameweek:int=Field(ge=1)
def service(request:Request): return MatchCenterService(request.app.state.fpl_adapter,request.app.state.fpl_manager_provider)
@router.post("/refresh")
def refresh(body:RefreshInput,request:Request,session:Session=Depends(get_session)):
    try:return service(request).refresh(session,body.gameweek)
    except (MatchCenterConfigurationError,LookupError) as exc: raise HTTPException(422,detail=str(exc)) from exc
@router.get("")
def read(request:Request,gameweek:int|None=None,session:Session=Depends(get_session)):
    result=service(request).get_match_center(session,gameweek) if gameweek is not None else service(request).get_latest_match_center(session)
    if result is None: raise HTTPException(404,detail="No durable Match Center snapshot is available")
    return result
@router.get("/{gameweek}")
def read_gameweek(gameweek:int,request:Request,session:Session=Depends(get_session)):
    result=service(request).get_match_center(session,gameweek)
    if result is None: raise HTTPException(404,detail="No durable Match Center snapshot is available")
    return result
