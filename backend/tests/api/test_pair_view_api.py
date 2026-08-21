from fastapi.testclient import TestClient
from fpl_intelligence.app import create_app
from fpl_intelligence.models import Player
from backend.tests.squads.test_pair_squad_service import FakeProvider

def test_pair_view_config_sync_and_read(database):
    session=database.session_factory(); session.add_all(Player(id=i) for i in range(1,21)); session.commit()
    app=create_app(database=database,fpl_manager_provider=FakeProvider()); client=TestClient(app)
    body={"our_pair":{"name":"Us","entry_ids":[1,2]},"opponent_pair":{"name":"Them","entry_ids":[3,4]}}
    assert client.put("/fpl/pair-view/config",json=body).status_code==200
    assert client.post("/fpl/pair-view/sync",json={"gameweek":1}).status_code==200
    assert client.get("/fpl/pair-view").json()["gameweek"]==1
    assert len(client.get("/fpl/pair-view/history").json())==1
