from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from fpl_intelligence.api.research import get_session
from fpl_intelligence.squads.service import PairConfigurationError, PairSquadService

router=APIRouter(prefix="/fpl/pair-view", tags=["pair-view"])
class PairInput(BaseModel): name:str=Field(min_length=1,max_length=255); entry_ids:list[int]
class ConfigInput(BaseModel): our_pair:PairInput; opponent_pair:PairInput
class SyncInput(BaseModel): gameweek:int=Field(ge=1)
def service(request:Request): return PairSquadService(request.app.state.fpl_manager_provider)
@router.put("/config")
def configure(body:ConfigInput,request:Request,session:Session=Depends(get_session)):
    try:return service(request).configure_pairs(session,our_pair=body.our_pair.model_dump(),opponent_pair=body.opponent_pair.model_dump())
    except PairConfigurationError as exc: raise HTTPException(422,detail=str(exc)) from exc
@router.get("/config")
def config(request:Request,session:Session=Depends(get_session)): return service(request).get_configuration(session)
@router.post("/sync")
def sync(body:SyncInput,request:Request,session:Session=Depends(get_session)):
    results=service(request).sync_all(session,body.gameweek); return {"results":results,"pair_view":service(request).get_pair_view(session,body.gameweek)}
@router.get("")
def view(request:Request,gameweek:int|None=None,session:Session=Depends(get_session)):
    result=service(request).get_pair_view(session,gameweek)
    if result is None: raise HTTPException(404,detail="No durable pair squad snapshots are available")
    return result
@router.get("/history")
def history(request:Request,session:Session=Depends(get_session)): return service(request).get_pair_history(session)
