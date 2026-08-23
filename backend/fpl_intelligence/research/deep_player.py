"""NR14/15 orchestration over existing evidence, discovery, quality, and bundles."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Protocol
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from fpl_intelligence.models import (MonitoringTrigger, Player, PlayerResearchTrigger, ResearchBlindSpotFinding, ResearchDeepRun, ResearchDeepRunStatus, ResearchDimensionAssessment, ResearchEvidence, ResearchEvidenceBundle, ResearchEvidenceBundleMember, ResearchEvidenceType, ResearchPlayerSynthesis, ResearchQualityRun, ResearchQualityStage, ResearchThread, research_deep_run_assessments, research_deep_run_quality_runs)
from fpl_intelligence.research.evidence import CLAIM_TYPES
from fpl_intelligence.research.evidence_bundles import EvidenceBundleService
from fpl_intelligence.research.quality import ResearchQualityService
from fpl_intelligence.research.two_stage import PlayerResolver

DEEP_PLAYER_RESEARCH_DIMENSIONS = tuple(item for item in ("availability","injury","suspension","minutes","starting_status","expected_xi","tactical_role","position","formation","penalties","corners","direct_free_kicks","indirect_free_kicks","competition","manager_intent","performance","underlying_stats","fixture_context","team_attack","team_defence","transfer","goalkeeper_hierarchy") if item in CLAIM_TYPES)
CRITICAL = tuple(item for item in ("availability","minutes","starting_status","expected_xi","tactical_role","penalties","corners","direct_free_kicks","competition","position") if item in CLAIM_TYPES)

class BlindSpotProvider(Protocol):
    def find(self, *, context: dict, prompt_version: str) -> dict: ...
class FinalPlayerSynthesisProvider(Protocol):
    def synthesize(self, *, context: dict, prompt_version: str) -> dict: ...

class DeepPlayerResearchService:
    orchestration_version="eval2_deep_player_research_v1"
    def __init__(self, *, source_service, quality_execution, player_resolver: PlayerResolver, bundle_service: EvidenceBundleService, blind_spot_provider: BlindSpotProvider | None=None, synthesis_provider: FinalPlayerSynthesisProvider | None=None):
        self.source_service,self.quality_execution,self.player_resolver,self.bundle_service=source_service,quality_execution,player_resolver,bundle_service; self.blind_spot_provider,self.synthesis_provider=blind_spot_provider,synthesis_provider; self.quality=ResearchQualityService()
    def create_run(self, session: Session, *, thread_id: str, player_id: int, research_cutoff: datetime, situation_id: str|None=None, trigger_id: str|None=None, target_dimensions: list[str]|None=None):
        if session.get(Player,player_id) is None: raise LookupError("Player not found")
        if session.get(ResearchThread,thread_id) is None: raise LookupError("ResearchThread not found")
        if trigger_id and (not (trigger:=session.get(PlayerResearchTrigger,trigger_id)) or trigger.player_id != player_id): raise ValueError("Trigger must belong to the researched Player")
        dimensions=list(dict.fromkeys(target_dimensions or DEEP_PLAYER_RESEARCH_DIMENSIONS))
        if not dimensions or any(item not in CLAIM_TYPES for item in dimensions): raise ValueError("Unknown target dimension")
        run=ResearchDeepRun(thread_id=thread_id,player_id=player_id,situation_id=situation_id,trigger_id=trigger_id,research_cutoff=_utc(research_cutoff),target_dimensions=dimensions,orchestration_version=self.orchestration_version)
        session.add(run); session.commit(); return self.get_run(session,run.id)
    def get_run(self, session, run_id):
        run=session.scalar(select(ResearchDeepRun).where(ResearchDeepRun.id==run_id).options(selectinload(ResearchDeepRun.assessments),selectinload(ResearchDeepRun.quality_runs),selectinload(ResearchDeepRun.blind_spots).selectinload(ResearchBlindSpotFinding.evidence),selectinload(ResearchDeepRun.synthesis)))
        if not run: raise LookupError("ResearchDeepRun not found")
        return run
    def execute_research(self, session, run_id):
        run=self.get_run(session,run_id)
        if run.discovery_execution_id is None:
            run.status=ResearchDeepRunStatus.RUNNING; run.started_at=run.started_at or datetime.now(timezone.utc); session.commit()
            state=self.source_service.start_player_discovery(session,thread_id=run.thread_id,player_id=run.player_id,research_cutoff=run.research_cutoff,situation_id=run.situation_id,trigger_id=run.trigger_id,known_missing_dimensions=run.target_dimensions,durable_context={"deep_run_id":run.id,"target_dimensions":run.target_dimensions})
            run.discovery_execution_id=state["id"]; session.commit()
            for candidate in self.source_service.list_execution_candidates(session,state["id"]):
                if candidate.research_link_id:
                    result=self.source_service.research_link(session,link_id=candidate.research_link_id,player_resolver=self.player_resolver,research_cutoff=run.research_cutoff,target_dimensions=run.target_dimensions,situation_id=run.situation_id,trigger_id=run.trigger_id)
                    for result_id in result["result_ids"]: self.source_service.extract_atomic_evidence(session,result_id=result_id,research_cutoff=run.research_cutoff,situation_id=run.situation_id,trigger_id=run.trigger_id)
        if not any(item.stage==ResearchQualityStage.REDDIT for item in run.quality_runs):
            quality=self.quality.start_reddit_run(session,thread_id=run.thread_id,player_id=run.player_id,research_cutoff=run.research_cutoff,situation_id=run.situation_id); session.execute(research_deep_run_quality_runs.insert().values(deep_run_id=run.id,quality_run_id=quality.id)); session.commit(); self.quality_execution.execute_reddit(session,quality.id)
        self._assess_all(session,run); self._quality_passes(session,run); self._assess_all(session,run); run.status=ResearchDeepRunStatus.RESEARCH_COMPLETE; session.commit(); return self.get_run(session,run.id)
    def _assess_all(self,session,run,dimensions=None):
        for dimension in dimensions or run.target_dimensions:
            bundle=self.bundle_service.build_dimension_bundle(session,thread_id=run.thread_id,player_id=run.player_id,dimension=dimension,research_cutoff=run.research_cutoff,situation_id=run.situation_id)
            _, assessment=self.bundle_service.assess_bundle(session,bundle.id); session.execute(research_deep_run_assessments.insert().prefix_with("OR IGNORE").values(deep_run_id=run.id,dimension_assessment_id=assessment.id)); session.commit()
    def _quality_passes(self,session,run):
        final=self._latest_assessments(session,run); count=0
        for dimension in CRITICAL:
            assessment=final.get(dimension)
            if count>=5 or not assessment or assessment.bundle_strength not in {"strong","adequate"} or assessment.confidence not in {"high","medium"}: continue
            quality=self.quality.start_counter_search_run(session,thread_id=run.thread_id,player_id=run.player_id,challenged_claim=assessment.thesis,research_cutoff=run.research_cutoff,situation_id=run.situation_id); session.execute(research_deep_run_quality_runs.insert().values(deep_run_id=run.id,quality_run_id=quality.id)); session.commit(); self.quality_execution.execute_counter_search(session,quality.id); count+=1
        count=0
        for dimension in run.target_dimensions:
            if count>=5: break
            bundle=session.scalar(select(ResearchEvidenceBundle).where(ResearchEvidenceBundle.thread_id==run.thread_id,ResearchEvidenceBundle.player_id==run.player_id,ResearchEvidenceBundle.dimension==dimension).order_by(ResearchEvidenceBundle.created_at.desc()).options(selectinload(ResearchEvidenceBundle.members).selectinload(ResearchEvidenceBundleMember.evidence)))
            if not bundle: continue
            candidate=next((m.evidence for m in sorted(bundle.members,key=lambda m:({"high":0,"medium":1,"low":2}.get(m.evidence.relevance,3),m.evidence.id)) if m.role=="current" and m.evidence.is_volatile),None)
            if candidate:
                quality=self.quality.start_freshness_run(session,thread_id=run.thread_id,player_id=run.player_id,target_evidence_id=candidate.id,research_cutoff=run.research_cutoff,situation_id=run.situation_id); session.execute(research_deep_run_quality_runs.insert().values(deep_run_id=run.id,quality_run_id=quality.id)); session.commit(); self.quality_execution.execute_freshness(session,quality.id); count+=1
    def run_blind_spot_pass(self,session,run_id):
        run=self.get_run(session,run_id)
        if self.blind_spot_provider is None: raise ValueError("Blind spot provider is required")
        if not run.blind_spots:
            payload=self.blind_spot_provider.find(context={"player_id":run.player_id,"dimensions":[_assessment(a) for a in self._latest_assessments(session,run).values()],"research_cutoff":run.research_cutoff.isoformat()},prompt_version="eval2_blind_spot_v1")
            findings=payload.get("findings",[]) if isinstance(payload,dict) else []
            if len(findings)>5 or any(len(item.get("suggested_queries",[]))>3 for item in findings): raise ValueError("Invalid blind spot output")
            for item in findings: session.add(ResearchBlindSpotFinding(deep_run_id=run.id,dimension=item.get("dimension"),category=str(item["category"]),question=str(item["question"]),why_it_matters=str(item["why_it_matters"])))
            session.commit()
        run=self.get_run(session,run.id)
        findings=list(run.blind_spots)
        if findings and run.status != ResearchDeepRunStatus.BLIND_SPOT_COMPLETE and hasattr(self.source_service, "start_player_discovery"):
            questions=[item.question for item in findings]
            context={"deep_run_id":run.id,"blind_spot_findings":[{"finding_id":item.id,"dimension":item.dimension,"question":item.question,"category":item.category} for item in findings]}
            state=self.source_service.start_player_discovery(session,thread_id=run.thread_id,player_id=run.player_id,research_cutoff=run.research_cutoff,situation_id=run.situation_id,trigger_id=run.trigger_id,known_missing_dimensions=list(dict.fromkeys([item.dimension for item in findings if item.dimension])),research_questions=questions,targeted_only=True,durable_context=context)
            run.failure_reason=(run.failure_reason or None)
            for candidate in self.source_service.list_execution_candidates(session,state["id"]):
                if not candidate.research_link_id: continue
                result=self.source_service.research_link(session,link_id=candidate.research_link_id,player_resolver=self.player_resolver,research_cutoff=run.research_cutoff,target_dimensions=run.target_dimensions,situation_id=run.situation_id,trigger_id=run.trigger_id,durable_context=context)
                for result_id in result["result_ids"]: self.source_service.extract_atomic_evidence(session,result_id=result_id,research_cutoff=run.research_cutoff,situation_id=run.situation_id,trigger_id=run.trigger_id,durable_context=context)
            evidence=list(session.scalars(select(ResearchEvidence).join(ResearchEvidence.players).where(ResearchEvidence.research_thread_id==run.thread_id,Player.id==run.player_id)))
            affected=set()
            for finding in findings:
                matches=[item for item in evidence if finding.dimension and item.claim_type==finding.dimension]
                if matches:
                    finding.evidence.extend(item for item in matches if item not in finding.evidence); finding.status="researched"; finding.resolution_summary="Targeted evidence was extracted for the requested dimension."; affected.add(finding.dimension)
                else: finding.status="unresolved"; finding.resolution_summary="The combined targeted pass did not extract relevant evidence."
            self._assess_all(session,run,sorted(affected)); session.commit()
        else:
            for finding in findings: finding.status="unresolved"; finding.resolution_summary="No targeted source evidence was persisted in this bounded pass."
        run.status=ResearchDeepRunStatus.BLIND_SPOT_COMPLETE; session.commit(); return self.get_run(session,run.id)
    def synthesize(self,session,run_id):
        run=self.get_run(session,run_id)
        if self.synthesis_provider is None: raise ValueError("Final synthesis provider is required")
        assessments=list(self._latest_assessments(session,run).values()); context={"player_id":run.player_id,"research_cutoff":run.research_cutoff.isoformat(),"dimension_assessments":[_assessment(x) for x in assessments],"blind_spots":[{"question":x.question,"status":x.status,"resolution_summary":x.resolution_summary} for x in run.blind_spots]}
        data=self.synthesis_provider.synthesize(context=context,prompt_version="eval2_final_player_synthesis_v1"); required={"overall_research_state","executive_summary","dimension_summaries","key_strengths","key_risks","contradictions","missing_information","future_monitoring"}
        if not isinstance(data,dict) or set(data)!=required or data["overall_research_state"] not in {"clear","mixed","thin","unresolved"}: raise ValueError("Invalid final synthesis output")
        synthesis=run.synthesis or ResearchPlayerSynthesis(deep_run_id=run.id,thread_id=run.thread_id,player_id=run.player_id,situation_id=run.situation_id,research_cutoff=run.research_cutoff,prompt_version="eval2_final_player_synthesis_v1",model_metadata=None,**data)
        if run.synthesis:
            for k,v in data.items(): setattr(synthesis,k,v)
        session.add(synthesis); self._monitor(session,run,data["future_monitoring"]); run.status=ResearchDeepRunStatus.COMPLETED; run.completed_at=datetime.now(timezone.utc); session.commit(); return self.get_run(session,run.id)
    def execute_full_run(self,session,run_id):
        try:
            self.execute_research(session,run_id)
            self.run_blind_spot_pass(session,run_id)
            return self.synthesize(session,run_id)
        except Exception as exc:
            run=self.get_run(session,run_id)
            run.status=ResearchDeepRunStatus.FAILED
            run.failure_reason=str(exc)[:500]
            run.completed_at=datetime.now(timezone.utc)
            session.commit()
            raise
    def get_latest_synthesis(self,session,player_id): return session.scalar(select(ResearchPlayerSynthesis).where(ResearchPlayerSynthesis.player_id==player_id).order_by(ResearchPlayerSynthesis.research_cutoff.desc(),ResearchPlayerSynthesis.updated_at.desc()))
    def _latest_assessments(self,session,run):
        items=list(session.scalars(select(ResearchDimensionAssessment).join(research_deep_run_assessments).where(research_deep_run_assessments.c.deep_run_id==run.id).order_by(ResearchDimensionAssessment.dimension,ResearchDimensionAssessment.updated_at.desc()))) ; result={}
        for item in items: result.setdefault(item.dimension,item)
        return result
    def _monitor(self,session,run,items):
        for item in items:
            if not isinstance(item,dict) or not item.get("condition") or not item.get("description"): continue
            category=item.get("category","other") if item.get("category") in {"appearance","minutes","set_piece","availability","team_selection","transfer","tactical_role","fixture","manager_comment","freshness","other"} else "other"
            existing=session.scalar(select(MonitoringTrigger).where(MonitoringTrigger.player_id==run.player_id,MonitoringTrigger.active.is_(True),MonitoringTrigger.description==item["description"]))
            if not existing: session.add(MonitoringTrigger(player_id=run.player_id,research_thread_id=run.thread_id,category=category,description=item["description"],condition=item["condition"]))
def _utc(value): return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
def _assessment(item): return {"dimension":item.dimension,"thesis":item.thesis,"confidence":item.confidence,"bundle_strength":item.bundle_strength,"missing_information":item.missing_information}
