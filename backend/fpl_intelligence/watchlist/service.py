"""Durable Watchlist membership behavior."""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fpl_intelligence.models import Player, ResearchResult, WatchlistAddedSource, WatchlistEntry, research_result_players


class WatchlistService:
    def get(self, session: Session, player_id: int) -> WatchlistEntry | None:
        return session.scalar(select(WatchlistEntry).where(WatchlistEntry.player_id == player_id))

    def add(self, session: Session, player_id: int, *, reason: str | None = None, pinned: bool = False,
            source: str = WatchlistAddedSource.USER, commit: bool = True) -> WatchlistEntry:
        if session.get(Player, player_id) is None:
            raise LookupError("Player not found")
        entry = self.get(session, player_id)
        if entry and entry.active:
            return entry
        now = datetime.now(timezone.utc)
        if entry is None:
            entry = WatchlistEntry(player_id=player_id, added_source=source)
            session.add(entry)
        entry.active = True
        entry.pinned = pinned
        entry.added_source = source
        entry.addition_reason = reason
        entry.added_at = now
        entry.removed_at = None
        entry.removal_reason = None
        if commit:
            session.commit()
            session.refresh(entry)
        else:
            session.flush()
        return entry

    def remove(self, session: Session, player_id: int, *, reason: str | None = None) -> WatchlistEntry:
        entry = self.get(session, player_id)
        if entry is None or not entry.active:
            raise LookupError("Active Watchlist entry not found")
        entry.active = False
        entry.removed_at = datetime.now(timezone.utc)
        entry.removal_reason = reason
        session.commit()
        session.refresh(entry)
        return entry

    def set_pinned(self, session: Session, player_id: int, pinned: bool) -> WatchlistEntry:
        entry = self.get(session, player_id)
        if entry is None or not entry.active:
            raise LookupError("Active Watchlist entry not found")
        entry.pinned = pinned
        session.commit()
        session.refresh(entry)
        return entry

    def list_active(self, session: Session) -> list[tuple[WatchlistEntry, datetime | None]]:
        latest = (
            select(research_result_players.c.player_id, func.max(ResearchResult.researched_at).label("last_research_at"))
            .join(ResearchResult, ResearchResult.id == research_result_players.c.research_result_id)
            .group_by(research_result_players.c.player_id)
            .subquery()
        )
        statement = (
            select(WatchlistEntry, latest.c.last_research_at)
            .outerjoin(latest, latest.c.player_id == WatchlistEntry.player_id)
            .where(WatchlistEntry.active.is_(True))
            .order_by(WatchlistEntry.pinned.desc(), WatchlistEntry.added_at.desc())
        )
        return list(session.execute(statement).all())
