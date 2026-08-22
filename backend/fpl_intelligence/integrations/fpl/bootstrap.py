"""Persistence of the canonical public FPL bootstrap catalogue."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from fpl_intelligence.integrations.fpl.snapshot import FPLDataAdapter
from fpl_intelligence.models import FPLClub, FPLGameweek, Player


@dataclass(frozen=True)
class BootstrapSyncResult:
    clubs: int
    gameweeks: int
    players: int


class FPLBootstrapSyncService:
    """Upsert public FPL identities while retaining their official integer IDs."""

    def __init__(self, adapter: FPLDataAdapter):
        self.adapter = adapter

    def sync(self, session: Session) -> BootstrapSyncResult:
        bootstrap = self.adapter.get_bootstrap()
        for item in bootstrap.clubs:
            row = session.get(FPLClub, item.id) or FPLClub(id=item.id, name=item.name, short_name=item.short_name)
            row.name, row.short_name = item.name, item.short_name
            session.add(row)
        for item in bootstrap.gameweeks:
            row = session.get(FPLGameweek, item.number) or FPLGameweek(number=item.number, name=item.name)
            row.name, row.deadline = item.name, item.deadline
            row.finished, row.is_current, row.is_next, row.is_previous = item.finished, item.is_current, item.is_next, item.is_previous
            session.add(row)
        for item in bootstrap.players:
            row = session.get(Player, item.id) or Player(id=item.id)
            row.first_name, row.second_name, row.display_name = item.first_name, item.second_name, item.display_name
            row.club_id, row.position, row.price = item.club_id, item.position, item.price
            row.ownership_percent, row.availability_status = item.ownership_percent, item.availability_status
            row.chance_of_playing_next_round, row.news = item.chance_of_playing_next_round, item.news
            session.add(row)
        session.commit()
        return BootstrapSyncResult(clubs=len(bootstrap.clubs), gameweeks=len(bootstrap.gameweeks), players=len(bootstrap.players))
