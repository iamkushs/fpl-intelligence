"""Application service for the two-stage research persistence workflow."""

from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fpl_intelligence.models import Player, ResearchLinkStatus, ResearchThreadStatus, ResearchThreadType
from fpl_intelligence.repositories.research_persistence import ResearchPersistenceRepository


def canonicalize_url(url: str) -> tuple[str, str]:
    raw = url.strip()
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Research links require an absolute HTTP(S) URL")
    host = parts.hostname.lower()
    port = parts.port
    netloc = host if port is None or (parts.scheme.lower(), port) in {("http", 80), ("https", 443)} else f"{host}:{port}"
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_")))
    return urlunsplit((parts.scheme.lower(), netloc, path, query, "")), host


class DuplicateResearchLinkError(ValueError):
    pass


class ResearchPersistenceService:
    def __init__(self, repository: ResearchPersistenceRepository | None = None):
        self.repository = repository or ResearchPersistenceRepository()

    def create_thread(self, session: Session, *, title: str, thread_type: str, gameweek_id: int | None = None, question: str | None = None):
        if not title.strip():
            raise ValueError("Research thread title is required")
        thread = self.repository.create_thread(session, title=title.strip(), thread_type=thread_type, status=ResearchThreadStatus.ACTIVE, gameweek_id=gameweek_id, question=question)
        session.commit()
        session.refresh(thread)
        return thread

    def add_collected_link(self, session: Session, *, thread_id: str, url: str, title: str | None = None, source_type: str | None = None, relevance_reason: str | None = None, player_ids: list[int] | None = None):
        canonical_url, domain = canonicalize_url(url)
        try:
            link = self.repository.add_link(session, research_thread_id=thread_id, original_url=url.strip(), canonical_url=canonical_url, domain=domain, title=title, source_type=source_type, relevance_reason=relevance_reason, status=ResearchLinkStatus.COLLECTED)
            link.players.extend(self.repository.get_or_create_player(session, player_id) for player_id in dict.fromkeys(player_ids or []))
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise DuplicateResearchLinkError("URL already collected in this research thread") from exc
        session.refresh(link)
        return link

    def list_links(self, session: Session, thread_id: str):
        return self.repository.list_links(session, thread_id)

    def associate_link_with_players(self, session: Session, link, player_ids: list[int]):
        existing = {player.id for player in link.players}
        link.players.extend(self.repository.get_or_create_player(session, player_id) for player_id in dict.fromkeys(player_ids) if player_id not in existing)
        session.commit()
        return link

    def persist_result(self, session: Session, *, link, summary: str, findings: str, evidence: str, player_ids: list[int], uncertainty: str | None = None, researched_at: datetime | None = None):
        result = self.repository.add_result(session, research_thread_id=link.research_thread_id, research_link_id=link.id, summary=summary, findings=findings, evidence=evidence, uncertainty=uncertainty, researched_at=researched_at or datetime.now(timezone.utc))
        result.players.extend(self.repository.get_or_create_player(session, player_id) for player_id in dict.fromkeys(player_ids))
        link.status = ResearchLinkStatus.RESEARCHED
        session.commit()
        session.refresh(result)
        return result

    def get_player_research(self, session: Session, player_id: int):
        return self.repository.results_for_player(session, player_id)

    def get_player_details(self, session: Session, player_id: int):
        player = self.repository.get_player_with_research(session, player_id)
        if player is None:
            return None
        return (
            player,
            self.repository.player_completed_results(player),
            self.repository.player_uncompleted_links(player),
        )

    def create_player_research_thread(
        self,
        session: Session,
        *,
        player_id: int,
        player_name: str,
        gameweek_id: int | None = None,
    ):
        if session.get(Player, player_id) is None:
            raise LookupError("Player not found")
        return self.create_thread(
            session,
            title=f"{player_name} research",
            thread_type=ResearchThreadType.PLAYER,
            gameweek_id=gameweek_id,
            question=f"What is the latest actionable FPL intelligence for {player_name}?",
        )
