"""Typed, application-owned representations of current FPL state."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


FPLPosition = Literal["GKP", "DEF", "MID", "FWD"]


class FPLClub(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    short_name: str


class FPLPlayer(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    first_name: str
    second_name: str
    display_name: str
    club_id: int
    club_name: str
    club_short_name: str
    position: FPLPosition
    price: float = Field(ge=0)
    ownership_percent: float | None = Field(default=None, ge=0)
    availability_status: str
    chance_of_playing_next_round: int | None = Field(default=None, ge=0, le=100)
    news: str | None = None


class FPLGameweek(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    name: str
    deadline: datetime | None = None
    finished: bool = False
    is_current: bool = False
    is_next: bool = False
    is_previous: bool = False


class FPLFixture(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None = None
    gameweek: int | None = Field(default=None, ge=1)
    home_club_id: int
    home_club_name: str
    home_club_short_name: str
    away_club_id: int
    away_club_name: str
    away_club_short_name: str
    kickoff: datetime | None = None
    home_difficulty: int | None = None
    away_difficulty: int | None = None
    finished: bool = False
    started: bool = False
    finished_provisional: bool | None = None
    minutes: int | None = None
    home_score: int | None = None
    away_score: int | None = None


class FPLPlayerGameweekStats(BaseModel):
    """Official, Gameweek-aggregated values from ``event/{gw}/live``."""

    model_config = ConfigDict(frozen=True)

    player_id: int
    minutes: int | None = None
    starts: int | None = None
    total_points: int | None = None
    goals_scored: int | None = None
    assists: int | None = None
    clean_sheets: int | None = None
    goals_conceded: int | None = None
    own_goals: int | None = None
    penalties_saved: int | None = None
    penalties_missed: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None
    saves: int | None = None
    bonus: int | None = None
    bps: int | None = None
    expected_goals: float | None = None
    expected_assists: float | None = None
    expected_goal_involvements: float | None = None
    expected_goals_conceded: float | None = None


class FPLBootstrap(BaseModel):
    """Normalized response from ``bootstrap-static``."""

    clubs: list[FPLClub]
    players: list[FPLPlayer]
    gameweeks: list[FPLGameweek]


class FPLSnapshot(BaseModel):
    """Coherent current official state used by application services.

    OfficialFPLAdapter-derived values are authoritative for official current
    FPL facts. Research may explain those facts later, but cannot replace them.
    """

    season_id: str
    retrieved_at: datetime
    players: list[FPLPlayer]
    clubs: list[FPLClub]
    gameweeks: list[FPLGameweek]
    fixtures: list[FPLFixture]
    current_gameweek: FPLGameweek | None = None
    next_gameweek: FPLGameweek | None = None
    fixture_sequence_by_club: dict[int, list[FPLFixture]] = Field(default_factory=dict)
    horizon_start: int | None = Field(default=None, ge=1)
    horizon_end: int | None = Field(default=None, ge=1)
