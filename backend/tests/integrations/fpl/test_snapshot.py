from datetime import datetime, timezone

from fastapi.testclient import TestClient

from fpl_intelligence.app import create_app
from fpl_intelligence.config import Settings
from fpl_intelligence.integrations.fpl.schemas import (
    FPLBootstrap,
    FPLClub,
    FPLFixture,
    FPLGameweek,
    FPLPlayer,
)
from fpl_intelligence.integrations.fpl.snapshot import FPLSnapshotService


class FakeOfficialFPLAdapter:
    def __init__(self, bootstrap, fixtures):
        self.bootstrap = bootstrap
        self.fixtures = fixtures

    def get_bootstrap(self):
        return self.bootstrap

    def get_fixtures(self, gameweek=None):
        return self.fixtures


def sample_bootstrap(current=False):
    return FPLBootstrap(
        clubs=[FPLClub(id=1, name="Example FC", short_name="EXA")],
        players=[
            FPLPlayer(
                id=10,
                first_name="Alex",
                second_name="Example",
                display_name="A Example",
                club_id=1,
                club_name="Example FC",
                club_short_name="EXA",
                position="DEF",
                price=4.5,
                availability_status="a",
            )
        ],
        gameweeks=[
            FPLGameweek(
                number=1,
                name="Gameweek 1",
                deadline=datetime(2026, 8, 14, 17, 30, tzinfo=timezone.utc),
                is_current=current,
                is_next=not current,
            )
        ],
    )


def test_snapshot_builds_fixture_sequences_and_supports_horizon():
    fixture = FPLFixture(
        id=99,
        gameweek=1,
        home_club_id=1,
        home_club_name="Example FC",
        home_club_short_name="EXA",
        away_club_id=1,
        away_club_name="Example FC",
        away_club_short_name="EXA",
    )
    service = FPLSnapshotService(
        FakeOfficialFPLAdapter(sample_bootstrap(), [fixture]), season_id="2026-27"
    )

    snapshot = service.get_snapshot(horizon_start=1, horizon_end=1)

    assert snapshot.season_id == "2026-27"
    assert snapshot.current_gameweek is None
    assert snapshot.next_gameweek.number == 1
    assert snapshot.fixtures == [fixture]
    assert snapshot.fixture_sequence_by_club[1] == [fixture, fixture]


def test_snapshot_allows_current_gameweek_after_season_opening():
    service = FPLSnapshotService(FakeOfficialFPLAdapter(sample_bootstrap(current=True), []))

    snapshot = service.get_snapshot()

    assert snapshot.current_gameweek.number == 1
    assert snapshot.next_gameweek is None


def test_snapshot_endpoint_returns_serializable_canonical_state():
    service = FPLSnapshotService(FakeOfficialFPLAdapter(sample_bootstrap(), []), season_id="2026-27")
    app = create_app(Settings(), fpl_snapshot_service=service)

    with TestClient(app) as client:
        response = client.get("/fpl/snapshot?horizon_start=1&horizon_end=1")

    assert response.status_code == 200
    assert response.json()["season_id"] == "2026-27"
    assert response.json()["players"][0]["position"] == "DEF"
