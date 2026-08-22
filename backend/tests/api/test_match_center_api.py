from fastapi.testclient import TestClient
from fpl_intelligence.app import create_app
from fpl_intelligence.models import Player
from fpl_intelligence.integrations.fpl.schemas import FPLPlayerGameweekStats
from backend.tests.squads.test_pair_squad_service import FakeProvider

class LiveProvider:
    def get_event_live(self, gameweek): return [FPLPlayerGameweekStats(player_id=i,total_points=1) for i in range(1,21)]
    def get_fixtures(self, gameweek): return []

def test_match_center_refresh_is_explicit_and_get_is_durable(database):
    s=database.session_factory(); s.add_all(Player(id=i) for i in range(1,21)); s.commit()
    app=create_app(database=database,fpl_adapter=LiveProvider(),fpl_manager_provider=FakeProvider()); c=TestClient(app)
    body={"our_pair":{"name":"Us","entry_ids":[1,2]},"opponent_pair":{"name":"Them","entry_ids":[3,4]}}
    c.put("/fpl/pair-view/config",json=body); c.post("/fpl/pair-view/sync",json={"gameweek":1})
    assert c.get("/fpl/match-center").status_code==404
    assert c.post("/fpl/match-center/refresh",json={"gameweek":1}).status_code==200
    assert c.get("/fpl/match-center").json()["gameweek"]==1
