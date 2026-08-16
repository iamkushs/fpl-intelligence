"""Durable application models for the initial vertical slice."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Float,
    JSON,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from fpl_intelligence.db.base import Base


class ResearchRunStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_GAPS = "COMPLETE_WITH_GAPS"
    FAILED = "FAILED"


class ResearchSectionStatus:
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_GAPS = "COMPLETE_WITH_GAPS"
    FAILED = "FAILED"


class ResearchJobStatus:
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED_RATE_LIMIT = "PAUSED_RATE_LIMIT"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_GAPS = "COMPLETE_WITH_GAPS"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"

    SUCCESSFUL_TERMINAL = frozenset({COMPLETE, COMPLETE_WITH_GAPS})
    TERMINAL = frozenset({COMPLETE, COMPLETE_WITH_GAPS, FAILED, SUPERSEDED})


research_job_dependencies = Table(
    "research_job_dependencies",
    Base.metadata,
    Column("job_id", String(36), ForeignKey("research_jobs.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "depends_on_job_id",
        String(36),
        ForeignKey("research_jobs.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
    CheckConstraint("job_id <> depends_on_job_id", name="ck_research_job_dependencies_not_self"),
)


research_link_players = Table(
    "research_link_players",
    Base.metadata,
    Column("research_link_id", String(36), ForeignKey("research_links.id", ondelete="CASCADE"), primary_key=True),
    Column("player_id", Integer, ForeignKey("players.id", ondelete="CASCADE"), primary_key=True),
)

research_result_players = Table(
    "research_result_players",
    Base.metadata,
    Column("research_result_id", String(36), ForeignKey("research_results.id", ondelete="CASCADE"), primary_key=True),
    Column("player_id", Integer, ForeignKey("players.id", ondelete="CASCADE"), primary_key=True),
)

situation_players = Table(
    "situation_players",
    Base.metadata,
    Column("situation_id", String(36), ForeignKey("research_situations.id", ondelete="CASCADE"), primary_key=True),
    Column("player_id", Integer, ForeignKey("players.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_situation_players_player_id", "player_id"),
)

research_evidence_players = Table(
    "research_evidence_players",
    Base.metadata,
    Column("evidence_id", String(36), ForeignKey("research_evidence.id", ondelete="CASCADE"), primary_key=True),
    Column("player_id", Integer, ForeignKey("players.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_research_evidence_players_player_id", "player_id"),
)

research_quality_run_links = Table(
    "research_quality_run_links",
    Base.metadata,
    Column("quality_run_id", String(36), ForeignKey("research_quality_runs.id", ondelete="CASCADE"), primary_key=True),
    Column("research_link_id", String(36), ForeignKey("research_links.id", ondelete="CASCADE"), primary_key=True),
)

research_quality_run_evidence = Table(
    "research_quality_run_evidence",
    Base.metadata,
    Column("quality_run_id", String(36), ForeignKey("research_quality_runs.id", ondelete="CASCADE"), primary_key=True),
    Column("research_evidence_id", String(36), ForeignKey("research_evidence.id", ondelete="CASCADE"), primary_key=True),
)

watchlist_suggestion_results = Table(
    "watchlist_suggestion_results",
    Base.metadata,
    Column("suggestion_id", String(36), ForeignKey("watchlist_suggestions.id", ondelete="CASCADE"), primary_key=True),
    Column("research_result_id", String(36), ForeignKey("research_results.id", ondelete="CASCADE"), primary_key=True),
)


class ResearchThreadType:
    DISCOVERY = "discovery"
    PLAYER = "player"
    USER_QUESTION = "user_question"
    INVESTIGATION = "investigation"


class ResearchThreadStatus:
    ACTIVE = "active"
    COMPLETE = "complete"
    FAILED = "failed"


class ResearchSituationStatus:
    OPEN = "open"
    LEANING = "leaning"
    RESOLVED = "resolved"
    ACTIVE = frozenset({OPEN, LEANING})


class ResearchLinkStatus:
    COLLECTED = "collected"
    RESEARCHED = "researched"
    FAILED = "failed"
    IGNORED = "ignored"


class ResearchEvidenceType:
    FACT = "fact"
    STATISTIC = "statistic"
    REPORT = "report"
    SUPPORTER_OBSERVATION = "supporter_observation"
    SPECULATION = "speculation"
    INFERENCE = "inference"


class EvidenceRelationshipType:
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"


class SourceClusterMembershipType:
    ORIGINAL = "original"
    INDEPENDENT = "independent"
    DERIVATIVE = "derivative"
    UNCLEAR = "unclear"


class ResearchDiscoveryStatus:
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class ResearchDiscoveryPhase:
    BROAD = "broad"
    TARGETED = "targeted"


class ResearchSourceCandidateStatus:
    COLLECTED = "collected"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    FAILED = "failed"


class ResearchPageResearchAttemptStatus:
    RESEARCHED = "researched"
    FAILED = "failed"


class ResearchQualityStage:
    REDDIT = "reddit"
    COUNTER_SEARCH = "counter_search"
    FRESHNESS = "freshness"


class ResearchQualityStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class Player(Base):
    """A durable reference to an official FPL player."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    research_links: Mapped[list["ResearchLink"]] = relationship(
        secondary=research_link_players, back_populates="players"
    )
    research_results: Mapped[list["ResearchResult"]] = relationship(
        secondary=research_result_players, back_populates="players"
    )
    watchlist_entry: Mapped["WatchlistEntry | None"] = relationship(back_populates="player")
    watchlist_suggestions: Mapped[list["WatchlistSuggestion"]] = relationship(back_populates="player")
    gameweek_pulses: Mapped[list["PlayerGameweekPulse"]] = relationship(back_populates="player")
    research_triggers: Mapped[list["PlayerResearchTrigger"]] = relationship(back_populates="player")
    monitoring_triggers: Mapped[list["MonitoringTrigger"]] = relationship(back_populates="player")
    research_situations: Mapped[list["ResearchSituation"]] = relationship(
        secondary=situation_players, back_populates="players"
    )
    research_evidence: Mapped[list["ResearchEvidence"]] = relationship(
        secondary=research_evidence_players, back_populates="players"
    )


class ResearchSituation(Base):
    """Shared football/FPL context that can involve one or more canonical players."""

    __tablename__ = "research_situations"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'leaning', 'resolved')", name="ck_research_situations_status"),
        Index("ix_research_situations_club_status", "club_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    club_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    fpl_relevance: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ResearchSituationStatus.OPEN, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    players: Mapped[list[Player]] = relationship(
        secondary=situation_players, back_populates="research_situations"
    )
    hypotheses: Mapped[list["SituationHypothesis"]] = relationship(
        back_populates="situation", cascade="all, delete-orphan", order_by="SituationHypothesis.created_at"
    )
    research_triggers: Mapped[list["PlayerResearchTrigger"]] = relationship(back_populates="situation")
    research_threads: Mapped[list["ResearchThread"]] = relationship(back_populates="situation")


class SituationHypothesis(Base):
    """Lightweight candidate interpretation for a research situation."""

    __tablename__ = "situation_hypotheses"
    __table_args__ = (
        Index("ix_situation_hypotheses_situation_active", "situation_id", "active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    situation_id: Mapped[str] = mapped_column(ForeignKey("research_situations.id", ondelete="CASCADE"), nullable=False, index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    situation: Mapped[ResearchSituation] = relationship(back_populates="hypotheses")


class PlayerGameweekPulse(Base):
    """Durable official performance facts, independent of current Watchlist membership."""

    __tablename__ = "player_gameweek_pulses"
    __table_args__ = (
        UniqueConstraint("player_id", "gameweek", name="uq_player_gameweek_pulses_player_gameweek"),
        Index("ix_player_gameweek_pulses_player_gameweek", "player_id", "gameweek"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    gameweek: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    starts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals_scored: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clean_sheets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals_conceded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    own_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    penalties_saved: Mapped[int | None] = mapped_column(Integer, nullable=True)
    penalties_missed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yellow_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    red_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bonus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_goals: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_assists: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_goal_involvements: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_goals_conceded: Mapped[float | None] = mapped_column(Float, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    player: Mapped[Player] = relationship(back_populates="gameweek_pulses")


class ResearchTriggerStatus:
    OPEN = "open"
    QUEUED = "queued"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    ACTIVE = frozenset({OPEN, QUEUED})


class ResearchTriggerSource:
    PULSE = "pulse"
    RESEARCH = "research"
    SYSTEM = "system"
    USER = "user"


class PlayerResearchTrigger(Base):
    """An episode explaining why a player deserves investigation."""

    __tablename__ = "player_research_triggers"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'queued', 'resolved', 'dismissed')", name="ck_player_research_triggers_status"),
        CheckConstraint("source IN ('pulse', 'research', 'system', 'user')", name="ck_player_research_triggers_source"),
        Index("ix_player_research_triggers_player_status", "player_id", "status"),
        Index(
            "uq_player_research_triggers_active_episode", "player_id", "trigger_type", "episode_key",
            unique=True, sqlite_where=text("status IN ('open', 'queued')"),
            postgresql_where=text("status IN ('open', 'queued')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    episode_key: Mapped[str] = mapped_column(String(64), nullable=False, default="current")
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ResearchTriggerStatus.OPEN, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    gameweek: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    monitoring_trigger_id: Mapped[str | None] = mapped_column(ForeignKey("monitoring_triggers.id", ondelete="SET NULL"), nullable=True, index=True)
    situation_id: Mapped[str | None] = mapped_column(ForeignKey("research_situations.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    player: Mapped[Player] = relationship(back_populates="research_triggers")
    monitoring_trigger: Mapped["MonitoringTrigger | None"] = relationship(back_populates="research_triggers")
    situation: Mapped["ResearchSituation | None"] = relationship(back_populates="research_triggers")


class MonitoringTrigger(Base):
    """A concrete future event selected during or after research."""

    __tablename__ = "monitoring_triggers"
    __table_args__ = (
        CheckConstraint(
            "category IN ('appearance', 'minutes', 'attacking_return', 'set_piece', 'availability', "
            "'team_selection', 'transfer', 'tactical_role', 'fixture', 'manager_comment', 'other')",
            name="ck_monitoring_triggers_category",
        ),
        Index("ix_monitoring_triggers_player_active", "player_id", "active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    research_result_id: Mapped[str | None] = mapped_column(ForeignKey("research_results.id", ondelete="SET NULL"), nullable=True, index=True)
    research_thread_id: Mapped[str | None] = mapped_column(ForeignKey("research_threads.id", ondelete="SET NULL"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1", index=True)
    condition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    satisfied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    player: Mapped[Player] = relationship(back_populates="monitoring_triggers")
    research_result: Mapped["ResearchResult | None"] = relationship()
    research_thread: Mapped["ResearchThread | None"] = relationship()
    research_triggers: Mapped[list[PlayerResearchTrigger]] = relationship(back_populates="monitoring_trigger")


class WatchlistAddedSource:
    USER = "user"
    RESEARCH = "research"
    SYSTEM = "system"


class WatchlistEntry(Base):
    """Durable current and most-recent Watchlist membership for a player."""

    __tablename__ = "watchlist_entries"
    __table_args__ = (
        CheckConstraint("added_source IN ('user', 'research', 'system')", name="ck_watchlist_entries_added_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1", index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    added_source: Mapped[str] = mapped_column(String(32), nullable=False)
    addition_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    player: Mapped[Player] = relationship(back_populates="watchlist_entry")


class WatchlistSuggestionStatus:
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class WatchlistSuggestion(Base):
    """Evidence-backed proposal that never implies Watchlist membership by itself."""

    __tablename__ = "watchlist_suggestions"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="ck_watchlist_suggestions_status"),
        UniqueConstraint("player_id", "research_thread_id", name="uq_watchlist_suggestions_player_thread"),
        Index(
            "uq_watchlist_suggestions_pending_player",
            "player_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    research_thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=WatchlistSuggestionStatus.PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    player: Mapped[Player] = relationship(back_populates="watchlist_suggestions")
    thread: Mapped["ResearchThread"] = relationship(back_populates="watchlist_suggestions")
    research_results: Mapped[list["ResearchResult"]] = relationship(secondary=watchlist_suggestion_results)


class ResearchThread(Base):
    """One bounded link-collection and research activity."""

    __tablename__ = "research_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ResearchThreadStatus.ACTIVE, index=True)
    gameweek_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    situation_id: Mapped[str | None] = mapped_column(ForeignKey("research_situations.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    links: Mapped[list["ResearchLink"]] = relationship(back_populates="thread", cascade="all, delete-orphan")
    results: Mapped[list["ResearchResult"]] = relationship(back_populates="thread", cascade="all, delete-orphan")
    discovery_executions: Mapped[list["ResearchDiscoveryExecution"]] = relationship(back_populates="thread", cascade="all, delete-orphan")
    watchlist_suggestions: Mapped[list[WatchlistSuggestion]] = relationship(back_populates="thread", cascade="all, delete-orphan")
    situation: Mapped["ResearchSituation | None"] = relationship(back_populates="research_threads")


class ResearchLink(Base):
    """A URL collected for later research."""

    __tablename__ = "research_links"
    __table_args__ = (UniqueConstraint("research_thread_id", "canonical_url", name="uq_research_links_thread_canonical_url"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relevance_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ResearchLinkStatus.COLLECTED, index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovery_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    thread: Mapped[ResearchThread] = relationship(back_populates="links")
    players: Mapped[list[Player]] = relationship(secondary=research_link_players, back_populates="research_links")
    results: Mapped[list["ResearchResult"]] = relationship(back_populates="research_link", cascade="all, delete-orphan")
    page_research_attempts: Mapped[list["ResearchPageResearchAttempt"]] = relationship(back_populates="research_link", cascade="all, delete-orphan")


class ResearchResult(Base):
    """Durable findings produced by researching one collected link."""

    __tablename__ = "research_results"
    __table_args__ = (
        Index(
            "uq_research_results_link_prompt_cutoff",
            "research_link_id",
            "prompt_version",
            "research_cutoff",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    research_link_id: Mapped[str] = mapped_column(ForeignKey("research_links.id", ondelete="CASCADE"), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    findings: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    research_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    researched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    thread: Mapped[ResearchThread] = relationship(back_populates="results")
    research_link: Mapped[ResearchLink] = relationship(back_populates="results")
    players: Mapped[list[Player]] = relationship(secondary=research_result_players, back_populates="research_results")
    page_research_attempts: Mapped[list["ResearchPageResearchAttempt"]] = relationship(back_populates="research_result")


class ResearchPageResearchAttempt(Base):
    """Durable status for researching one link under one prompt/cutoff request."""

    __tablename__ = "research_page_research_attempts"
    __table_args__ = (
        CheckConstraint("status IN ('researched', 'failed')", name="ck_research_page_research_attempts_status"),
        Index(
            "uq_research_page_attempt_link_prompt_cutoff",
            "research_link_id",
            "prompt_version",
            "research_cutoff",
            unique=True,
        ),
        Index("ix_research_page_attempts_thread_status", "research_thread_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    research_link_id: Mapped[str] = mapped_column(ForeignKey("research_links.id", ondelete="CASCADE"), nullable=False, index=True)
    research_result_id: Mapped[str | None] = mapped_column(ForeignKey("research_results.id", ondelete="SET NULL"), nullable=True, index=True)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    research_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_research_model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    research_link: Mapped[ResearchLink] = relationship(back_populates="page_research_attempts")
    research_result: Mapped[ResearchResult | None] = relationship(back_populates="page_research_attempts")


class ResearchDiscoveryExecution(Base):
    """One player-centred Eval 2 source-discovery execution."""

    __tablename__ = "research_discovery_executions"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'complete', 'partial', 'failed')", name="ck_research_discovery_executions_status"),
        Index("ix_research_discovery_executions_thread_status", "research_thread_id", "status"),
        Index("ix_research_discovery_executions_player_status", "player_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False, index=True)
    research_situation_id: Mapped[str | None] = mapped_column(ForeignKey("research_situations.id", ondelete="SET NULL"), nullable=True, index=True)
    trigger_id: Mapped[str | None] = mapped_column(ForeignKey("player_research_triggers.id", ondelete="SET NULL"), nullable=True, index=True)
    gameweek_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    target_gameweek_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    research_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    discovery_prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    page_research_prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ResearchDiscoveryStatus.RUNNING, index=True)
    known_missing_dimensions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    durable_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    thread: Mapped[ResearchThread] = relationship(back_populates="discovery_executions")
    player: Mapped[Player] = relationship()
    situation: Mapped["ResearchSituation | None"] = relationship()
    trigger: Mapped["PlayerResearchTrigger | None"] = relationship()
    candidates: Mapped[list["ResearchSourceCandidate"]] = relationship(back_populates="execution", cascade="all, delete-orphan")


class ResearchSourceCandidate(Base):
    """A discovered source candidate; it is not evidence and not researched content."""

    __tablename__ = "research_source_candidates"
    __table_args__ = (
        UniqueConstraint("discovery_execution_id", "canonical_url", name="uq_research_source_candidates_execution_url"),
        CheckConstraint("discovery_phase IN ('broad', 'targeted')", name="ck_research_source_candidates_phase"),
        CheckConstraint("expected_relevance IN ('high', 'medium', 'low')", name="ck_research_source_candidates_relevance"),
        CheckConstraint("lineage_type IN ('original', 'independent', 'derivative', 'unclear')", name="ck_research_source_candidates_lineage_type"),
        CheckConstraint("status IN ('collected', 'duplicate', 'rejected', 'failed')", name="ck_research_source_candidates_status"),
        Index("ix_research_source_candidates_thread_phase", "research_thread_id", "discovery_phase"),
        Index("ix_research_source_candidates_link", "research_link_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    discovery_execution_id: Mapped[str] = mapped_column(ForeignKey("research_discovery_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    research_thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    research_link_id: Mapped[str | None] = mapped_column(ForeignKey("research_links.id", ondelete="SET NULL"), nullable=True, index=True)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_dimensions: Mapped[list] = mapped_column(JSON, nullable=False)
    usefulness: Mapped[str] = mapped_column(Text, nullable=False)
    source_category: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_relevance: Mapped[str] = mapped_column(String(16), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recency: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lineage_type: Mapped[str] = mapped_column(String(32), nullable=False, default=SourceClusterMembershipType.UNCLEAR)
    lineage_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovery_phase: Mapped[str] = mapped_column(String(32), nullable=False)
    discovery_prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    research_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ResearchSourceCandidateStatus.COLLECTED, index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    execution: Mapped[ResearchDiscoveryExecution] = relationship(back_populates="candidates")
    research_link: Mapped[ResearchLink | None] = relationship()


class ResearchSourceCluster(Base):
    """One information lineage; it is not a count of pages repeating a claim."""

    __tablename__ = "research_source_clusters"
    __table_args__ = (Index("ix_research_source_clusters_thread_situation", "research_thread_id", "research_situation_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="RESTRICT"), nullable=False, index=True)
    research_situation_id: Mapped[str | None] = mapped_column(ForeignKey("research_situations.id", ondelete="RESTRICT"), nullable=True, index=True)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    likely_original_research_link_id: Mapped[str | None] = mapped_column(ForeignKey("research_links.id", ondelete="RESTRICT"), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    memberships: Mapped[list["ResearchSourceClusterMembership"]] = relationship(back_populates="source_cluster", order_by="ResearchSourceClusterMembership.created_at")


class ResearchSourceClusterMembership(Base):
    __tablename__ = "research_source_cluster_memberships"
    __table_args__ = (
        UniqueConstraint("source_cluster_id", "research_link_id", name="uq_source_cluster_memberships_cluster_link"),
        CheckConstraint("lineage_type IN ('original', 'independent', 'derivative', 'unclear')", name="ck_source_cluster_memberships_lineage_type"),
        Index("ix_source_cluster_memberships_link_id", "research_link_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_cluster_id: Mapped[str] = mapped_column(ForeignKey("research_source_clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    research_link_id: Mapped[str] = mapped_column(ForeignKey("research_links.id", ondelete="RESTRICT"), nullable=False)
    lineage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    source_cluster: Mapped[ResearchSourceCluster] = relationship(back_populates="memberships")
    research_link: Mapped[ResearchLink] = relationship()


class ResearchEvidence(Base):
    """One materially coherent claim. Similar text is deliberately not deduplicated."""

    __tablename__ = "research_evidence"
    __table_args__ = (
        CheckConstraint("evidence_type IN ('fact', 'statistic', 'report', 'supporter_observation', 'speculation', 'inference')", name="ck_research_evidence_evidence_type"),
        CheckConstraint("reliability IN ('high', 'medium', 'low')", name="ck_research_evidence_reliability"),
        CheckConstraint("relevance IN ('high', 'medium', 'low')", name="ck_research_evidence_relevance"),
        Index("ix_research_evidence_thread_situation", "research_thread_id", "research_situation_id"),
        Index("ix_research_evidence_source_cluster_id", "source_cluster_id"),
        Index("uq_research_evidence_result_extraction_fingerprint", "research_result_id", "extraction_prompt_version", "extraction_fingerprint", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="RESTRICT"), nullable=False, index=True)
    research_situation_id: Mapped[str | None] = mapped_column(ForeignKey("research_situations.id", ondelete="RESTRICT"), nullable=True, index=True)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    research_link_id: Mapped[str | None] = mapped_column(ForeignKey("research_links.id", ondelete="RESTRICT"), nullable=True, index=True)
    research_result_id: Mapped[str | None] = mapped_column(ForeignKey("research_results.id", ondelete="RESTRICT"), nullable=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    season: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reliability: Mapped[str] = mapped_column(String(16), nullable=False)
    relevance: Mapped[str] = mapped_column(String(16), nullable=False)
    is_volatile: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0", index=True)
    source_cluster_id: Mapped[str | None] = mapped_column(ForeignKey("research_source_clusters.id", ondelete="RESTRICT"), nullable=True)
    extraction_prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    extraction_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    players: Mapped[list[Player]] = relationship(secondary=research_evidence_players, back_populates="research_evidence")
    research_link: Mapped[ResearchLink | None] = relationship(foreign_keys=[research_link_id])
    research_result: Mapped[ResearchResult | None] = relationship(foreign_keys=[research_result_id])
    source_cluster: Mapped[ResearchSourceCluster | None] = relationship(foreign_keys=[source_cluster_id])
    hypothesis_relations: Mapped[list["EvidenceHypothesisRelation"]] = relationship(back_populates="evidence")


class ResearchQualityRun(Base):
    """Durable quality-control pass over research evidence or source material."""

    __tablename__ = "research_quality_runs"
    __table_args__ = (
        Index("ix_research_quality_runs_thread_id", "thread_id"),
        Index("ix_research_quality_runs_player_id", "player_id"),
        Index("ix_research_quality_runs_stage", "stage"),
        Index("ix_research_quality_runs_status", "status"),
        Index("ix_research_quality_runs_research_cutoff", "research_cutoff"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False)
    situation_id: Mapped[str | None] = mapped_column(ForeignKey("research_situations.id", ondelete="SET NULL"), nullable=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    target_evidence_id: Mapped[str | None] = mapped_column(ForeignKey("research_evidence.id", ondelete="SET NULL"), nullable=True)
    superseding_evidence_id: Mapped[str | None] = mapped_column(ForeignKey("research_evidence.id", ondelete="SET NULL"), nullable=True)
    research_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    challenged_claim: Mapped[str | None] = mapped_column(Text, nullable=True)
    questions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    thread: Mapped[ResearchThread] = relationship()
    player: Mapped[Player] = relationship()
    situation: Mapped[ResearchSituation | None] = relationship()
    target_evidence: Mapped[ResearchEvidence | None] = relationship(foreign_keys=[target_evidence_id])
    superseding_evidence: Mapped[ResearchEvidence | None] = relationship(foreign_keys=[superseding_evidence_id])
    links: Mapped[list[ResearchLink]] = relationship(secondary=research_quality_run_links)
    evidence: Mapped[list[ResearchEvidence]] = relationship(secondary=research_quality_run_evidence)


class EvidenceHypothesisRelation(Base):
    __tablename__ = "evidence_hypothesis_relations"
    __table_args__ = (
        UniqueConstraint("evidence_id", "hypothesis_id", "relationship_type", name="uq_evidence_hypothesis_relations_identity"),
        CheckConstraint("relationship_type IN ('supports', 'contradicts')", name="ck_evidence_hypothesis_relations_type"),
        Index("ix_evidence_hypothesis_relations_hypothesis_id", "hypothesis_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    evidence_id: Mapped[str] = mapped_column(ForeignKey("research_evidence.id", ondelete="CASCADE"), nullable=False, index=True)
    hypothesis_id: Mapped[str] = mapped_column(ForeignKey("situation_hypotheses.id", ondelete="RESTRICT"), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(16), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    evidence: Mapped[ResearchEvidence] = relationship(back_populates="hypothesis_relations")
    hypothesis: Mapped[SituationHypothesis] = relationship()


class EvidenceRelation(Base):
    __tablename__ = "evidence_relations"
    __table_args__ = (
        UniqueConstraint("from_evidence_id", "to_evidence_id", "relation_type", name="uq_evidence_relations_identity"),
        CheckConstraint("from_evidence_id <> to_evidence_id", name="ck_evidence_relations_not_self"),
        CheckConstraint("relation_type IN ('supports', 'contradicts', 'supersedes')", name="ck_evidence_relations_type"),
        Index("ix_evidence_relations_to_evidence_id", "to_evidence_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    from_evidence_id: Mapped[str] = mapped_column(ForeignKey("research_evidence.id", ondelete="RESTRICT"), nullable=False, index=True)
    to_evidence_id: Mapped[str] = mapped_column(ForeignKey("research_evidence.id", ondelete="RESTRICT"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    from_evidence: Mapped[ResearchEvidence] = relationship(foreign_keys=[from_evidence_id])
    to_evidence: Mapped[ResearchEvidence] = relationship(foreign_keys=[to_evidence_id])


class ResearchRun(Base):
    """One coherent, durable research cycle."""

    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    season_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    gameweek_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(64), nullable=False, default="STANDARD")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ResearchRunStatus.RUNNING, index=True
    )
    research_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sections: Mapped[list["ResearchSection"]] = relationship(
        back_populates="research_run", cascade="all, delete-orphan", order_by="ResearchSection.ordering"
    )
    jobs: Mapped[list["ResearchJob"]] = relationship(
        back_populates="research_run", cascade="all, delete-orphan", order_by="ResearchJob.ordering"
    )
    documents: Mapped[list["ResearchDocument"]] = relationship(
        back_populates="research_run", foreign_keys="ResearchDocument.research_run_id"
    )


class ResearchSection(Base):
    """A configurable group of jobs within a research run."""

    __tablename__ = "research_sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_run_id: Mapped[str] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    name = synonym("title")
    ordering: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order = synonym("ordering")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ResearchSectionStatus.READY, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    research_run: Mapped[ResearchRun] = relationship(back_populates="sections")
    jobs: Mapped[list["ResearchJob"]] = relationship(
        back_populates="research_section", cascade="all, delete-orphan", order_by="ResearchJob.ordering"
    )
    documents: Mapped[list["ResearchDocument"]] = relationship(
        back_populates="research_section", foreign_keys="ResearchDocument.research_section_id"
    )


class ResearchJob(Base):
    """The durable, not-yet-executed unit of research work."""

    __tablename__ = "research_jobs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_research_jobs_attempt_count_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_run_id: Mapped[str] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    research_section_id: Mapped[str] = mapped_column(
        ForeignKey("research_sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    title = synonym("subject")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    request = synonym("question")
    ordering: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order = synonym("ordering")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ResearchJobStatus.PENDING, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    codex_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    codex_turn_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    research_run: Mapped[ResearchRun] = relationship(back_populates="jobs")
    research_section: Mapped[ResearchSection] = relationship(back_populates="jobs")
    dependencies: Mapped[list["ResearchJob"]] = relationship(
        "ResearchJob",
        secondary=research_job_dependencies,
        primaryjoin=id == research_job_dependencies.c.job_id,
        secondaryjoin=id == research_job_dependencies.c.depends_on_job_id,
        back_populates="dependents",
    )
    dependents: Mapped[list["ResearchJob"]] = relationship(
        "ResearchJob",
        secondary=research_job_dependencies,
        primaryjoin=id == research_job_dependencies.c.depends_on_job_id,
        secondaryjoin=id == research_job_dependencies.c.job_id,
        back_populates="dependencies",
    )
    documents: Mapped[list["ResearchDocument"]] = relationship(
        back_populates="research_job", foreign_keys="ResearchDocument.research_job_id"
    )


class ResearchDocument(Base):
    """A durable free-form research artifact and its small metadata envelope."""

    __tablename__ = "research_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    research_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    research_section_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_sections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    research_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    season_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    gameweek_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    codex_thread_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    codex_turn_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CURRENT", index=True)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    usage_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    execution_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    research_job: Mapped[ResearchJob | None] = relationship(
        back_populates="documents", foreign_keys=[research_job_id]
    )
    research_run: Mapped[ResearchRun | None] = relationship(
        back_populates="documents", foreign_keys=[research_run_id]
    )
    research_section: Mapped[ResearchSection | None] = relationship(
        back_populates="documents", foreign_keys=[research_section_id]
    )
