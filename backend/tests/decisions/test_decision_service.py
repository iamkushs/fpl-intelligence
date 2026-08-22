from types import SimpleNamespace

import pytest

from fpl_intelligence.decisions.service import DecisionError, DecisionService
from fpl_intelligence.models import FPLManager, FPLManagerGameweekPick, FPLManagerGameweekSnapshot, FPLManagerPair, FPLManagerPairMember, Player


def players():
    result = {}
    for number, position in enumerate(["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3, 1):
        result[number] = SimpleNamespace(id=number, position=position, club_id=number, price=5.0)
    result[16] = SimpleNamespace(id=16, position="MID", club_id=16, price=6.0)
    return result


def prepared(session):
    manager = FPLManager(entry_id=10); pair = FPLManagerPair(name="Ours", side="ours"); session.add_all([manager, pair]); session.flush()
    session.add(FPLManagerPairMember(pair_id=pair.id, manager_id=manager.id, slot=1))
    snapshot = FPLManagerGameweekSnapshot(manager_id=manager.id, gameweek=2, bank=10, fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)); session.add(snapshot); session.flush()
    session.add_all(Player(id=i) for i in range(1, 17))
    session.add_all(FPLManagerGameweekPick(snapshot_id=snapshot.id, player_id=i, squad_position=i, multiplier=1, is_captain=False, is_vice_captain=False, selling_price=50) for i in range(1, 16)); session.commit()
    return manager, snapshot


def test_freezes_squad_and_enforces_budget_legality(database):
    session = database.session_factory(); manager, snapshot = prepared(session); service = DecisionService(); decision = service.create_or_reuse(session, manager_id=manager.id, gameweek=2)
    snapshot.picks[0].selling_price = 1; session.commit()
    assert decision.frozen_picks[0].selling_price == 50
    option = service.add_transfers(session, decision.id, [{"outgoing_player_id": 12, "incoming_player_id": 16}], players())
    assert option.is_legal and option.budget_available == 60 and option.budget_required == 60
    bad = service.add_transfers(session, decision.id, [{"outgoing_player_id": 99, "incoming_player_id": 16}], players())
    assert bad.validation_errors == ["invalid_squad_size", "outgoing_not_in_frozen_squad"]


def test_invalid_option_cannot_be_selected_and_finalization_is_explicit(database):
    session = database.session_factory(); manager, _ = prepared(session); service = DecisionService(); decision = service.create_or_reuse(session, manager_id=manager.id, gameweek=2)
    invalid = service.add_transfers(session, decision.id, [{"outgoing_player_id": 1, "incoming_player_id": 99}], players())
    with pytest.raises(DecisionError, match="invalid_option_cannot_be_selected"): service.select(session, decision.id, invalid.id)
    hold = service.add_hold(session, decision.id, players()); service.select(session, decision.id, hold.id); completed = service.finalize(session, decision.id)
    assert completed.finalized_option_id == hold.id and completed.status == "finalized"
