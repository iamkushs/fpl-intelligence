from datetime import datetime, timezone

import httpx
import pytest

from fpl_intelligence.integrations.fpl.adapter import OfficialFPLAdapter
from fpl_intelligence.integrations.fpl.errors import (
    OfficialFPLHTTPError,
    OfficialFPLSchemaError,
    OfficialFPLTransportError,
)


BOOTSTRAP = {
    "teams": [{"id": 1, "name": "Example FC", "short_name": "EXA"}],
    "elements": [
        {
            "id": 10,
            "first_name": "Alex",
            "second_name": "Example",
            "web_name": "A Example",
            "team": 1,
            "element_type": 3,
            "now_cost": 55,
            "selected_by_percent": "12.4",
            "status": "a",
            "chance_of_playing_next_round": 75,
            "news": "Minor knock",
        }
    ],
    "events": [
        {
            "id": 1,
            "name": "Gameweek 1",
            "deadline_time": "2026-08-14T17:30:00Z",
            "finished": False,
            "is_current": False,
            "is_next": True,
            "is_previous": False,
        }
    ],
}
FIXTURES = [
    {
        "id": 99,
        "event": 1,
        "team_h": 1,
        "team_a": 1,
        "kickoff_time": "2026-08-15T15:00:00Z",
        "team_h_difficulty": 2,
        "team_a_difficulty": 4,
        "finished": False,
        "started": False,
    }
]


def adapter_for(payloads):
    def handler(request: httpx.Request):
        if request.url.path.endswith("bootstrap-static/"):
            return httpx.Response(200, json=payloads["bootstrap"])
        if "/event/" in request.url.path:
            return httpx.Response(200, json=payloads["live"])
        return httpx.Response(200, json=payloads["fixtures"])

    return OfficialFPLAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_bootstrap_normalization_converts_position_price_and_status():
    bootstrap = adapter_for({"bootstrap": BOOTSTRAP, "fixtures": FIXTURES}).get_bootstrap()

    player = bootstrap.players[0]
    assert player.position == "MID"
    assert player.price == 5.5
    assert player.club_name == "Example FC"
    assert player.ownership_percent == 12.4
    assert player.chance_of_playing_next_round == 75
    assert bootstrap.gameweeks[0].deadline == datetime(2026, 8, 14, 17, 30, tzinfo=timezone.utc)


def test_fixture_normalization_resolves_clubs_and_difficulty():
    adapter = adapter_for({"bootstrap": BOOTSTRAP, "fixtures": FIXTURES})
    fixture = adapter.get_fixtures()[0]

    assert fixture.id == 99
    assert fixture.gameweek == 1
    assert fixture.home_club_name == "Example FC"
    assert fixture.away_club_short_name == "EXA"
    assert fixture.home_difficulty == 2
    assert fixture.away_difficulty == 4


def test_schema_failure_is_explicit():
    adapter = adapter_for({"bootstrap": {"teams": [], "elements": []}, "fixtures": []})
    with pytest.raises(OfficialFPLSchemaError, match="events"):
        adapter.get_bootstrap()


def test_http_failure_is_explicit():
    def handler(request):
        return httpx.Response(503)

    adapter = OfficialFPLAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(OfficialFPLHTTPError) as error:
        adapter.get_bootstrap()
    assert error.value.status_code == 503


def test_transport_failure_is_explicit():
    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    adapter = OfficialFPLAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(OfficialFPLTransportError):
        adapter.get_bootstrap()


def test_gameweek_live_normalizes_official_stats_without_inventing_missing_fields():
    live = {"elements": [{"id": 10, "stats": {
        "minutes": 82, "total_points": 8, "goals_scored": 1, "assists": 0,
        "bonus": 2, "bps": 31, "expected_goals": "0.70",
    }}]}
    player = adapter_for({"bootstrap": BOOTSTRAP, "fixtures": FIXTURES, "live": live}).get_gameweek_live(7)[0]
    assert (player.player_id, player.minutes, player.total_points, player.goals_scored) == (10, 82, 8, 1)
    assert player.expected_goals == .7
    assert player.starts is None
