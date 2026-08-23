"""Research-aware analysis of already deterministic, legal decision options."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from fpl_intelligence.models import (DecisionAnalysisPlayerContext, DecisionAnalysisRun, DecisionAnalysisStatus, DecisionOptionAnalysis, DecisionSessionStatus, MonitoringTrigger, PlayerResearchTrigger, ResearchPlayerSynthesis, ResearchQueueItem, ResearchQueueSource)
from fpl_intelligence.research.queue import ResearchQueueService
from fpl_intelligence.decisions.service import DecisionService, DecisionError

PROMPT_VERSION = "decision_analysis_v1"
class DecisionAnalysisError(ValueError): pass

class DecisionAnalysisService:
    def _synthesis(self, db, player_id, cutoff):
        return db.scalar(select(ResearchPlayerSynthesis).where(ResearchPlayerSynthesis.player_id == player_id, ResearchPlayerSynthesis.research_cutoff <= cutoff).order_by(ResearchPlayerSynthesis.research_cutoff.desc(), ResearchPlayerSynthesis.id.desc()))
    def gaps(self, db: Session, decision_id: str, cutoff: datetime | None = None):
        cutoff = cutoff or datetime.now(timezone.utc); decision = DecisionService().get(db, decision_id); roles = {p.player_id:{"retained_context"} for p in decision.frozen_picks}
        for option in decision.options:
            if option.is_legal:
                for move in option.movements: roles.setdefault(move.outgoing_player_id,set()).add("outgoing"); roles.setdefault(move.incoming_player_id,set()).add("incoming")
        result=[]
        for player_id, player_roles in roles.items():
            syn=self._synthesis(db,player_id,cutoff); reasons=[]; state="current"
            if not syn: state="missing"; reasons=["No completed player synthesis is available."]
            else:
                dims=syn.dimension_summaries or []
                thin = syn.overall_research_state in ("thin","unresolved") or any(isinstance(d,dict) and str(d.get("confidence",d.get("state",""))).lower() in ("thin","unresolved") for d in dims)
                newer_trigger=db.scalar(select(PlayerResearchTrigger.id).where(PlayerResearchTrigger.player_id==player_id, PlayerResearchTrigger.status.in_(("open","queued")), PlayerResearchTrigger.created_at>syn.research_cutoff)) or db.scalar(select(MonitoringTrigger.id).where(MonitoringTrigger.player_id==player_id, MonitoringTrigger.active.is_(True), MonitoringTrigger.created_at>syn.research_cutoff))
                if newer_trigger: state="stale"; reasons=["A newer open monitoring or research trigger exists."]
                elif thin: state="unresolved"; reasons=["Research has unresolved or thin material dimensions."]
            result.append({"player_id":player_id,"roles":sorted(player_roles),"synthesis_id":syn.id if syn else None,"synthesis_cutoff":syn.research_cutoff if syn else None,"overall_research_state":syn.overall_research_state if syn else None,"freshness_state":state,"research_gap_state":state,"research_gap_reasons":reasons,"needs_research":state!="current"})
        return result
    def packet(self, db, decision_id, cutoff=None):
        decision=DecisionService().get(db,decision_id); cutoff=cutoff or datetime.now(timezone.utc); gaps=self.gaps(db,decision_id,cutoff)
        legal=[o for o in decision.options if o.is_legal]
        return {"prompt_version":PROMPT_VERSION,"research_cutoff":cutoff.isoformat(),"session":{"id":decision.id,"gameweek":decision.gameweek,"manager_id":decision.manager_id,"bank":decision.frozen_bank,"frozen_squad":[p.player_id for p in decision.frozen_picks]},"legal_options":[{"id":o.id,"type":o.option_type,"bank_after":None if o.budget_available is None or o.budget_required is None else o.budget_available-o.budget_required,"movements":[{"outgoing_player_id":m.outgoing_player_id,"incoming_player_id":m.incoming_player_id} for m in o.movements]} for o in legal],"research_context":gaps}
    def analyze(self, db, decision_id, codex, cutoff=None):
        decision=DecisionService().get(db,decision_id)
        if decision.status != DecisionSessionStatus.DRAFT: raise DecisionAnalysisError("decision_session_is_finalized")
        cutoff=cutoff or datetime.now(timezone.utc); packet=self.packet(db,decision_id,cutoff); run=DecisionAnalysisRun(session_id=decision_id,status=DecisionAnalysisStatus.RUNNING,research_cutoff=cutoff,prompt_version=PROMPT_VERSION,started_at=datetime.now(timezone.utc)); db.add(run); db.flush()
        for context in packet["research_context"]:
            for role in context["roles"]: db.add(DecisionAnalysisPlayerContext(analysis_run_id=run.id,player_id=context["player_id"],role=role,synthesis_id=context["synthesis_id"],synthesis_cutoff=context["synthesis_cutoff"],overall_research_state=context["overall_research_state"],freshness_state=context["freshness_state"],research_gap_state=context["research_gap_state"],research_gap_reasons=context["research_gap_reasons"]))
        prompt="""You analyze only supplied legal FPL plans. Return JSON only with outcome (recommend_option|research_required|unresolved), recommended_option_id (or null), confidence (high|medium|low|unresolved), executive_summary, key_tradeoffs, key_risks, contradictions, missing_information, what_could_change_decision, option_analyses. Do not invent facts, browse, estimate points, use scores, or select a plan. A recommendation must name a supplied legal option.\n"""+json.dumps(packet,default=str)
        try:
            data=json.loads(codex.execute(prompt=prompt).final_text)
            if not isinstance(data, dict): raise ValueError("model output must be an object")
        except Exception as exc: run.status=DecisionAnalysisStatus.FAILED; run.failure_reason="Model output could not be parsed."; run.completed_at=datetime.now(timezone.utc); db.flush(); raise DecisionAnalysisError("decision_analysis_model_failed") from exc
        outcome=data.get("outcome"); rec=data.get("recommended_option_id"); legal={o["id"] for o in packet["legal_options"]}
        if outcome not in ("recommend_option","research_required","unresolved") or (outcome=="recommend_option" and rec not in legal) or (outcome!="recommend_option" and rec is not None): run.status=DecisionAnalysisStatus.FAILED; run.failure_reason="Model returned an invalid recommendation."; db.flush(); raise DecisionAnalysisError("invalid_decision_analysis_output")
        run.outcome=outcome; run.recommended_option_id=rec; run.confidence=data.get("confidence") if data.get("confidence") in ("high","medium","low","unresolved") else "unresolved"; run.status=DecisionAnalysisStatus.RESEARCH_REQUIRED if outcome=="research_required" else DecisionAnalysisStatus.COMPLETED; run.executive_summary=data.get("executive_summary"); run.key_tradeoffs=data.get("key_tradeoffs",[]); run.key_risks=data.get("key_risks",[]); run.contradictions=data.get("contradictions",[]); run.missing_information=data.get("missing_information",[]); run.what_could_change_decision=data.get("what_could_change_decision",[]); run.reasoning={"packet":packet}; run.completed_at=datetime.now(timezone.utc)
        for item in data.get("option_analyses",[]):
            if item.get("option_id") in legal: db.add(DecisionOptionAnalysis(analysis_run_id=run.id,option_id=item["option_id"],summary=item.get("summary"),strengths=item.get("strengths",[]),weaknesses=item.get("weaknesses",[]),risks=item.get("risks",[]),research_gaps=item.get("research_gaps",[])))
        db.flush(); return run
    def latest(self,db,session_id): return db.scalar(select(DecisionAnalysisRun).where(DecisionAnalysisRun.session_id==session_id,DecisionAnalysisRun.status.in_(("completed","research_required"))).order_by(DecisionAnalysisRun.created_at.desc()))
    def history(self,db,session_id): return list(db.scalars(select(DecisionAnalysisRun).where(DecisionAnalysisRun.session_id==session_id).order_by(DecisionAnalysisRun.created_at.desc())))
    def queue_gaps(self,db,decision_id,player_ids=None):
        gaps=[g for g in self.gaps(db,decision_id) if g["needs_research"] and (player_ids is None or g["player_id"] in player_ids)]; queue=ResearchQueueService(); items=[]
        for gap in gaps: items.append(queue.add_player(db,player_id=gap["player_id"],source=ResearchQueueSource.DECISION_CENTER,reason="Research requested from active transfer decision.",source_context={"decision_session_id":decision_id,"roles":gap["roles"],"gap_reasons":gap["research_gap_reasons"]}))
        return items

