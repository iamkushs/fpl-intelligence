"""Eager, bounded data access for atomic research evidence."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.models import (
    EvidenceHypothesisRelation,
    EvidenceRelation,
    Player,
    ResearchEvidence,
    ResearchLink,
    ResearchResult,
    ResearchSourceCluster,
    ResearchSourceClusterMembership,
    ResearchSituation,
    ResearchThread,
    SituationHypothesis,
)


class ResearchEvidenceRepository:
    _evidence_loads = (
        selectinload(ResearchEvidence.players),
        selectinload(ResearchEvidence.research_link),
        selectinload(ResearchEvidence.research_result).selectinload(ResearchResult.research_link),
        selectinload(ResearchEvidence.source_cluster),
        selectinload(ResearchEvidence.hypothesis_relations).selectinload(EvidenceHypothesisRelation.hypothesis),
    )

    def create_evidence(self, session: Session, **values) -> ResearchEvidence:
        evidence = ResearchEvidence(**values)
        session.add(evidence)
        session.flush()
        return evidence

    def get_evidence(self, session: Session, evidence_id: str) -> ResearchEvidence | None:
        return session.scalar(select(ResearchEvidence).where(ResearchEvidence.id == evidence_id).options(*self._evidence_loads).execution_options(populate_existing=True))

    def list_evidence(self, session: Session, *, thread_id: str | None = None, situation_id: str | None = None, player_id: int | None = None) -> list[ResearchEvidence]:
        statement = select(ResearchEvidence).options(*self._evidence_loads)
        if thread_id is not None:
            statement = statement.where(ResearchEvidence.research_thread_id == thread_id)
        if situation_id is not None:
            statement = statement.where(ResearchEvidence.research_situation_id == situation_id)
        if player_id is not None:
            statement = statement.join(ResearchEvidence.players).where(Player.id == player_id)
        return list(session.scalars(statement.order_by(ResearchEvidence.created_at, ResearchEvidence.id)).unique())

    def get_thread(self, session: Session, value: str) -> ResearchThread | None:
        return session.get(ResearchThread, value)

    def get_situation(self, session: Session, value: str) -> ResearchSituation | None:
        return session.get(ResearchSituation, value)

    def get_link(self, session: Session, value: str) -> ResearchLink | None:
        return session.get(ResearchLink, value)

    def get_result(self, session: Session, value: str) -> ResearchResult | None:
        return session.get(ResearchResult, value)

    def existing_players(self, session: Session, values: list[int]) -> list[Player]:
        return list(session.scalars(select(Player).where(Player.id.in_(set(values))))) if values else []

    def get_hypothesis(self, session: Session, value: str) -> SituationHypothesis | None:
        return session.get(SituationHypothesis, value)

    def add_hypothesis_relation(self, session: Session, **values) -> EvidenceHypothesisRelation:
        relation = EvidenceHypothesisRelation(**values)
        session.add(relation)
        session.flush()
        return relation

    def add_evidence_relation(self, session: Session, **values) -> EvidenceRelation:
        relation = EvidenceRelation(**values)
        session.add(relation)
        session.flush()
        return relation

    def list_relations(self, session: Session, evidence_id: str) -> list[EvidenceRelation]:
        return list(session.scalars(
            select(EvidenceRelation).where(
                (EvidenceRelation.from_evidence_id == evidence_id) | (EvidenceRelation.to_evidence_id == evidence_id)
            ).options(selectinload(EvidenceRelation.from_evidence), selectinload(EvidenceRelation.to_evidence))
        ))

    def list_relations_for_evidence(self, session: Session, evidence_ids: list[str]) -> list[EvidenceRelation]:
        """Load relation traversal for a collection in one bounded query."""
        if not evidence_ids:
            return []
        return list(session.scalars(
            select(EvidenceRelation)
            .where(
                EvidenceRelation.from_evidence_id.in_(set(evidence_ids))
                | EvidenceRelation.to_evidence_id.in_(set(evidence_ids))
            )
            .options(selectinload(EvidenceRelation.from_evidence), selectinload(EvidenceRelation.to_evidence))
        ).unique())

    def create_cluster(self, session: Session, **values) -> ResearchSourceCluster:
        cluster = ResearchSourceCluster(**values)
        session.add(cluster)
        session.flush()
        return cluster

    def get_cluster(self, session: Session, cluster_id: str) -> ResearchSourceCluster | None:
        return session.scalar(select(ResearchSourceCluster).where(ResearchSourceCluster.id == cluster_id).options(
            selectinload(ResearchSourceCluster.memberships).selectinload(ResearchSourceClusterMembership.research_link)
        ).execution_options(populate_existing=True))

    def add_membership(self, session: Session, **values) -> ResearchSourceClusterMembership:
        membership = ResearchSourceClusterMembership(**values)
        session.add(membership)
        session.flush()
        return membership

    def get_membership(self, session: Session, cluster_id: str, link_id: str) -> ResearchSourceClusterMembership | None:
        return session.scalar(select(ResearchSourceClusterMembership).where(
            ResearchSourceClusterMembership.source_cluster_id == cluster_id,
            ResearchSourceClusterMembership.research_link_id == link_id,
        ))
