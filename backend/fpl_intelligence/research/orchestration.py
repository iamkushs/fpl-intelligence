"""Durable weekly coordination of the existing monitoring and deep-research pipeline."""
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from fpl_intelligence.models import (PlayerGameweekPulse, PlayerResearchTrigger, ResearchCycle, ResearchCyclePlayer, ResearchCyclePlayerState, ResearchCycleStatus, ResearchDeepRun, ResearchThread, ResearchThreadType, ResearchTriggerStatus, WatchlistEntry)
from fpl_intelligence.watchlist.triggers import TriggerService

class WeeklyResearchOrchestrator:
    orchestration_version="weekly_research_orchestration_v1"
    def __init__(self, *, pulse_service=None, trigger_service=None, deep_service=None): self.pulse_service=pulse_service; self.trigger_service=trigger_service or TriggerService(); self.deep_service=deep_service
    def create_cycle(self,session:Session,*,gameweek:int,research_cutoff:datetime,max_deep_runs:int=15):
        if gameweek<1 or not 1<=max_deep_runs<=15: raise ValueError("gameweek and max_deep_runs are invalid")
        cutoff=_utc(research_cutoff); active={ResearchCycleStatus.PENDING,ResearchCycleStatus.MONITORING,ResearchCycleStatus.PREPARED,ResearchCycleStatus.EXECUTING}
        found=session.scalar(select(ResearchCycle).where(ResearchCycle.gameweek==gameweek,ResearchCycle.research_cutoff==cutoff,ResearchCycle.orchestration_version==self.orchestration_version,ResearchCycle.status.in_(active)))
        if found:return found
        cycle=ResearchCycle(gameweek=gameweek,research_cutoff=cutoff,max_deep_runs=max_deep_runs,orchestration_version=self.orchestration_version);session.add(cycle);session.commit();return self.get_cycle(session,cycle.id)
    def get_cycle(self,session,cycle_id):
        cycle=session.scalar(select(ResearchCycle).where(ResearchCycle.id==cycle_id).options(selectinload(ResearchCycle.players).selectinload(ResearchCyclePlayer.triggers),selectinload(ResearchCycle.players).selectinload(ResearchCyclePlayer.pulse),selectinload(ResearchCycle.players).selectinload(ResearchCyclePlayer.deep_run)))
        if not cycle: raise LookupError("ResearchCycle not found")
        return cycle
    def list_cycles(self,session,limit=20): return list(session.scalars(select(ResearchCycle).order_by(ResearchCycle.gameweek.desc(),ResearchCycle.created_at.desc()).limit(limit)))
    def get_latest_cycle(self,session): return session.scalar(select(ResearchCycle).order_by(ResearchCycle.gameweek.desc(),ResearchCycle.created_at.desc()))
    def prepare_cycle(self,session,cycle_id):
        cycle=self.get_cycle(session,cycle_id)
        if cycle.status in {ResearchCycleStatus.PREPARED,ResearchCycleStatus.EXECUTING,ResearchCycleStatus.COMPLETED,ResearchCycleStatus.PARTIAL}: return cycle
        cycle.status=ResearchCycleStatus.MONITORING
        entries=list(session.scalars(select(WatchlistEntry).where(WatchlistEntry.active.is_(True)).order_by(WatchlistEntry.player_id)))
        known={item.player_id:item for item in cycle.players}
        for entry in entries:
            if entry.player_id not in known: session.add(ResearchCyclePlayer(cycle_id=cycle.id,player_id=entry.player_id,watchlist_entry_id=entry.id))
        session.commit(); session.expire_all(); cycle=self.get_cycle(session,cycle.id)
        missing=[item.player_id for item in cycle.players if session.scalar(select(PlayerGameweekPulse.id).where(PlayerGameweekPulse.player_id==item.player_id,PlayerGameweekPulse.gameweek==cycle.gameweek)) is None]
        if missing and self.pulse_service: self.pulse_service.run_watchlist_pulse(session,cycle.gameweek)
        for item in cycle.players: item.pulse_id=session.scalar(select(PlayerGameweekPulse.id).where(PlayerGameweekPulse.player_id==item.player_id,PlayerGameweekPulse.gameweek==cycle.gameweek))
        self.trigger_service.evaluate_watchlist_triggers(session,cycle.gameweek)
        active=list(session.scalars(select(PlayerResearchTrigger).where(PlayerResearchTrigger.player_id.in_([item.player_id for item in cycle.players] or [-1]),PlayerResearchTrigger.status.in_(ResearchTriggerStatus.ACTIVE)).order_by(PlayerResearchTrigger.priority.desc(),PlayerResearchTrigger.created_at.asc(),PlayerResearchTrigger.player_id)))
        by_player={}
        for trigger in active: by_player.setdefault(trigger.player_id,[]).append(trigger)
        for item in cycle.players:
            item.triggers=list(by_player.get(item.player_id,[])); item.selected_for_deep_research=False;item.queue_rank=None
            item.state=ResearchCyclePlayerState.TRIGGERED if item.triggers else ResearchCyclePlayerState.MONITORED
        candidates=[item for item in cycle.players if item.triggers]
        candidates.sort(key=lambda item:(-max(trigger.priority for trigger in item.triggers),min(trigger.created_at for trigger in item.triggers),not bool(item.watchlist_entry_id and session.get(WatchlistEntry,item.watchlist_entry_id).pinned),item.player_id))
        for rank,item in enumerate(candidates,1):
            item.selection_reason=[{"trigger_id":trigger.id,"priority":trigger.priority,"description":trigger.description} for trigger in item.triggers]
            if rank<=cycle.max_deep_runs:item.state=ResearchCyclePlayerState.SELECTED;item.selected_for_deep_research=True;item.queue_rank=rank
            else:item.state=ResearchCyclePlayerState.DEFERRED
        cycle.status=ResearchCycleStatus.PREPARED;cycle.prepared_at=datetime.now(timezone.utc);session.commit();return self.get_cycle(session,cycle.id)
    def execute_selected_player(self,session,cycle_id,player_id):
        cycle=self.get_cycle(session,cycle_id); item=next((x for x in cycle.players if x.player_id==player_id),None)
        if not item or not item.selected_for_deep_research: raise ValueError("Player is not selected for this cycle")
        if item.state==ResearchCyclePlayerState.RESEARCHED:return cycle
        if self.deep_service is None: raise ValueError("Deep research service is unavailable")
        item.state=ResearchCyclePlayerState.RESEARCHING;cycle.status=ResearchCycleStatus.EXECUTING;cycle.started_at=cycle.started_at or datetime.now(timezone.utc);session.commit()
        try:
            primary=max(item.triggers,key=lambda x:(x.priority,-x.created_at.timestamp(),x.id))
            run=session.get(ResearchDeepRun,item.deep_run_id) if item.deep_run_id else None
            if run is None:
                thread=ResearchThread(title=f"GW{cycle.gameweek} weekly player research",thread_type=ResearchThreadType.PLAYER,question="Weekly trigger-led research") ;session.add(thread);session.commit()
                run=self.deep_service.create_run(session,thread_id=thread.id,player_id=item.player_id,research_cutoff=cycle.research_cutoff,situation_id=primary.situation_id,trigger_id=primary.id);item.deep_run_id=run.id
            run=self.deep_service.execute_full_run(session,run.id);item.state=ResearchCyclePlayerState.RESEARCHED
            now=datetime.now(timezone.utc)
            for trigger in item.triggers:
                if trigger.status in ResearchTriggerStatus.ACTIVE: trigger.status=ResearchTriggerStatus.RESOLVED;trigger.resolved_at=now
            session.commit()
        except Exception as exc:
            item.state=ResearchCyclePlayerState.FAILED;item.failure_reason=str(exc)[:500];session.commit()
        return self.get_cycle(session,cycle.id)
    def execute_cycle(self,session,cycle_id):
        cycle=self.get_cycle(session,cycle_id)
        if cycle.status==ResearchCycleStatus.PENDING: cycle=self.prepare_cycle(session,cycle_id)
        if cycle.status not in {ResearchCycleStatus.PREPARED,ResearchCycleStatus.EXECUTING}: return cycle
        cycle.status=ResearchCycleStatus.EXECUTING;cycle.started_at=cycle.started_at or datetime.now(timezone.utc);session.commit()
        for item in sorted([x for x in cycle.players if x.selected_for_deep_research and x.state not in {ResearchCyclePlayerState.RESEARCHED}],key=lambda x:x.queue_rank or 999): self.execute_selected_player(session,cycle.id,item.player_id)
        cycle=self.get_cycle(session,cycle.id);cycle.status=ResearchCycleStatus.PARTIAL if any(x.state==ResearchCyclePlayerState.FAILED for x in cycle.players) else ResearchCycleStatus.COMPLETED;cycle.completed_at=datetime.now(timezone.utc);session.commit();return self.get_cycle(session,cycle.id)
def _utc(value): return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
