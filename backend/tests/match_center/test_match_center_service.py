from datetime import datetime, timezone
from types import SimpleNamespace
from fpl_intelligence.integrations.fpl.schemas import FPLFixture, FPLPlayerGameweekStats
from fpl_intelligence.match_center.service import MatchCenterService
from fpl_intelligence.models import Player
from fpl_intelligence.squads.service import PairSquadService
from backend.tests.squads.test_pair_squad_service import FakeProvider

class LiveProvider:
    def get_event_live(self, gameweek): return [FPLPlayerGameweekStats(player_id=i,total_points=i,minutes=90) for i in range(1,21)]
    def get_fixtures(self, gameweek): return [FPLFixture(id=1,gameweek=gameweek,home_club_id=1,home_club_name="A",home_club_short_name="A",away_club_id=2,away_club_name="B",away_club_short_name="B",kickoff=datetime.now(timezone.utc),started=True,finished=False,home_score=1,away_score=0)]
    def get_bootstrap(self): return SimpleNamespace(players=[SimpleNamespace(id=i,club_id=1 if i % 2 else 2,position="MID") for i in range(1,21)])

def test_refresh_persists_live_state_and_calculates_pair_score(database):
    s=database.session_factory(); s.add_all(Player(id=i) for i in range(1,21)); s.commit()
    pairs=PairSquadService(FakeProvider()); pairs.configure_pairs(s,our_pair={"name":"Us","entry_ids":[1,2]},opponent_pair={"name":"Them","entry_ids":[3,4]}); pairs.sync_all(s,1)
    result=MatchCenterService(LiveProvider(),FakeProvider()).refresh(s,1)
    assert result["snapshot_status"]=="available"; assert len(result["fixtures"])==1; assert len(result["managers"])==4
    assert result["scoreboard"]["our_pair"]["total"] > 0
    first=result["managers"][0]["starting_xi"][0]
    assert first["fixture"]["official_fixture_id"] == 1 and first["fixture_state"] == "live_fixture"
    assert result["captaincy"]["captaincy_swing"] == result["captaincy"]["our_captain_contribution"]-result["captaincy"]["opponent_captain_contribution"]
    assert result["player_swings"] == sorted(result["player_swings"],key=lambda x:(-abs(x["net_pair_swing"]),x["player"]["id"]))
