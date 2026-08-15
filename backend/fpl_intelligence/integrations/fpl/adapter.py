"""The sole HTTP and normalization boundary for public Official FPL data."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx

from fpl_intelligence.integrations.fpl.errors import (
    OfficialFPLHTTPError,
    OfficialFPLSchemaError,
    OfficialFPLTransportError,
)
from fpl_intelligence.integrations.fpl.schemas import (
    FPLBootstrap,
    FPLClub,
    FPLFixture,
    FPLGameweek,
    FPLPlayer,
    FPLPlayerGameweekStats,
)


POSITION_BY_ELEMENT_TYPE = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def _required(payload: Mapping[str, Any], field: str, *, context: str) -> Any:
    if field not in payload or payload[field] is None:
        raise OfficialFPLSchemaError(f"Official FPL {context} is missing required field '{field}'")
    return payload[field]


def _as_int(value: Any, *, field: str, context: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise OfficialFPLSchemaError(
            f"Official FPL {context} field '{field}' must be an integer"
        ) from exc


def _as_float(value: Any, *, field: str, context: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise OfficialFPLSchemaError(
            f"Official FPL {context} field '{field}' must be numeric"
        ) from exc


def _datetime(value: Any, *, field: str, context: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OfficialFPLSchemaError(f"Official FPL {context} field '{field}' must be a timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OfficialFPLSchemaError(
            f"Official FPL {context} field '{field}' must be an ISO timestamp"
        ) from exc


class OfficialFPLAdapter:
    """Fetch and normalize the public ``bootstrap-static`` and ``fixtures`` APIs."""

    def __init__(
        self,
        *,
        base_url: str = "https://fantasy.premierleague.com/api",
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._clubs_by_id: dict[int, FPLClub] = {}

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        client = self._client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self._client is None
        try:
            try:
                response = client.get(f"{self.base_url}/{path.lstrip('/')}", params=params)
            except httpx.TimeoutException as exc:
                raise OfficialFPLTransportError(
                    "Timed out while contacting the Official FPL API", cause=exc
                ) from exc
            except httpx.RequestError as exc:
                raise OfficialFPLTransportError(
                    "Could not contact the Official FPL API", cause=exc
                ) from exc
            if response.is_error:
                raise OfficialFPLHTTPError(response.status_code)
            try:
                return response.json()
            except ValueError as exc:
                raise OfficialFPLSchemaError("Official FPL returned invalid JSON", cause=exc) from exc
        finally:
            if close_client:
                client.close()

    def get_bootstrap(self) -> FPLBootstrap:
        payload = self._get_json("bootstrap-static/")
        if not isinstance(payload, Mapping):
            raise OfficialFPLSchemaError("Official FPL bootstrap response must be an object")
        clubs = self._normalize_clubs(_required(payload, "teams", context="bootstrap"))
        self._clubs_by_id = {club.id: club for club in clubs}
        players = self._normalize_players(_required(payload, "elements", context="bootstrap"), clubs)
        gameweeks = self._normalize_gameweeks(_required(payload, "events", context="bootstrap"))
        return FPLBootstrap(clubs=clubs, players=players, gameweeks=gameweeks)

    def get_fixtures(self, gameweek: int | None = None) -> list[FPLFixture]:
        params = {"event": gameweek} if gameweek is not None else None
        payload = self._get_json("fixtures/", params=params)
        if not isinstance(payload, list):
            raise OfficialFPLSchemaError("Official FPL fixtures response must be a list")
        # Fixture club labels are resolved against the authoritative bootstrap
        # response so downstream services never depend on team_h/team_a.
        if not self._clubs_by_id:
            self.get_bootstrap()
        return self._normalize_fixtures(payload, self._clubs_by_id)

    # The fetch_* names mirror the integration specification and are retained
    # as normalized aliases rather than exposing raw provider dictionaries.
    fetch_bootstrap = get_bootstrap
    fetch_fixtures = get_fixtures

    def get_gameweek_live(self, gameweek: int) -> list[FPLPlayerGameweekStats]:
        if gameweek < 1:
            raise ValueError("gameweek must be at least 1")
        payload = self._get_json(f"event/{gameweek}/live/")
        if not isinstance(payload, Mapping) or not isinstance(payload.get("elements"), list):
            raise OfficialFPLSchemaError("Official FPL Gameweek live response must contain elements")
        return self._normalize_gameweek_live(payload["elements"])

    fetch_gameweek_live = get_gameweek_live

    @staticmethod
    def _normalize_gameweek_live(payload: list[Any]) -> list[FPLPlayerGameweekStats]:
        integer_fields = (
            "minutes", "starts", "total_points", "goals_scored", "assists", "clean_sheets",
            "goals_conceded", "own_goals", "penalties_saved", "penalties_missed", "yellow_cards",
            "red_cards", "saves", "bonus", "bps",
        )
        float_fields = (
            "expected_goals", "expected_assists", "expected_goal_involvements", "expected_goals_conceded",
        )
        normalized = []
        for item in payload:
            if not isinstance(item, Mapping) or not isinstance(item.get("stats"), Mapping):
                raise OfficialFPLSchemaError("Official FPL Gameweek live elements must contain stats")
            player_id = _as_int(_required(item, "id", context="Gameweek live player"), field="id", context="Gameweek live player")
            stats = item["stats"]
            values = {field: (_as_int(stats[field], field=field, context=f"Gameweek live player {player_id}")
                              if stats.get(field) is not None else None) for field in integer_fields}
            values.update({field: (_as_float(stats[field], field=field, context=f"Gameweek live player {player_id}")
                                   if stats.get(field) is not None else None) for field in float_fields})
            normalized.append(FPLPlayerGameweekStats(player_id=player_id, **values))
        return normalized

    @staticmethod
    def _normalize_clubs(payload: Any) -> list[FPLClub]:
        if not isinstance(payload, list):
            raise OfficialFPLSchemaError("Official FPL bootstrap teams must be a list")
        clubs = []
        for item in payload:
            if not isinstance(item, Mapping):
                raise OfficialFPLSchemaError("Official FPL team entries must be objects")
            context = "team"
            clubs.append(
                FPLClub(
                    id=_as_int(_required(item, "id", context=context), field="id", context=context),
                    name=str(_required(item, "name", context=context)),
                    short_name=str(_required(item, "short_name", context=context)),
                )
            )
        return clubs

    @staticmethod
    def _normalize_players(payload: Any, clubs: list[FPLClub]) -> list[FPLPlayer]:
        if not isinstance(payload, list):
            raise OfficialFPLSchemaError("Official FPL bootstrap elements must be a list")
        clubs_by_id = {club.id: club for club in clubs}
        players = []
        for item in payload:
            if not isinstance(item, Mapping):
                raise OfficialFPLSchemaError("Official FPL player entries must be objects")
            context = "player"
            player_id = _as_int(_required(item, "id", context=context), field="id", context=context)
            club_id = _as_int(_required(item, "team", context=context), field="team", context=context)
            club = clubs_by_id.get(club_id)
            if club is None:
                raise OfficialFPLSchemaError(
                    f"Official FPL player {player_id} references unknown club {club_id}"
                )
            element_type = _as_int(
                _required(item, "element_type", context=context), field="element_type", context=context
            )
            position = POSITION_BY_ELEMENT_TYPE.get(element_type)
            if position is None:
                raise OfficialFPLSchemaError(
                    f"Official FPL player {player_id} has unknown element_type {element_type}"
                )
            chance = item.get("chance_of_playing_next_round")
            if chance is not None:
                chance = _as_int(chance, field="chance_of_playing_next_round", context=context)
            players.append(
                FPLPlayer(
                    id=player_id,
                    first_name=str(_required(item, "first_name", context=context)),
                    second_name=str(_required(item, "second_name", context=context)),
                    display_name=str(_required(item, "web_name", context=context)),
                    club_id=club.id,
                    club_name=club.name,
                    club_short_name=club.short_name,
                    position=position,
                    price=_as_int(
                        _required(item, "now_cost", context=context), field="now_cost", context=context
                    )
                    / 10,
                    ownership_percent=(
                        _as_float(item["selected_by_percent"], field="selected_by_percent", context=context)
                        if item.get("selected_by_percent") is not None
                        else None
                    ),
                    availability_status=str(_required(item, "status", context=context)),
                    chance_of_playing_next_round=chance,
                    news=item.get("news"),
                )
            )
        return players

    @staticmethod
    def _normalize_gameweeks(payload: Any) -> list[FPLGameweek]:
        if not isinstance(payload, list):
            raise OfficialFPLSchemaError("Official FPL bootstrap events must be a list")
        gameweeks = []
        for item in payload:
            if not isinstance(item, Mapping):
                raise OfficialFPLSchemaError("Official FPL event entries must be objects")
            context = "event"
            number = _as_int(_required(item, "id", context=context), field="id", context=context)
            gameweeks.append(
                FPLGameweek(
                    number=number,
                    name=str(_required(item, "name", context=context)),
                    deadline=_datetime(item.get("deadline_time"), field="deadline_time", context=context),
                    finished=bool(item.get("finished", False)),
                    is_current=bool(item.get("is_current", False)),
                    is_next=bool(item.get("is_next", False)),
                    is_previous=bool(item.get("is_previous", False)),
                )
            )
        return gameweeks

    @staticmethod
    def _normalize_fixtures(payload: list[Any], clubs_by_id: dict[int, FPLClub]) -> list[FPLFixture]:
        fixtures = []
        for item in payload:
            if not isinstance(item, Mapping):
                raise OfficialFPLSchemaError("Official FPL fixture entries must be objects")
            context = "fixture"
            home_id = _as_int(_required(item, "team_h", context=context), field="team_h", context=context)
            away_id = _as_int(_required(item, "team_a", context=context), field="team_a", context=context)
            home = clubs_by_id.get(home_id)
            away = clubs_by_id.get(away_id)
            if home is None or away is None:
                raise OfficialFPLSchemaError(
                    f"Official FPL fixture references unknown clubs {home_id} and {away_id}"
                )
            fixture_id = item.get("id")
            fixtures.append(
                FPLFixture(
                    id=(
                        _as_int(fixture_id, field="id", context=context)
                        if fixture_id is not None
                        else None
                    ),
                    gameweek=(
                        _as_int(item["event"], field="event", context=context)
                        if item.get("event") is not None
                        else None
                    ),
                    home_club_id=home.id,
                    home_club_name=home.name,
                    home_club_short_name=home.short_name,
                    away_club_id=away.id,
                    away_club_name=away.name,
                    away_club_short_name=away.short_name,
                    kickoff=_datetime(item.get("kickoff_time"), field="kickoff_time", context=context),
                    home_difficulty=(
                        _as_int(item["team_h_difficulty"], field="team_h_difficulty", context=context)
                        if item.get("team_h_difficulty") is not None
                        else None
                    ),
                    away_difficulty=(
                        _as_int(item["team_a_difficulty"], field="team_a_difficulty", context=context)
                        if item.get("team_a_difficulty") is not None
                        else None
                    ),
                    finished=bool(item.get("finished", False)),
                    started=bool(item.get("started", False)),
                )
            )
        return fixtures
