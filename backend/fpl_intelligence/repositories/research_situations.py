"""Data access for lightweight research situations."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.models import (
    Player,
    PlayerResearchTrigger,
    ResearchSituation,
    ResearchThread,
    SituationHypothesis,
)


class ResearchSituationRepository:
    def create(self, session: Session, **values) -> ResearchSituation:
        situation = ResearchSituation(**values)
        session.add(situation)
        session.flush()
        return situation

    def get(self, session: Session, situation_id: str) -> ResearchSituation | None:
        return session.scalar(
            select(ResearchSituation)
            .where(ResearchSituation.id == situation_id)
            .options(
                selectinload(ResearchSituation.players),
                selectinload(ResearchSituation.hypotheses),
            )
        )

    def existing_players(self, session: Session, player_ids: list[int]) -> list[Player]:
        if not player_ids:
            return []
        return list(session.scalars(select(Player).where(Player.id.in_(set(player_ids)))))

    def add_hypothesis(self, session: Session, situation_id: str, *, statement: str, active: bool) -> SituationHypothesis:
        hypothesis = SituationHypothesis(situation_id=situation_id, statement=statement, active=active)
        session.add(hypothesis)
        session.flush()
        return hypothesis

    def get_hypothesis(self, session: Session, hypothesis_id: str) -> SituationHypothesis | None:
        return session.get(SituationHypothesis, hypothesis_id)

    def get_trigger(self, session: Session, trigger_id: str) -> PlayerResearchTrigger | None:
        return session.get(PlayerResearchTrigger, trigger_id)

    def get_thread(self, session: Session, thread_id: str) -> ResearchThread | None:
        return session.get(ResearchThread, thread_id)

    def list_for_player(self, session: Session, player_id: int) -> list[ResearchSituation]:
        return list(
            session.scalars(
                select(ResearchSituation)
                .join(ResearchSituation.players)
                .where(Player.id == player_id)
                .options(
                    selectinload(ResearchSituation.players),
                    selectinload(ResearchSituation.hypotheses),
                )
                .order_by(ResearchSituation.updated_at.desc(), ResearchSituation.created_at.desc(), ResearchSituation.id)
            ).unique()
        )
