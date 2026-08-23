from datetime import datetime, timedelta, timezone

from fpl_intelligence.briefing.service import GameweekBriefingService, gameweek_phase
from fpl_intelligence.integrations.fpl.schemas import FPLBootstrap, FPLClub, FPLGameweek, FPLPlayer, FPLPlayerGameweekStats
from fpl_intelligence.models import FPLGameweek as StoredGameweek, PlayerResearchTrigger, ResearchQueueItem
from fpl_intelligence.watchlist.service import WatchlistService


class Adapter:
    def __init__(self):
        self.bootstrap = FPLBootstrap(clubs=[FPLClub(id=1,name="Club",short_name="CLB")], gameweeks=[FPLGameweek(number=4,name="GW4",is_current=True,deadline=datetime.now(timezone.utc)+timedelta(days=1))], players=[FPLPlayer(id=9,first_name="Test",second_name="Player",display_name="Test Player",club_id=1,club_name="Club",club_short_name="CLB",position="MID",price=5.0,ownership_percent=10.0,availability_status="a")])
    def get_bootstrap(self): return self.bootstrap
    def get_gameweek_live(self, gameweek): return [FPLPlayerGameweekStats(player_id=9,minutes=90,total_points=2)]


def test_refresh_is_explicit_idempotent_and_does_not_enqueue(database):
    adapter=Adapter(); service=GameweekBriefingService(adapter)
    with database.session_factory() as session:
        first=service.refresh(session); WatchlistService().add(session, 9)
        second=service.refresh(session); third=service.refresh(session)
        assert first.players_considered == 0
        assert second.pulses_created == 1 and third.pulses_updated == 1
        assert session.query(ResearchQueueItem).count() == 0


def test_briefing_is_durable_read_model_and_phase_is_deterministic(database):
    adapter=Adapter(); service=GameweekBriefingService(adapter)
    with database.session_factory() as session:
        service.refresh(session); WatchlistService().add(session, 9); session.add(PlayerResearchTrigger(player_id=9,trigger_type="manual_request",episode_key="x",source="user",status="open",priority=100,description="Check availability")); session.add(ResearchQueueItem(player_id=9,status="queued",source="user",queue_order=1,reason="Explicit")); session.commit()
        data=service.briefing(session)
        assert data["phase"] == "pre_deadline"
        assert data["research_summary"]["queued"] == 1
        assert data["attention_signals"][0]["already_queued"] is True
        assert gameweek_phase(session.get(StoredGameweek,4), datetime.now(timezone.utc)) == "pre_deadline"
