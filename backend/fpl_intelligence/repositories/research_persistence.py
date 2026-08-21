"""Data access for collected links and durable link research."""

from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fpl_intelligence.models import Player, ResearchLink, ResearchLinkStatus, ResearchResult, ResearchThread


class ResearchPersistenceRepository:
    def create_thread(self, session: Session, **values) -> ResearchThread:
        thread = ResearchThread(**values)
        session.add(thread)
        session.flush()
        return thread

    def add_link(self, session: Session, **values) -> ResearchLink:
        link = ResearchLink(**values)
        session.add(link)
        session.flush()
        return link

    def list_links(self, session: Session, thread_id: str) -> list[ResearchLink]:
        return list(session.scalars(select(ResearchLink).where(ResearchLink.research_thread_id == thread_id).order_by(ResearchLink.discovered_at, ResearchLink.id)))

    def get_thread(self, session: Session, thread_id: str) -> ResearchThread | None:
        return session.get(ResearchThread, thread_id)

    def get_link(self, session: Session, link_id: str) -> ResearchLink | None:
        return session.scalar(
            select(ResearchLink)
            .where(ResearchLink.id == link_id)
            .options(selectinload(ResearchLink.players))
        )

    def get_link_by_canonical_url(self, session: Session, *, thread_id: str, canonical_url: str) -> ResearchLink | None:
        return session.scalar(
            select(ResearchLink)
            .where(ResearchLink.research_thread_id == thread_id, ResearchLink.canonical_url == canonical_url)
            .options(selectinload(ResearchLink.players), selectinload(ResearchLink.results))
        )

    def list_results(self, session: Session, thread_id: str) -> list[ResearchResult]:
        return list(
            session.scalars(
                select(ResearchResult)
                .where(ResearchResult.research_thread_id == thread_id)
                .options(selectinload(ResearchResult.players), selectinload(ResearchResult.research_link))
                .order_by(ResearchResult.researched_at.desc(), ResearchResult.id)
            ).unique()
        )

    def existing_players(self, session: Session, player_ids: list[int]) -> list[Player]:
        if not player_ids:
            return []
        return list(session.scalars(select(Player).where(Player.id.in_(set(player_ids)))))

    def get_player_with_research(self, session: Session, player_id: int) -> Player | None:
        """Load the complete player research view with bounded eager queries."""
        return session.scalar(
            select(Player)
            .where(Player.id == player_id)
            .options(
                selectinload(Player.research_results).selectinload(ResearchResult.research_link),
                selectinload(Player.research_results).selectinload(ResearchResult.thread),
                selectinload(Player.research_links).selectinload(ResearchLink.thread),
            )
        )

    def player_completed_results(self, player: Player) -> list[ResearchResult]:
        return sorted(
            {result.id: result for result in player.research_results}.values(),
            key=lambda result: (result.researched_at, result.id),
            reverse=True,
        )

    def player_uncompleted_links(self, player: Player) -> list[ResearchLink]:
        return sorted(
            {
                link.id: link
                for link in player.research_links
                if link.status != ResearchLinkStatus.RESEARCHED
            }.values(),
            key=lambda link: (link.discovered_at, link.id),
            reverse=True,
        )

    def get_or_create_player(self, session: Session, player_id: int) -> Player:
        player = session.get(Player, player_id)
        if player is None:
            player = Player(id=player_id)
            session.add(player)
            session.flush()
        return player

    def add_result(self, session: Session, **values) -> ResearchResult:
        result = ResearchResult(**values)
        session.add(result)
        session.flush()
        return result

    def get_result_by_link_prompt_cutoff(
        self,
        session: Session,
        *,
        research_link_id: str,
        prompt_version: str,
        research_cutoff,
    ) -> ResearchResult | None:
        candidates = session.scalars(
            select(ResearchResult).where(
                ResearchResult.research_link_id == research_link_id,
                ResearchResult.prompt_version == prompt_version,
            )
        )
        for result in candidates:
            if _normalize_result_cutoff(result.research_cutoff) == _normalize_result_cutoff(research_cutoff):
                return result
        return None

    def results_for_player(self, session: Session, player_id: int) -> list[ResearchResult]:
        statement = (
            select(ResearchResult)
            .join(ResearchResult.players)
            .where(Player.id == player_id)
            .options(
                selectinload(ResearchResult.players),
                selectinload(ResearchResult.research_link),
                selectinload(ResearchResult.thread),
            )
            .order_by(ResearchResult.researched_at.desc(), ResearchResult.id)
        )
        return list(session.scalars(statement).unique())


def _normalize_result_cutoff(value):
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
