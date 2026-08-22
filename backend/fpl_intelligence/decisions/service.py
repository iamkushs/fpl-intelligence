"""Deterministic planning logic.  This module intentionally contains no scoring or recommendations."""
from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.models import (
    DecisionMovement, DecisionOption, DecisionOptionType, DecisionSession,
    DecisionSessionPick, DecisionSessionStatus, FPLManager, FPLManagerGameweekSnapshot,
    FPLManagerPair, FPLManagerPairMember, Player, ResearchDeepRun,
)


class DecisionError(ValueError):
    pass


class DecisionService:
    def create_or_reuse(self, session: Session, *, manager_id: int, gameweek: int) -> DecisionSession:
        manager = session.get(FPLManager, manager_id)
        if manager is None or not session.scalar(select(FPLManagerPairMember.id).join(FPLManagerPair).where(FPLManagerPairMember.manager_id == manager_id, FPLManagerPair.side == "ours")):
            raise DecisionError("manager_must_belong_to_our_configured_pair")
        snapshot = session.scalar(select(FPLManagerGameweekSnapshot).where(FPLManagerGameweekSnapshot.manager_id == manager_id, FPLManagerGameweekSnapshot.gameweek == gameweek).options(selectinload(FPLManagerGameweekSnapshot.picks)))
        if snapshot is None:
            raise DecisionError("frozen_squad_snapshot_not_found")
        existing = session.scalar(select(DecisionSession).where(DecisionSession.manager_id == manager_id, DecisionSession.snapshot_id == snapshot.id).options(selectinload(DecisionSession.frozen_picks)))
        if existing is not None:
            return existing
        result = DecisionSession(manager_id=manager_id, snapshot_id=snapshot.id, gameweek=gameweek, frozen_bank=snapshot.bank)
        session.add(result); session.flush()
        for pick in snapshot.picks:
            session.add(DecisionSessionPick(session_id=result.id, player_id=pick.player_id, squad_position=pick.squad_position, selling_price=pick.selling_price))
        session.flush()
        return result

    def get(self, session: Session, session_id: str) -> DecisionSession:
        result = session.scalar(select(DecisionSession).where(DecisionSession.id == session_id).options(selectinload(DecisionSession.manager), selectinload(DecisionSession.frozen_picks), selectinload(DecisionSession.options).selectinload(DecisionOption.movements)))
        if result is None:
            raise LookupError("decision_session_not_found")
        return result

    def list(self, session: Session, manager_id: int | None = None) -> list[DecisionSession]:
        query = select(DecisionSession).options(selectinload(DecisionSession.manager), selectinload(DecisionSession.options))
        if manager_id is not None: query = query.where(DecisionSession.manager_id == manager_id)
        return list(session.scalars(query.order_by(DecisionSession.created_at.desc())))

    def add_hold(self, session: Session, session_id: str, players: dict[int, object]) -> DecisionOption:
        return self._add_option(session, self.get(session, session_id), [], players, DecisionOptionType.HOLD)

    def add_transfers(self, session: Session, session_id: str, movements: list[dict], players: dict[int, object]) -> DecisionOption:
        if len(movements) not in (1, 2):
            raise DecisionError("transfer_option_requires_one_or_two_movements")
        return self._add_option(session, self.get(session, session_id), movements, players, DecisionOptionType.TRANSFER)

    def select(self, session: Session, session_id: str, option_id: str) -> DecisionSession:
        result = self.get(session, session_id)
        if result.status != DecisionSessionStatus.DRAFT: raise DecisionError("decision_session_is_finalized")
        option = session.get(DecisionOption, option_id)
        if option is None or option.session_id != result.id: raise DecisionError("decision_option_not_in_session")
        if not option.is_legal: raise DecisionError("invalid_option_cannot_be_selected")
        result.selected_option_id = option.id; session.flush(); return result

    def finalize(self, session: Session, session_id: str) -> DecisionSession:
        result = self.get(session, session_id)
        if result.status != DecisionSessionStatus.DRAFT: raise DecisionError("decision_session_is_finalized")
        option = session.get(DecisionOption, result.selected_option_id) if result.selected_option_id else None
        if option is None or not option.is_legal: raise DecisionError("legal_selected_option_required_for_finalization")
        result.status = DecisionSessionStatus.FINALIZED; result.finalized_option_id = option.id; result.finalized_at = datetime.now(timezone.utc)
        session.flush(); return result

    def _add_option(self, session: Session, decision: DecisionSession, movements: list[dict], players: dict[int, object], option_type: str) -> DecisionOption:
        if decision.status != DecisionSessionStatus.DRAFT: raise DecisionError("decision_session_is_finalized")
        errors, available, required = self._validate(decision, movements, players)
        option = DecisionOption(session_id=decision.id, option_type=option_type, is_legal=not errors, validation_errors=errors, budget_available=available, budget_required=required)
        session.add(option); session.flush()
        for sequence, movement in enumerate(movements, 1):
            outgoing, incoming = movement.get("outgoing_player_id"), movement.get("incoming_player_id")
            session.add(DecisionMovement(option_id=option.id, sequence=sequence, outgoing_player_id=outgoing, incoming_player_id=incoming, outgoing_synthesis_id=self._latest_synthesis(session, outgoing), incoming_synthesis_id=self._latest_synthesis(session, incoming)))
        session.flush(); return option

    @staticmethod
    def _validate(decision: DecisionSession, movements: list[dict], players: dict[int, object]) -> tuple[list[str], int | None, int | None]:
        errors: list[str] = []; frozen = {pick.player_id: pick for pick in decision.frozen_picks}; outgoing = [item.get("outgoing_player_id") for item in movements]; incoming = [item.get("incoming_player_id") for item in movements]
        if len(set(outgoing)) != len(outgoing) or len(set(incoming)) != len(incoming): errors.append("duplicate_transfer_player")
        for player_id in outgoing:
            if player_id not in frozen: errors.append("outgoing_not_in_frozen_squad")
        for player_id in incoming:
            if player_id not in players: errors.append("incoming_not_canonical")
        retained = set(frozen) - set(outgoing)
        if any(player_id in retained for player_id in incoming): errors.append("incoming_already_retained")
        final_ids = retained | set(incoming)
        if len(final_ids) != 15: errors.append("invalid_squad_size")
        final_players = [players.get(player_id) for player_id in final_ids]
        if any(player is None for player in final_players): errors.append("player_not_in_current_catalog")
        if not errors:
            positions = {name: sum(getattr(player, "position") == name for player in final_players) for name in ("GKP", "DEF", "MID", "FWD")}
            if positions != {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}: errors.append("invalid_position_composition")
            clubs: dict[int, int] = {}
            for player in final_players: clubs[player.club_id] = clubs.get(player.club_id, 0) + 1
            if any(count > 3 for count in clubs.values()): errors.append("club_limit_exceeded")
        available = decision.frozen_bank
        selling = []
        for player_id in outgoing:
            if player_id not in frozen:
                continue
            price = frozen[player_id].selling_price
            if price is None: errors.append("missing_selling_price")
            else: selling.append(price)
        if available is None: errors.append("missing_frozen_bank")
        required = sum(round(float(getattr(players[player_id], "price")) * 10) for player_id in incoming if player_id in players)
        if available is not None: available += sum(selling)
        if not errors and available is not None and required > available: errors.append("insufficient_budget")
        return sorted(set(errors)), available, required

    @staticmethod
    def _latest_synthesis(session: Session, player_id: int | None) -> str | None:
        if not isinstance(player_id, int): return None
        run = session.scalar(select(ResearchDeepRun).where(ResearchDeepRun.player_id == player_id, ResearchDeepRun.status.in_(("completed", "partial"))).options(selectinload(ResearchDeepRun.synthesis)).order_by(ResearchDeepRun.research_cutoff.desc(), ResearchDeepRun.id.desc()))
        return run.synthesis.id if run and run.synthesis else None
