"""Application service for lightweight research situations."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from fpl_intelligence.models import (
    ResearchSituation,
    ResearchSituationStatus,
    SituationHypothesis,
)
from fpl_intelligence.repositories.research_situations import ResearchSituationRepository


VALID_SITUATION_STATUSES = {
    ResearchSituationStatus.OPEN,
    ResearchSituationStatus.LEANING,
    ResearchSituationStatus.RESOLVED,
}


class ResearchSituationService:
    def __init__(self, repository: ResearchSituationRepository | None = None):
        self.repository = repository or ResearchSituationRepository()

    def create_situation(
        self,
        session: Session,
        *,
        title: str,
        context: str,
        fpl_relevance: str,
        club_id: int | None = None,
        player_ids: list[int] | None = None,
        status: str = ResearchSituationStatus.OPEN,
        last_checked_at: datetime | None = None,
    ) -> ResearchSituation:
        self._validate_text(title, "Situation title is required")
        self._validate_text(context, "Situation context is required")
        self._validate_text(fpl_relevance, "FPL relevance is required")
        self._validate_status(status)
        situation = self.repository.create(
            session,
            title=title.strip(),
            club_id=club_id,
            context=context.strip(),
            fpl_relevance=fpl_relevance.strip(),
            status=status,
            last_checked_at=last_checked_at,
        )
        self._attach_players(session, situation, player_ids or [])
        session.commit()
        return self.get_situation(session, situation.id) or situation

    def get_situation(self, session: Session, situation_id: str) -> ResearchSituation | None:
        return self.repository.get(session, situation_id)

    def update_situation(
        self,
        session: Session,
        situation_id: str,
        *,
        title: str | None = None,
        context: str | None = None,
        fpl_relevance: str | None = None,
        club_id: int | None = None,
        status: str | None = None,
        last_checked_at: datetime | None = None,
        touch_last_checked: bool = False,
    ) -> ResearchSituation:
        situation = self._require_situation(session, situation_id)
        if title is not None:
            self._validate_text(title, "Situation title is required")
            situation.title = title.strip()
        if context is not None:
            self._validate_text(context, "Situation context is required")
            situation.context = context.strip()
        if fpl_relevance is not None:
            self._validate_text(fpl_relevance, "FPL relevance is required")
            situation.fpl_relevance = fpl_relevance.strip()
        if club_id is not None:
            situation.club_id = club_id
        if status is not None:
            self._validate_status(status)
            situation.status = status
        if last_checked_at is not None:
            situation.last_checked_at = last_checked_at
        elif touch_last_checked:
            situation.last_checked_at = datetime.now(timezone.utc)
        session.commit()
        return self.get_situation(session, situation_id) or situation

    def attach_players(self, session: Session, situation_id: str, player_ids: list[int]) -> ResearchSituation:
        situation = self._require_situation(session, situation_id)
        self._attach_players(session, situation, player_ids)
        session.commit()
        return self.get_situation(session, situation_id) or situation

    def add_hypothesis(
        self,
        session: Session,
        situation_id: str,
        *,
        statement: str,
        active: bool = True,
    ) -> SituationHypothesis:
        self._require_situation(session, situation_id)
        self._validate_text(statement, "Hypothesis statement is required")
        hypothesis = self.repository.add_hypothesis(
            session, situation_id, statement=statement.strip(), active=active
        )
        session.commit()
        session.refresh(hypothesis)
        return hypothesis

    def update_hypothesis(
        self,
        session: Session,
        hypothesis_id: str,
        *,
        statement: str | None = None,
        active: bool | None = None,
    ) -> SituationHypothesis:
        hypothesis = self.repository.get_hypothesis(session, hypothesis_id)
        if hypothesis is None:
            raise LookupError("SituationHypothesis not found")
        if statement is not None:
            self._validate_text(statement, "Hypothesis statement is required")
            hypothesis.statement = statement.strip()
        if active is not None:
            hypothesis.active = active
        session.commit()
        session.refresh(hypothesis)
        return hypothesis

    def attach_trigger(self, session: Session, situation_id: str, trigger_id: str):
        self._require_situation(session, situation_id)
        trigger = self.repository.get_trigger(session, trigger_id)
        if trigger is None:
            raise LookupError("Research trigger not found")
        trigger.situation_id = situation_id
        session.commit()
        session.refresh(trigger)
        return trigger

    def attach_thread(self, session: Session, situation_id: str, thread_id: str):
        self._require_situation(session, situation_id)
        thread = self.repository.get_thread(session, thread_id)
        if thread is None:
            raise LookupError("ResearchThread not found")
        thread.situation_id = situation_id
        session.commit()
        session.refresh(thread)
        return thread

    def list_for_player(self, session: Session, player_id: int) -> list[ResearchSituation]:
        return self.repository.list_for_player(session, player_id)

    def _require_situation(self, session: Session, situation_id: str) -> ResearchSituation:
        situation = self.repository.get(session, situation_id)
        if situation is None:
            raise LookupError("ResearchSituation not found")
        return situation

    def _attach_players(self, session: Session, situation: ResearchSituation, player_ids: list[int]) -> None:
        unique_ids = list(dict.fromkeys(player_ids))
        players = self.repository.existing_players(session, unique_ids)
        players_by_id = {player.id: player for player in players}
        missing = [player_id for player_id in unique_ids if player_id not in players_by_id]
        if missing:
            raise LookupError(f"Player not found: {missing[0]}")
        existing = {player.id for player in situation.players}
        situation.players.extend(players_by_id[player_id] for player_id in unique_ids if player_id not in existing)

    @staticmethod
    def _validate_text(value: str, message: str) -> None:
        if not value.strip():
            raise ValueError(message)

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in VALID_SITUATION_STATUSES:
            raise ValueError("Research situation status must be open, leaning, or resolved")
