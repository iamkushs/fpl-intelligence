"""Explicit queue operations. Trigger generation does not call this service."""
from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from fpl_intelligence.models import Player, ResearchQueueItem, ResearchQueueSource, ResearchQueueStatus, ResearchCyclePlayer, ResearchCyclePlayerState, ResearchCycleStatus

class ResearchQueueService:
    def add_player(self, session: Session, *, player_id: int, source: str, reason=None, source_context=None, trigger_id=None, research_situation_id=None):
        if source not in {ResearchQueueSource.USER, ResearchQueueSource.DECISION_CENTER, ResearchQueueSource.ACCEPTED_SIGNAL, ResearchQueueSource.RESEARCH_MONITORING}: raise ValueError("invalid queue source")
        if session.get(Player, player_id) is None: raise LookupError("Player not found")
        item=session.scalar(select(ResearchQueueItem).where(ResearchQueueItem.player_id==player_id, ResearchQueueItem.status.in_(ResearchQueueStatus.ACTIVE)).order_by(ResearchQueueItem.queue_order))
        if item:
            if source_context: item.source_context={**(item.source_context or {}), **source_context}
            session.commit(); return item
        order=session.scalar(select(func.max(ResearchQueueItem.queue_order)).where(ResearchQueueItem.status.in_(ResearchQueueStatus.ACTIVE))) or 0
        item=ResearchQueueItem(player_id=player_id,source=source,reason=reason,source_context=source_context,trigger_id=trigger_id,research_situation_id=research_situation_id,queue_order=order+1); session.add(item); session.commit(); return item
    def get_queue(self, session, include_snoozed=False):
        statuses=[ResearchQueueStatus.QUEUED,ResearchQueueStatus.RUNNING]+([ResearchQueueStatus.SNOOZED] if include_snoozed else [])
        return list(session.scalars(select(ResearchQueueItem).options(selectinload(ResearchQueueItem.player)).where(ResearchQueueItem.status.in_(statuses)).order_by(ResearchQueueItem.queue_order,ResearchQueueItem.created_at)))
    def get_recent_history(self, session, limit=30): return list(session.scalars(select(ResearchQueueItem).options(selectinload(ResearchQueueItem.player)).order_by(ResearchQueueItem.updated_at.desc()).limit(limit)))
    def _set(self,session,item_id,status):
        item=session.get(ResearchQueueItem,item_id)
        if not item: raise LookupError("Queue item not found")
        item.status=status; session.commit(); return item
    def remove_item(self,session,item_id): return self._set(session,item_id,ResearchQueueStatus.REMOVED)
    def retry_failed_item(self,session,item_id): return self._set(session,item_id,ResearchQueueStatus.QUEUED)
    def unsnooze_item(self,session,item_id):
        item=self._set(session,item_id,ResearchQueueStatus.QUEUED); item.snoozed_until_gameweek=None; session.commit(); return item
    def snooze_item(self,session,item_id,until_gameweek):
        item=self._set(session,item_id,ResearchQueueStatus.SNOOZED); item.snoozed_until_gameweek=until_gameweek; session.commit(); return item
    def reorder_queue(self,session,item_ids):
        items={x.id:x for x in self.get_queue(session)}
        if set(item_ids)!=set(items): raise ValueError("ordered IDs must contain every active queue item")
        for n,item_id in enumerate(item_ids,1): items[item_id].queue_order=n
        session.commit(); return self.get_queue(session)

    def executable(self, session, item_ids=None, limit=15):
        if not 1 <= limit <= 15: raise ValueError("limit must be between 1 and 15")
        query=select(ResearchQueueItem).where(ResearchQueueItem.status==ResearchQueueStatus.QUEUED).order_by(ResearchQueueItem.queue_order)
        if item_ids is not None: query=query.where(ResearchQueueItem.id.in_(item_ids))
        return list(session.scalars(query.limit(limit)))

    def run(self, session, *, orchestrator, deep_service, gameweek, research_cutoff, limit=15, item_ids=None):
        items=self.executable(session,item_ids=item_ids,limit=limit)
        cycle=orchestrator.create_cycle(session,gameweek=gameweek,research_cutoff=research_cutoff,max_deep_runs=limit)
        cycle=orchestrator.prepare_cycle(session,cycle.id)
        existing={x.player_id:x for x in cycle.players}
        for item in items:
            item.status=ResearchQueueStatus.RUNNING
            cp=existing.get(item.player_id)
            if cp is None:
                cp=ResearchCyclePlayer(cycle_id=cycle.id,player_id=item.player_id,state=ResearchCyclePlayerState.SELECTED,selected_for_deep_research=True,queue_rank=item.queue_order)
                session.add(cp); session.flush()
            else:
                cp.state=ResearchCyclePlayerState.SELECTED; cp.selected_for_deep_research=True; cp.queue_rank=item.queue_order
            item.cycle_id=cycle.id
            item.cycle_player_id=cp.id
            item.deep_run_id=cp.deep_run_id
            session.commit()
            try:
                orchestrator.execute_selected_player(session,cycle.id,item.player_id,queue_context={"reason":item.reason,"source":item.source,"source_context":item.source_context,"research_situation_id":item.research_situation_id,"trigger_id":item.trigger_id})
                cp=session.scalar(select(ResearchCyclePlayer).where(ResearchCyclePlayer.cycle_id==cycle.id,ResearchCyclePlayer.player_id==item.player_id))
                item.cycle_id=cycle.id; item.cycle_player_id=cp.id; item.deep_run_id=cp.deep_run_id
                item.status=ResearchQueueStatus.COMPLETED if cp.state==ResearchCyclePlayerState.RESEARCHED else ResearchQueueStatus.FAILED
                session.commit()
            except Exception as exc:
                # The queue is the durable owner of this lifecycle.  Preserve
                # the attempted links even when orchestration fails before it
                # can create a deep run, and never leave an executable item
                # looking selected/running without a reason.
                cp=session.scalar(select(ResearchCyclePlayer).where(ResearchCyclePlayer.cycle_id==cycle.id,ResearchCyclePlayer.player_id==item.player_id))
                item.cycle_id=cycle.id
                item.cycle_player_id=cp.id if cp else None
                item.deep_run_id=cp.deep_run_id if cp else None
                if cp and cp.state != ResearchCyclePlayerState.RESEARCHED:
                    cp.state=ResearchCyclePlayerState.FAILED
                    cp.failure_reason=str(exc)[:500]
                item.status=ResearchQueueStatus.FAILED
                cycle.status=ResearchCycleStatus.PARTIAL
                cycle.completed_at=datetime.now(timezone.utc)
                session.commit()
        if items and cycle.status not in {ResearchCycleStatus.PARTIAL, ResearchCycleStatus.FAILED}:
            cycle.status=ResearchCycleStatus.COMPLETED
            cycle.completed_at=datetime.now(timezone.utc)
            session.commit()
        return items
