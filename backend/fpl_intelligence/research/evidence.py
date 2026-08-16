"""Transactional domain operations for the NR10 evidence language."""

from sqlalchemy.orm import Session

from fpl_intelligence.models import (
    EvidenceRelationshipType,
    ResearchEvidenceType,
    SourceClusterMembershipType,
)
from fpl_intelligence.repositories.research_evidence import ResearchEvidenceRepository

EVIDENCE_TYPES = {
    ResearchEvidenceType.FACT,
    ResearchEvidenceType.STATISTIC,
    ResearchEvidenceType.REPORT,
    ResearchEvidenceType.SUPPORTER_OBSERVATION,
    ResearchEvidenceType.SPECULATION,
    ResearchEvidenceType.INFERENCE,
}
RELATION_TYPES = {EvidenceRelationshipType.SUPPORTS, EvidenceRelationshipType.CONTRADICTS, EvidenceRelationshipType.SUPERSEDES}
HYPOTHESIS_RELATION_TYPES = {EvidenceRelationshipType.SUPPORTS, EvidenceRelationshipType.CONTRADICTS}
LINEAGE_TYPES = {
    SourceClusterMembershipType.ORIGINAL,
    SourceClusterMembershipType.INDEPENDENT,
    SourceClusterMembershipType.DERIVATIVE,
    SourceClusterMembershipType.UNCLEAR,
}
LEVELS = {"high", "medium", "low"}
CLAIM_TYPES = {
    "minutes", "starting_status", "expected_xi", "tactical_role", "position", "formation", "penalties", "corners",
    "direct_free_kicks", "indirect_free_kicks", "availability", "injury", "suspension", "transfer", "competition",
    "manager_intent", "performance", "underlying_stats", "fixture_context", "team_attack", "team_defence",
    "goalkeeper_hierarchy", "price", "ownership", "other",
}
VOLATILE_CLAIM_TYPES = {
    "injury", "availability", "expected_xi", "starting_status", "minutes", "tactical_role", "transfer", "suspension",
    "penalties", "corners", "direct_free_kicks", "indirect_free_kicks", "goalkeeper_hierarchy", "manager_intent", "position",
}


class ResearchEvidenceService:
    def __init__(self, repository: ResearchEvidenceRepository | None = None):
        self.repository = repository or ResearchEvidenceRepository()

    def create_evidence(self, session: Session, *, research_thread_id: str, claim: str, claim_type: str, evidence_type: str,
                        reliability: str, relevance: str, research_situation_id: str | None = None,
                        research_link_id: str | None = None, research_result_id: str | None = None,
                        source_cluster_id: str | None = None, player_ids: list[int] | None = None,
                        published_at=None, observed_at=None, retrieved_at=None, season: str | None = None,
                        is_volatile: bool | None = None, notes: str | None = None):
        self._text(claim, "Evidence claim is required")
        self._choice(claim_type, CLAIM_TYPES, "Unknown claim type")
        self._choice(evidence_type, EVIDENCE_TYPES, "Unknown evidence type")
        self._choice(reliability, LEVELS, "Reliability must be high, medium, or low")
        self._choice(relevance, LEVELS, "Relevance must be high, medium, or low")
        thread = self._require(self.repository.get_thread(session, research_thread_id), "ResearchThread")
        situation = self._require(self.repository.get_situation(session, research_situation_id), "ResearchSituation") if research_situation_id else None
        link = self._require(self.repository.get_link(session, research_link_id), "ResearchLink") if research_link_id else None
        result = self._require(self.repository.get_result(session, research_result_id), "ResearchResult") if research_result_id else None
        cluster = self._require(self.repository.get_cluster(session, source_cluster_id), "ResearchSourceCluster") if source_cluster_id else None
        if link and link.research_thread_id != thread.id:
            raise ValueError("ResearchLink must belong to the evidence thread")
        if result and result.research_thread_id != thread.id:
            raise ValueError("ResearchResult must belong to the evidence thread")
        if link and result and result.research_link_id != link.id:
            raise ValueError("ResearchResult must belong to the supplied ResearchLink")
        if cluster and (cluster.research_thread_id != thread.id or (situation and cluster.research_situation_id and cluster.research_situation_id != situation.id)):
            raise ValueError("Source cluster context is incompatible with evidence")
        evidence = self.repository.create_evidence(session, research_thread_id=thread.id, research_situation_id=situation.id if situation else None,
            claim=claim.strip(), claim_type=claim_type, evidence_type=evidence_type, research_link_id=link.id if link else None,
            research_result_id=result.id if result else None, source_cluster_id=cluster.id if cluster else None, published_at=published_at,
            observed_at=observed_at, retrieved_at=retrieved_at, season=season, reliability=reliability, relevance=relevance,
            is_volatile=claim_type in VOLATILE_CLAIM_TYPES if is_volatile is None else is_volatile, notes=notes)
        self._attach_players(session, evidence, player_ids or [])
        session.commit()
        return self.get_evidence(session, evidence.id)

    def get_evidence(self, session: Session, evidence_id: str):
        return self.repository.get_evidence(session, evidence_id)

    def list_evidence(self, session: Session, **filters):
        return self.repository.list_evidence(session, **filters)

    def attach_players(self, session: Session, evidence_id: str, player_ids: list[int]):
        evidence = self._require(self.get_evidence(session, evidence_id), "ResearchEvidence")
        self._attach_players(session, evidence, player_ids)
        session.commit()
        return self.get_evidence(session, evidence_id)

    def add_hypothesis_relation(self, session: Session, *, evidence_id: str, hypothesis_id: str, relationship_type: str, rationale: str | None = None):
        evidence = self._require(self.get_evidence(session, evidence_id), "ResearchEvidence")
        hypothesis = self._require(self.repository.get_hypothesis(session, hypothesis_id), "SituationHypothesis")
        self._choice(relationship_type, HYPOTHESIS_RELATION_TYPES, "Hypothesis relationship must support or contradict")
        if evidence.research_situation_id and evidence.research_situation_id != hypothesis.situation_id:
            raise ValueError("Evidence and hypothesis have incompatible situations")
        existing = next((item for item in evidence.hypothesis_relations if item.hypothesis_id == hypothesis_id and item.relationship_type == relationship_type), None)
        if existing:
            return existing
        relation = self.repository.add_hypothesis_relation(session, evidence_id=evidence.id, hypothesis_id=hypothesis.id, relationship_type=relationship_type, rationale=rationale)
        session.commit()
        return relation

    def add_evidence_relation(self, session: Session, *, from_evidence_id: str, to_evidence_id: str, relation_type: str, rationale: str | None = None):
        if from_evidence_id == to_evidence_id:
            raise ValueError("Evidence cannot relate to itself")
        self._require(self.get_evidence(session, from_evidence_id), "ResearchEvidence")
        self._require(self.get_evidence(session, to_evidence_id), "ResearchEvidence")
        self._choice(relation_type, RELATION_TYPES, "Unknown evidence relationship type")
        existing = next((item for item in self.repository.list_relations(session, from_evidence_id)
                         if item.from_evidence_id == from_evidence_id and item.to_evidence_id == to_evidence_id and item.relation_type == relation_type), None)
        if existing:
            return existing
        relation = self.repository.add_evidence_relation(session, from_evidence_id=from_evidence_id, to_evidence_id=to_evidence_id, relation_type=relation_type, rationale=rationale)
        session.commit()
        return relation

    def relations_for(self, session: Session, evidence_id: str):
        return self.repository.list_relations(session, evidence_id)

    def relations_for_many(self, session: Session, evidence_ids: list[str]) -> dict[str, list]:
        relations = self.repository.list_relations_for_evidence(session, evidence_ids)
        by_evidence = {evidence_id: [] for evidence_id in evidence_ids}
        for relation in relations:
            by_evidence.setdefault(relation.from_evidence_id, []).append(relation)
            by_evidence.setdefault(relation.to_evidence_id, []).append(relation)
        return by_evidence

    def create_cluster(self, session: Session, *, research_thread_id: str, narrative: str, research_situation_id: str | None = None,
                       likely_original_research_link_id: str | None = None, notes: str | None = None):
        self._require(self.repository.get_thread(session, research_thread_id), "ResearchThread")
        self._text(narrative, "Source cluster narrative is required")
        situation = self._require(self.repository.get_situation(session, research_situation_id), "ResearchSituation") if research_situation_id else None
        link = self._require(self.repository.get_link(session, likely_original_research_link_id), "ResearchLink") if likely_original_research_link_id else None
        if link and link.research_thread_id != research_thread_id:
            raise ValueError("Likely original link must belong to the cluster thread")
        cluster = self.repository.create_cluster(session, research_thread_id=research_thread_id, research_situation_id=situation.id if situation else None,
            narrative=narrative.strip(), likely_original_research_link_id=link.id if link else None, notes=notes)
        session.commit()
        return self.get_cluster(session, cluster.id)

    def get_cluster(self, session: Session, cluster_id: str):
        return self.repository.get_cluster(session, cluster_id)

    def attach_cluster_link(self, session: Session, *, cluster_id: str, research_link_id: str, lineage_type: str, notes: str | None = None):
        cluster = self._require(self.get_cluster(session, cluster_id), "ResearchSourceCluster")
        link = self._require(self.repository.get_link(session, research_link_id), "ResearchLink")
        self._choice(lineage_type, LINEAGE_TYPES, "Unknown source lineage type")
        if cluster.research_thread_id != link.research_thread_id:
            raise ValueError("ResearchLink must belong to the source cluster thread")
        existing = self.repository.get_membership(session, cluster_id, research_link_id)
        if existing:
            return self.get_cluster(session, cluster_id)
        self.repository.add_membership(session, source_cluster_id=cluster_id, research_link_id=research_link_id, lineage_type=lineage_type, notes=notes)
        if lineage_type == SourceClusterMembershipType.ORIGINAL and cluster.likely_original_research_link_id is None:
            cluster.likely_original_research_link_id = link.id
        session.commit()
        return self.get_cluster(session, cluster_id)

    def remove_cluster_link(self, session: Session, *, cluster_id: str, research_link_id: str):
        cluster = self._require(self.get_cluster(session, cluster_id), "ResearchSourceCluster")
        membership = self.repository.get_membership(session, cluster_id, research_link_id)
        if membership is None:
            raise LookupError("Source cluster membership not found")
        # A likely-original pointer must never outlive the membership that gives it lineage context.
        if cluster.likely_original_research_link_id == research_link_id:
            cluster.likely_original_research_link_id = None
        session.delete(membership)
        session.commit()

    def set_likely_original(self, session: Session, *, cluster_id: str, research_link_id: str | None):
        cluster = self._require(self.get_cluster(session, cluster_id), "ResearchSourceCluster")
        if research_link_id is not None:
            link = self._require(self.repository.get_link(session, research_link_id), "ResearchLink")
            if link.research_thread_id != cluster.research_thread_id:
                raise ValueError("Likely original link must belong to the cluster thread")
            cluster.likely_original_research_link_id = link.id
        else:
            cluster.likely_original_research_link_id = None
        session.commit()
        return self.get_cluster(session, cluster_id)

    @staticmethod
    def independent_confirmation_count(cluster) -> int:
        # The original reporter is the first lineage source; derivatives never count.
        return sum(item.lineage_type in {SourceClusterMembershipType.ORIGINAL, SourceClusterMembershipType.INDEPENDENT} for item in cluster.memberships)

    def _attach_players(self, session, evidence, player_ids):
        unique_ids = list(dict.fromkeys(player_ids))
        players = {item.id: item for item in self.repository.existing_players(session, unique_ids)}
        missing = next((item for item in unique_ids if item not in players), None)
        if missing is not None:
            raise LookupError(f"Player not found: {missing}")
        existing = {item.id for item in evidence.players}
        evidence.players.extend(players[item] for item in unique_ids if item not in existing)

    @staticmethod
    def _require(value, name):
        if value is None:
            raise LookupError(f"{name} not found")
        return value

    @staticmethod
    def _text(value, message):
        if not value or not value.strip():
            raise ValueError(message)

    @staticmethod
    def _choice(value, allowed, message):
        if value not in allowed:
            raise ValueError(message)
