from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from fpl_intelligence.api.research import get_session
from fpl_intelligence.models import (ResearchQueueItem, PlayerResearchTrigger, MonitoringTrigger, ResearchTriggerStatus,
    WatchlistEntry, PlayerGameweekPulse, ResearchDeepRun, ResearchPlayerSynthesis, ResearchCycle, ResearchCyclePlayer, Player)
from fpl_intelligence.research.queue import ResearchQueueService

router=APIRouter(prefix="/fpl",tags=["research-center"]); service=ResearchQueueService()
class Add(BaseModel): player_id:int; source:str="user"; reason:str|None=None; source_context:dict|None=None; trigger_id:str|None=None; research_situation_id:str|None=None
class Reorder(BaseModel): item_ids:list[str]
class Snooze(BaseModel): until_gameweek:int=Field(ge=1)
class Run(BaseModel): limit:int=Field(default=15,ge=1,le=15); item_ids:list[str]|None=None
def out(x): return {"id":x.id,"player_id":x.player_id,"player_name":x.player.display_name or f"{x.player.first_name or ''} {x.player.second_name or ''}".strip(),"status":x.status,"source":x.source,"reason":x.reason,"queue_order":x.queue_order,"requested_gameweek":x.requested_gameweek,"snoozed_until_gameweek":x.snoozed_until_gameweek,"source_context":x.source_context,"deep_run_id":x.deep_run_id,"created_at":x.created_at,"updated_at":x.updated_at}
@router.get("/research-queue")
def queue(session:Session=Depends(get_session)): return [out(x) for x in service.get_queue(session,include_snoozed=True)]
@router.post("/research-queue",status_code=status.HTTP_201_CREATED)
def add(payload:Add,session:Session=Depends(get_session)):
    try:return out(service.add_player(session,**payload.model_dump()))
    except LookupError as e: raise HTTPException(404,detail=str(e))
    except ValueError as e: raise HTTPException(422,detail=str(e))
@router.post("/research-queue/reorder")
def reorder(payload:Reorder,session:Session=Depends(get_session)):
    try:return [out(x) for x in service.reorder_queue(session,payload.item_ids)]
    except (LookupError,ValueError) as e: raise HTTPException(422,detail=str(e))
@router.post("/research-queue/{item_id}/remove")
def remove(item_id:str,session:Session=Depends(get_session)): return out(service.remove_item(session,item_id))
@router.post("/research-queue/{item_id}/snooze")
def snooze(item_id:str,payload:Snooze,session:Session=Depends(get_session)): return out(service.snooze_item(session,item_id,payload.until_gameweek))
@router.post("/research-queue/{item_id}/unsnooze")
def unsnooze(item_id:str,session:Session=Depends(get_session)): return out(service.unsnooze_item(session,item_id))
@router.post("/research-queue/{item_id}/retry")
def retry(item_id:str,session:Session=Depends(get_session)): return out(service.retry_failed_item(session,item_id))
@router.get("/research-center")
def research_center(session:Session=Depends(get_session)):
    items=service.get_queue(session,include_snoozed=True); history=service.get_recent_history(session)
    active_ids={x.player_id for x in items if x.status in {"queued","running","snoozed"}}
    triggers=list(session.scalars(select(PlayerResearchTrigger).where(PlayerResearchTrigger.status.in_(ResearchTriggerStatus.ACTIVE)).order_by(PlayerResearchTrigger.priority.desc(),PlayerResearchTrigger.created_at.desc()).limit(100)))
    monitors=list(session.scalars(select(MonitoringTrigger).where(MonitoringTrigger.active.is_(True)).order_by(MonitoringTrigger.created_at.desc()).limit(100)))
    signals=[{"id":t.id,"player_id":t.player_id,"signal_type":t.trigger_type,"reason":t.description,"gameweek":t.gameweek,"source":t.source,"priority":t.priority,"created_at":t.created_at,"already_queued":t.player_id in active_ids,"trigger_id":t.id} for t in triggers]
    signals += [{"id":m.id,"player_id":m.player_id,"signal_type":m.category,"reason":m.description,"context":m.condition,"gameweek":None,"source":"monitoring","priority":None,"created_at":m.created_at,"already_queued":m.player_id in active_ids,"monitoring_trigger_id":m.id} for m in monitors]
    watch_ids=list(session.scalars(select(WatchlistEntry.player_id).where(WatchlistEntry.active.is_(True))))
    pulses=list(session.scalars(select(PlayerGameweekPulse).options(selectinload(PlayerGameweekPulse.player)).where(PlayerGameweekPulse.player_id.in_(watch_ids)).order_by(PlayerGameweekPulse.player_id,PlayerGameweekPulse.gameweek.desc()))) if watch_ids else []
    monitoring=[]
    for player_id in watch_ids:
        rows=[p for p in pulses if p.player_id==player_id][:5]
        if rows:
            player=rows[0].player
            monitoring.append({"player_id":player_id,"player_name":player.display_name,"price":player.price,"availability":player.availability_status,"ownership_percent":player.ownership_percent,"pulses":[{"gameweek":p.gameweek,"minutes":p.minutes,"starts":p.starts,"total_points":p.total_points,"goals":p.goals_scored,"assists":p.assists,"clean_sheets":p.clean_sheets,"bonus":p.bonus,"xG":p.expected_goals,"xA":p.expected_assists,"xGI":p.expected_goal_involvements,"xGC":p.expected_goals_conceded} for p in rows]})
    synth=list(session.scalars(select(ResearchPlayerSynthesis).options(selectinload(ResearchPlayerSynthesis.deep_run)).order_by(ResearchPlayerSynthesis.created_at.desc()).limit(20)))
    recent=[{"id":s.id,"player_id":s.player_id,"player_name":(session.get(PlayerGameweekPulse,s.player_id).player.display_name if False else None),"completed_at":s.created_at,"overall_research_state":s.overall_research_state,"research_cutoff":s.research_cutoff,"deep_run_id":s.deep_run_id} for s in synth]
    player_names={p.id:p.display_name for p in session.scalars(select(Player).where(Player.id.in_([s.player_id for s in synth])))} if synth else {}
    for x in recent: x["player_name"]=player_names.get(x["player_id"])
    cycles=list(session.scalars(select(ResearchCycle).options(selectinload(ResearchCycle.players)).order_by(ResearchCycle.created_at.desc()).limit(10)))
    runs=[{"id":c.id,"gameweek":c.gameweek,"status":c.status,"started_at":c.started_at,"completed_at":c.completed_at,"player_count":len(c.players),"completed_count":sum(p.state=="researched" for p in c.players),"failed_count":sum(p.state=="failed" for p in c.players),"running_count":sum(p.state in {"researching","selected"} for p in c.players)} for c in cycles]
    return {"queue":[out(x) for x in items if x.status in {"queued","running"}],"snoozed":[out(x) for x in items if x.status=="snoozed"],"recent_research":recent,"attention_signals":signals,"watchlist_monitoring":monitoring,"recent_cycles":runs}

@router.post("/research-signals/{signal_id}/queue")
def queue_signal(signal_id:str,session:Session=Depends(get_session)):
    trigger=session.get(PlayerResearchTrigger,signal_id)
    if trigger is None: raise HTTPException(404,detail="Signal not found")
    try: return out(service.add_player(session,player_id=trigger.player_id,source="accepted_signal",reason=trigger.description,trigger_id=trigger.id,research_situation_id=trigger.situation_id,source_context={"trigger_type":trigger.trigger_type,"gameweek":trigger.gameweek,"signal_source":trigger.source}))
    except LookupError as exc: raise HTTPException(404,detail=str(exc))

@router.post("/research-queue/run")
def run(payload:Run, request:Request, session:Session=Depends(get_session)):
    current=session.scalar(select(ResearchQueueItem).order_by(ResearchQueueItem.created_at.desc()))
    gameweek=current.requested_gameweek if current and current.requested_gameweek else 1
    try:
        items=service.run(session,orchestrator=request.app.state.weekly_research_orchestrator,deep_service=request.app.state.deep_player_research_service,gameweek=gameweek,research_cutoff=datetime.now(timezone.utc),limit=payload.limit,item_ids=payload.item_ids)
        return [out(x) for x in items]
    except (LookupError,ValueError) as exc: raise HTTPException(422,detail=str(exc))
