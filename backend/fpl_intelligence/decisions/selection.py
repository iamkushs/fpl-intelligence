"""Deterministic selection legality and research-aware selection analysis."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from sqlalchemy import select
from fpl_intelligence.models import (DecisionSessionStatus, FPLSelectionAnalysisRun, GameweekSelection, Player,
    ResearchPlayerSynthesis, SelectionAnalysisPlayerContext, SelectionAnalysisStatus)
from fpl_intelligence.decisions.service import DecisionService, DecisionError
from fpl_intelligence.decisions.analysis import DecisionAnalysisService

PROMPT_VERSION = "selection_analysis_v1"
class SelectionError(ValueError): pass

def validate_selection(squad_ids, players, xi, bench, captain, vice):
    errors=[]; squad=set(squad_ids); xi=list(xi or []); bench=list(bench or [])
    if len(xi)!=11: errors.append("starting_xi_must_contain_11_players")
    if len(bench)!=4: errors.append("bench_must_contain_4_players")
    if len(set(xi))!=len(xi) or len(set(bench))!=len(bench): errors.append("duplicate_player")
    if set(xi)|set(bench)!=squad or set(xi)&set(bench): errors.append("xi_and_bench_must_be_exact_squad_complement")
    xi_players=[players.get(i) for i in xi]
    if any(p is None for p in xi_players): errors.append("player_not_in_current_catalog")
    if not errors:
        counts={p:{"GKP":0,"DEF":0,"MID":0,"FWD":0} for p in []} # intentionally avoid inferred formation
        positions={k:sum(x.position==k for x in xi_players) for k in ("GKP","DEF","MID","FWD")}
        if positions["GKP"]!=1: errors.append("starting_xi_must_contain_exactly_1_gk")
        if positions["DEF"]<3: errors.append("starting_xi_requires_at_least_3_def")
        if positions["MID"]<2: errors.append("starting_xi_requires_at_least_2_mid")
        if positions["FWD"]<1: errors.append("starting_xi_requires_at_least_1_fwd")
    if captain not in xi: errors.append("captain_must_be_in_starting_xi")
    if vice not in xi: errors.append("vice_captain_must_be_in_starting_xi")
    if captain==vice: errors.append("captain_and_vice_must_differ")
    return sorted(set(errors))

class SelectionService:
    def _players(self, db, ids): return {p.id:p for p in db.scalars(select(Player).where(Player.id.in_(ids)))}
    def _session(self, db, sid): return DecisionService().get(db,sid)
    def save(self, db, sid, xi, bench, captain, vice):
        decision=self._session(db,sid); selection=db.scalar(select(GameweekSelection).where(GameweekSelection.session_id==sid))
        if decision.status!=DecisionSessionStatus.DRAFT or (selection and selection.finalized_at): raise SelectionError("selection_is_finalized")
        squad=[p.player_id for p in decision.frozen_picks]; errors=validate_selection(squad,self._players(db,squad),xi,bench,captain,vice)
        if errors: raise SelectionError(",".join(errors))
        if not selection: selection=GameweekSelection(session_id=sid); db.add(selection)
        selection.starting_xi_player_ids=list(xi); selection.bench_player_ids=list(bench); selection.captain_player_id=captain; selection.vice_captain_player_id=vice; db.flush(); return selection
    def finalize(self,db,sid):
        selection=db.scalar(select(GameweekSelection).where(GameweekSelection.session_id==sid))
        if not selection: raise SelectionError("selection_required_before_finalization")
        if selection.finalized_at: raise SelectionError("selection_is_finalized")
        selection.finalized_at=datetime.now(timezone.utc); db.flush(); return selection
    def gaps(self,db,sid,cutoff=None): return DecisionAnalysisService().gaps(db,sid,cutoff)
    def packet(self,db,sid,cutoff=None):
        decision=self._session(db,sid); cutoff=cutoff or datetime.now(timezone.utc); ids=[p.player_id for p in decision.frozen_picks]; players=self._players(db,ids); gaps={x['player_id']:x for x in self.gaps(db,sid,cutoff)}
        roster=[]
        for pick in decision.frozen_picks:
            p=players.get(pick.player_id); g=gaps[pick.player_id]; syn=db.get(ResearchPlayerSynthesis,g['synthesis_id']) if g['synthesis_id'] else None
            roster.append({'player_id':p.id,'name':p.display_name,'club_id':p.club_id,'position':p.position,'price':p.price,'availability_status':p.availability_status,'chance_of_playing_next_round':p.chance_of_playing_next_round,'news':p.news,'existing_squad_position':pick.squad_position,'existing_multiplier':None,'synthesis_id':g['synthesis_id'],'research_state':g['overall_research_state'],'research_gap_state':g['research_gap_state'],'research_gap_reasons':g['research_gap_reasons'],'dimensions':(syn.dimension_summaries if syn else [])})
        return {'prompt_version':PROMPT_VERSION,'research_cutoff':cutoff.isoformat(),'session':{'id':sid,'manager_id':decision.manager_id,'gameweek':decision.gameweek,'frozen_squad_player_ids':ids},'squad':roster}
    def analyze(self,db,sid,codex,cutoff=None):
        decision=self._session(db,sid)
        if decision.status!=DecisionSessionStatus.DRAFT: raise SelectionError('decision_session_is_finalized')
        current=db.scalar(select(GameweekSelection).where(GameweekSelection.session_id==sid))
        if current and current.finalized_at: raise SelectionError('selection_is_finalized')
        cutoff=cutoff or datetime.now(timezone.utc); packet=self.packet(db,sid,cutoff); run=FPLSelectionAnalysisRun(session_id=sid,status=SelectionAnalysisStatus.RUNNING,research_cutoff=cutoff,prompt_version=PROMPT_VERSION,started_at=datetime.now(timezone.utc)); db.add(run); db.flush()
        for p in packet['squad']:
            db.add(SelectionAnalysisPlayerContext(analysis_run_id=run.id,player_id=p['player_id'],synthesis_id=p['synthesis_id'],synthesis_cutoff=cutoff if p['synthesis_id'] else None,research_state=p['research_state'],research_gap_state=p['research_gap_state'],relevant_dimension_facts=p['dimensions']))
        prompt='Return JSON only: outcome (recommendation|research_required|unresolved), recommended_starting_xi_player_ids, recommended_bench_player_ids_in_order, recommended_captain_player_id, recommended_vice_player_id, confidence (high|medium|low|unresolved), executive_summary, captaincy_reasoning, lineup_reasoning, bench_reasoning, key_risks, contradictions, missing_information, what_could_change_decision. Use supplied facts only; no points, scores or probabilities. For non-recommendation leave recommendation fields null or empty.\n'+json.dumps(packet,default=str)
        try: data=json.loads(codex.execute(prompt=prompt).final_text)
        except Exception as exc: run.status=SelectionAnalysisStatus.FAILED; run.failure_reason='Model output could not be parsed.'; run.completed_at=datetime.now(timezone.utc); db.flush(); raise SelectionError('selection_analysis_model_failed') from exc
        outcome=data.get('outcome'); valid_outcomes={'recommendation','research_required','unresolved'}
        if outcome not in valid_outcomes: return self._fail(db,run,'invalid_selection_analysis_output')
        xi=data.get('recommended_starting_xi_player_ids') or []; bench=data.get('recommended_bench_player_ids_in_order') or []; captain=data.get('recommended_captain_player_id'); vice=data.get('recommended_vice_player_id')
        if outcome=='recommendation':
            errors=validate_selection(packet['session']['frozen_squad_player_ids'],self._players(db,packet['session']['frozen_squad_player_ids']),xi,bench,captain,vice)
            if errors: return self._fail(db,run,'invalid_selection_analysis_output')
        elif any((xi,bench,captain,vice)): return self._fail(db,run,'invalid_selection_analysis_output')
        run.outcome=outcome; run.status=SelectionAnalysisStatus.RESEARCH_REQUIRED if outcome=='research_required' else SelectionAnalysisStatus.COMPLETED; run.recommended_starting_xi=xi; run.recommended_bench_order=bench; run.recommended_captain_player_id=captain; run.recommended_vice_player_id=vice; run.confidence=data.get('confidence') if data.get('confidence') in ('high','medium','low','unresolved') else 'unresolved'; run.executive_summary=data.get('executive_summary'); run.captaincy_reasoning=data.get('captaincy_reasoning'); run.lineup_reasoning=data.get('lineup_reasoning'); run.bench_reasoning=data.get('bench_reasoning'); run.risks=data.get('key_risks',[]); run.contradictions=data.get('contradictions',[]); run.research_gaps=data.get('missing_information',[]); run.what_could_change_decision=data.get('what_could_change_decision',[]); run.reasoning={'packet':packet}; run.completed_at=datetime.now(timezone.utc); db.flush(); return run
    def _fail(self,db,run,error): run.status=SelectionAnalysisStatus.FAILED; run.failure_reason='Model returned an invalid selection.'; run.completed_at=datetime.now(timezone.utc); db.flush(); raise SelectionError(error)
    def latest(self,db,sid): return db.scalar(select(FPLSelectionAnalysisRun).where(FPLSelectionAnalysisRun.session_id==sid,FPLSelectionAnalysisRun.status.in_((SelectionAnalysisStatus.COMPLETED,SelectionAnalysisStatus.RESEARCH_REQUIRED))).order_by(FPLSelectionAnalysisRun.created_at.desc()))
    def history(self,db,sid): return list(db.scalars(select(FPLSelectionAnalysisRun).where(FPLSelectionAnalysisRun.session_id==sid).order_by(FPLSelectionAnalysisRun.created_at.desc())))
    def apply(self,db,sid,run_id):
        run=db.get(FPLSelectionAnalysisRun,run_id)
        if not run or run.session_id!=sid or run.outcome!='recommendation': raise SelectionError('selection_recommendation_not_available')
        return self.save(db,sid,run.recommended_starting_xi,run.recommended_bench_order,run.recommended_captain_player_id,run.recommended_vice_player_id)
