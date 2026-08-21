"""Durable, dimension-specific views over canonical atomic evidence."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Protocol
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from fpl_intelligence.models import (EvidenceRelation, EvidenceRelationshipType, Player, ResearchDimensionAssessment, ResearchEvidence, ResearchEvidenceBundle, ResearchEvidenceBundleMember, ResearchEvidenceBundleStatus, ResearchSourceClusterMembership, SourceClusterMembershipType)
from fpl_intelligence.research.evidence import CLAIM_TYPES

EVAL2_EVIDENCE_BUNDLE_ASSESSMENT_PROMPT_VERSION = "eval2_evidence_bundle_assessment_v1"
STRENGTHS = {"strong", "adequate", "thin", "unresolved"}; CONFIDENCES = {"high", "medium", "low", "unresolved"}

class EvidenceBundleAssessmentProvider(Protocol):
    def assess(self, *, context: dict, prompt_version: str) -> dict: ...

class EvidenceBundleService:
    def __init__(self, provider: EvidenceBundleAssessmentProvider | None = None): self.provider = provider
    def build_dimension_bundle(self, session: Session, *, thread_id: str, player_id: int, dimension: str, research_cutoff: datetime, situation_id: str | None = None, evidence_ids: list[str] | None = None):
        if dimension not in CLAIM_TYPES: raise ValueError("Unknown claim dimension")
        if session.get(Player, player_id) is None: raise LookupError("Player not found")
        cutoff = _utc(research_cutoff)
        statement = select(ResearchEvidence).join(ResearchEvidence.players).where(ResearchEvidence.research_thread_id == thread_id, Player.id == player_id, ResearchEvidence.claim_type == dimension).options(selectinload(ResearchEvidence.players), selectinload(ResearchEvidence.research_link))
        if situation_id is not None: statement = statement.where(ResearchEvidence.research_situation_id == situation_id)
        evidence = list(session.scalars(statement).unique())
        eligible = [item for item in evidence if _evidence_time(item) <= cutoff]
        if evidence_ids is not None:
            requested = set(evidence_ids); selected = [item for item in eligible if item.id in requested]
            if requested != {item.id for item in selected}: raise ValueError("Evidence is missing, incompatible, or post-cutoff")
        else: selected = sorted(eligible, key=lambda item: (_evidence_time(item), item.id))
        bundle = ResearchEvidenceBundle(thread_id=thread_id, player_id=player_id, situation_id=situation_id, dimension=dimension, research_cutoff=cutoff)
        session.add(bundle); session.flush()
        superseded = {relation.to_evidence_id for relation in session.scalars(select(EvidenceRelation).where(EvidenceRelation.relation_type == EvidenceRelationshipType.SUPERSEDES, EvidenceRelation.from_evidence_id.in_([item.id for item in selected]), EvidenceRelation.to_evidence_id.in_([item.id for item in selected]))) }
        session.add_all([ResearchEvidenceBundleMember(bundle_id=bundle.id, evidence_id=item.id, role="superseded" if item.id in superseded else "current") for item in selected]); session.commit()
        return self.get_bundle(session, bundle.id)
    def get_bundle(self, session, bundle_id):
        result = session.scalar(select(ResearchEvidenceBundle).where(ResearchEvidenceBundle.id == bundle_id).options(selectinload(ResearchEvidenceBundle.members).selectinload(ResearchEvidenceBundleMember.evidence).selectinload(ResearchEvidence.research_link), selectinload(ResearchEvidenceBundle.assessment)))
        if result is None: raise LookupError("ResearchEvidenceBundle not found")
        return result
    def list_bundles_for_player(self, session, player_id): return list(session.scalars(select(ResearchEvidenceBundle).where(ResearchEvidenceBundle.player_id == player_id).order_by(ResearchEvidenceBundle.research_cutoff.desc(), ResearchEvidenceBundle.id)))
    def list_latest_dimension_assessments(self, session, player_id):
        rows = list(session.scalars(select(ResearchDimensionAssessment).where(ResearchDimensionAssessment.player_id == player_id).order_by(ResearchDimensionAssessment.dimension, ResearchDimensionAssessment.research_cutoff.desc(), ResearchDimensionAssessment.updated_at.desc())))
        seen=set(); return [row for row in rows if not (row.dimension in seen or seen.add(row.dimension))]
    def metrics(self, session, bundle):
        members=bundle.members; ids=[item.evidence_id for item in members]; links={item.evidence.research_link_id for item in members if item.evidence.research_link_id}; relations=list(session.scalars(select(EvidenceRelation).where(EvidenceRelation.from_evidence_id.in_(ids), EvidenceRelation.to_evidence_id.in_(ids)))) if ids else []
        clusters={item.evidence.source_cluster_id for item in members if item.evidence.source_cluster_id}; independent=0
        for cluster in clusters:
            memberships=list(session.scalars(select(ResearchSourceClusterMembership).where(ResearchSourceClusterMembership.source_cluster_id == cluster, ResearchSourceClusterMembership.research_link_id.in_(links))))
            if any(item.lineage_type in {SourceClusterMembershipType.ORIGINAL, SourceClusterMembershipType.INDEPENDENT} for item in memberships): independent += 1
        independent += len({link for link in links if not any(item.evidence.source_cluster_id for item in members if item.evidence.research_link_id == link)})
        return {"evidence_count":len(members), "distinct_source_count":len(links), "independent_source_count":independent, "contradiction_count":len({relation.id for relation in relations if relation.relation_type == EvidenceRelationshipType.CONTRADICTS}), "superseded_count":sum(item.role == "superseded" for item in members)}
    def assess_bundle(self, session, bundle_id):
        if self.provider is None: raise ValueError("Evidence bundle assessment provider is required")
        bundle=self.get_bundle(session,bundle_id); metrics=self.metrics(session,bundle)
        context={"player_id":bundle.player_id,"thread_id":bundle.thread_id,"situation_id":bundle.situation_id,"dimension":bundle.dimension,"research_cutoff":bundle.research_cutoff.isoformat(),"metrics":metrics,"evidence":[{"id":m.evidence_id,"claim":m.evidence.claim,"role":m.role,"type":m.evidence.evidence_type,"reliability":m.evidence.reliability,"relevance":m.evidence.relevance,"published_at":str(m.evidence.published_at),"source_url":m.evidence.research_link.original_url if m.evidence.research_link else None} for m in bundle.members]}
        payload=self.provider.assess(context=context,prompt_version=EVAL2_EVIDENCE_BUNDLE_ASSESSMENT_PROMPT_VERSION); self._validate(payload, metrics)
        assessment=bundle.assessment or ResearchDimensionAssessment(bundle_id=bundle.id,thread_id=bundle.thread_id,player_id=bundle.player_id,situation_id=bundle.situation_id,dimension=bundle.dimension,research_cutoff=bundle.research_cutoff,prompt_version=EVAL2_EVIDENCE_BUNDLE_ASSESSMENT_PROMPT_VERSION,**metrics)
        for key in ("bundle_strength","confidence","thesis","rationale","contradiction_summary","missing_information"): setattr(assessment,key,payload.get(key))
        assessment.evidence_count=metrics["evidence_count"]; assessment.distinct_source_count=metrics["distinct_source_count"]; assessment.independent_source_count=metrics["independent_source_count"]; assessment.contradiction_count=metrics["contradiction_count"]; assessment.superseded_count=metrics["superseded_count"]
        session.add(assessment); bundle.status=ResearchEvidenceBundleStatus.ASSESSED; session.commit(); return bundle, assessment
    @staticmethod
    def _validate(payload, metrics):
        if not isinstance(payload,dict) or set(payload) != {"bundle_strength","confidence","thesis","rationale","contradiction_summary","missing_information"}: raise ValueError("Invalid assessment output schema")
        if payload["bundle_strength"] not in STRENGTHS or payload["confidence"] not in CONFIDENCES or not isinstance(payload["missing_information"],list) or not str(payload["thesis"]).strip() or not str(payload["rationale"]).strip(): raise ValueError("Invalid assessment output")
        if metrics["evidence_count"] == 0 and (payload["bundle_strength"],payload["confidence"]) not in {("thin","low"),("unresolved","unresolved")}: raise ValueError("Zero-evidence bundle cannot be strong or high confidence")
def _utc(value): return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
def _evidence_time(item): return _utc(item.published_at or item.observed_at or item.retrieved_at or item.created_at)
