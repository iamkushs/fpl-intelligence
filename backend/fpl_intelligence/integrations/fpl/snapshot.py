"""Construction of the canonical current FPL snapshot."""

from datetime import datetime, timezone
from typing import Protocol

from fpl_intelligence.integrations.fpl.schemas import FPLBootstrap, FPLFixture, FPLSnapshot


class FPLDataAdapter(Protocol):
    def get_bootstrap(self) -> FPLBootstrap: ...

    def get_fixtures(self, gameweek: int | None = None) -> list[FPLFixture]: ...


def _season_label(now: datetime) -> str:
    """Return the season containing ``now`` without assuming GW1 forever."""

    start_year = now.year if now.month >= 7 else now.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


class FPLSnapshotService:
    def __init__(self, adapter: FPLDataAdapter, *, season_id: str | None = None):
        self.adapter = adapter
        self.season_id = season_id

    def get_snapshot(
        self,
        *,
        season_id: str | None = None,
        horizon_start: int | None = None,
        horizon_end: int | None = None,
    ) -> FPLSnapshot:
        if horizon_start is not None and horizon_start < 1:
            raise ValueError("horizon_start must be at least 1")
        if horizon_end is not None and horizon_end < 1:
            raise ValueError("horizon_end must be at least 1")
        if horizon_start is not None and horizon_end is not None and horizon_start > horizon_end:
            raise ValueError("horizon_start must not be greater than horizon_end")

        bootstrap = self.adapter.get_bootstrap()
        fixtures = self.adapter.get_fixtures()
        selected_fixtures = [
            fixture
            for fixture in fixtures
            if fixture.gameweek is not None
            and (horizon_start is None or fixture.gameweek >= horizon_start)
            and (horizon_end is None or fixture.gameweek <= horizon_end)
        ]
        # With no requested horizon, all fixtures are part of current context.
        if horizon_start is None and horizon_end is None:
            selected_fixtures = fixtures
        selected_fixtures.sort(
            key=lambda fixture: (
                fixture.gameweek or 10_000,
                fixture.kickoff or datetime.max.replace(tzinfo=timezone.utc),
            )
        )

        fixture_sequence: dict[int, list[FPLFixture]] = {club.id: [] for club in bootstrap.clubs}
        for fixture in selected_fixtures:
            fixture_sequence.setdefault(fixture.home_club_id, []).append(fixture)
            fixture_sequence.setdefault(fixture.away_club_id, []).append(fixture)

        current = next((gameweek for gameweek in bootstrap.gameweeks if gameweek.is_current), None)
        next_gameweek = next((gameweek for gameweek in bootstrap.gameweeks if gameweek.is_next), None)
        return FPLSnapshot(
            season_id=season_id or self.season_id or _season_label(datetime.now(timezone.utc)),
            retrieved_at=datetime.now(timezone.utc),
            players=bootstrap.players,
            clubs=bootstrap.clubs,
            gameweeks=bootstrap.gameweeks,
            fixtures=selected_fixtures,
            current_gameweek=current,
            next_gameweek=next_gameweek,
            fixture_sequence_by_club=fixture_sequence,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
        )
