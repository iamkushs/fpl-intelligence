from fpl_intelligence.integrations.fpl.bootstrap import FPLBootstrapSyncService
from fpl_intelligence.integrations.fpl.schemas import FPLBootstrap, FPLClub, FPLGameweek, FPLPlayer
from fpl_intelligence.models import FPLClub as StoredClub, FPLGameweek as StoredGameweek, Player


class FakeAdapter:
    def __init__(self):
        self.bootstrap = FPLBootstrap(
            clubs=[FPLClub(id=1, name="Example FC", short_name="EXA")],
            gameweeks=[FPLGameweek(number=1, name="Gameweek 1", is_next=True)],
            players=[FPLPlayer(id=10, first_name="Alex", second_name="Example", display_name="A Example", club_id=1, club_name="Example FC", club_short_name="EXA", position="DEF", price=4.5, ownership_percent=12.3, availability_status="a")],
        )

    def get_bootstrap(self):
        return self.bootstrap


def test_bootstrap_sync_upserts_canonical_ids_and_updates_current_values(database):
    session = database.session_factory()
    adapter = FakeAdapter()
    service = FPLBootstrapSyncService(adapter)

    assert service.sync(session).players == 1
    adapter.bootstrap = adapter.bootstrap.model_copy(update={"clubs": [FPLClub(id=1, name="Renamed FC", short_name="REN")]})
    assert service.sync(session).clubs == 1

    assert session.get(StoredClub, 1).name == "Renamed FC"
    assert session.get(StoredGameweek, 1).is_next is True
    player = session.get(Player, 10)
    assert player.display_name == "A Example"
    assert player.club_id == 1
