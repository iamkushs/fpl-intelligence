from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from fpl_intelligence.models import FPLManagerGameweekSnapshot, Player
from fpl_intelligence.squads.service import PairConfigurationError, PairSquadService

class FakeProvider:
    def get_entry(self, entry_id): return {"player_first_name":f"M{entry_id}","player_last_name":"Test","name":f"Team {entry_id}"}
    def get_entry_history(self, entry_id): return {"current":[{"event":1,"points":42,"total_points":100,"overall_rank":123}]}
    def get_gameweek_picks(self, entry_id, gameweek):
        base=(entry_id-1)*3
        return {"active_chip":None,"entry_history":{"bank":10,"value":1000},"picks":[{"element":((base+i-1)%20)+1,"position":i,"multiplier":2 if i==1 else 1,"is_captain":i==1,"is_vice_captain":i==2,"purchase_price":50,"selling_price":50} for i in range(1,16)]}

def configured(service, session): return service.configure_pairs(session,our_pair={"name":"Us","entry_ids":[1,2]},opponent_pair={"name":"Them","entry_ids":[3,4]})
def test_configure_sync_idempotency_and_view(database):
    session=database.session_factory(); session.add_all(Player(id=i) for i in range(1,21)); session.commit(); service=PairSquadService(FakeProvider()); configured(service,session)
    assert service.sync_all(session,1)==[{"entry_id":i,"status":"synced"} for i in range(1,5)]
    service.sync_manager(session,1,1); assert len(list(session.scalars(select(FPLManagerGameweekSnapshot))))==4
    view=service.get_pair_view(session,1); assert len(view["our_pair"]["managers"][0]["starting_xi"])==11; assert len(view["our_pair"]["managers"][0]["bench"])==4; assert any(item["exposure_state"]=="universal" for item in view["exposure"]); assert view["overlap"]["ours"]["shared_player_count"] >= 0
def test_duplicate_manager_is_rejected(database):
    with pytest.raises(PairConfigurationError): PairSquadService().configure_pairs(database.session_factory(),our_pair={"name":"Us","entry_ids":[1,2]},opponent_pair={"name":"Them","entry_ids":[2,3]})
def test_unknown_player_does_not_create_snapshot(database):
    session=database.session_factory(); session.add(Player(id=1)); session.commit()
    with pytest.raises(LookupError): PairSquadService(FakeProvider()).sync_manager(session,1,1)
    assert not list(session.scalars(select(FPLManagerGameweekSnapshot)))
