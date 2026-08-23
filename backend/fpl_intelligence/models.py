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


class ResearchEvidenceBundleStatus:
    DRAFT = "draft"
    ASSESSED = "assessed"


class ResearchDeepRunStatus:
    PENDING = "pending"; RUNNING = "running"; RESEARCH_COMPLETE = "research_complete"; BLIND_SPOT_COMPLETE = "blind_spot_complete"; COMPLETED = "completed"; PARTIAL = "partial"; FAILED = "failed"


class Player(Base):
    """A durable reference to an official FPL player."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    first_name: Mapped[str | None] = mapped_column(String(255))
    second_name: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    club_id: Mapped[int | None] = mapped_column(Integer, index=True)
    position: Mapped[str | None] = mapped_column(String(8))
    price: Mapped[float | None] = mapped_column(Float)
    ownership_percent: Mapped[float | None] = mapped_column(Float)
    availability_status: Mapped[str | None] = mapped_column(String(32))
    chance_of_playing_next_round: Mapped[int | None] = mapped_column(Integer)
    news: Mapped[str | None] = mapped_column(Text)
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
    squad_picks: Mapped[list["FPLManagerGameweekPick"]] = relationship(back_populates="player")


class FPLClub(Base):
    __tablename__ = "fpl_clubs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str] = mapped_column(String(32), nullable=False)


class FPLGameweek(Base):
    __tablename__ = "fpl_gameweeks"

    number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_next: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_previous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class FPLManager(Base):
    __tablename__ = "fpl_managers"
    __table_args__ = (UniqueConstraint("entry_id", name="uq_fpl_managers_entry_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    manager_name: Mapped[str | None] = mapped_column(String(255))
    team_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    memberships: Mapped[list["FPLManagerPairMember"]] = relationship(back_populates="manager")
    snapshots: Mapped[list["FPLManagerGameweekSnapshot"]] = relationship(back_populates="manager")


class FPLManagerPair(Base):
    __tablename__ = "fpl_manager_pairs"
    __table_args__ = (CheckConstraint("side IN ('ours', 'opponent')", name="ck_fpl_manager_pairs_side"), UniqueConstraint("side", name="uq_fpl_manager_pairs_side"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    members: Mapped[list["FPLManagerPairMember"]] = relationship(back_populates="pair", cascade="all, delete-orphan", order_by="FPLManagerPairMember.slot")


class FPLManagerPairMember(Base):
    __tablename__ = "fpl_manager_pair_members"
    __table_args__ = (CheckConstraint("slot IN (1, 2)", name="ck_fpl_manager_pair_members_slot"), UniqueConstraint("pair_id", "manager_id", name="uq_fpl_pair_member_manager"), UniqueConstraint("pair_id", "slot", name="uq_fpl_pair_member_slot"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pair_id: Mapped[int] = mapped_column(ForeignKey("fpl_manager_pairs.id", ondelete="CASCADE"), nullable=False, index=True)
    manager_id: Mapped[int] = mapped_column(ForeignKey("fpl_managers.id", ondelete="RESTRICT"), nullable=False, index=True)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    pair: Mapped[FPLManagerPair] = relationship(back_populates="members")
    manager: Mapped[FPLManager] = relationship(back_populates="memberships")


class FPLManagerGameweekSnapshot(Base):
    __tablename__ = "fpl_manager_gameweek_snapshots"
    __table_args__ = (UniqueConstraint("manager_id", "gameweek", name="uq_fpl_manager_gameweek_snapshot"), Index("ix_fpl_manager_snapshots_manager_gameweek", "manager_id", "gameweek"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    manager_id: Mapped[int] = mapped_column(ForeignKey("fpl_managers.id", ondelete="CASCADE"), nullable=False, index=True)
    gameweek: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_points: Mapped[int | None] = mapped_column(Integer)
    total_points: Mapped[int | None] = mapped_column(Integer)
    overall_rank: Mapped[int | None] = mapped_column(Integer)
    bank: Mapped[int | None] = mapped_column(Integer)
    squad_value: Mapped[int | None] = mapped_column(Integer)
    active_chip: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    manager: Mapped[FPLManager] = relationship(back_populates="snapshots")
    picks: Mapped[list["FPLManagerGameweekPick"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan", order_by="FPLManagerGameweekPick.squad_position")


class FPLManagerGameweekPick(Base):
    __tablename__ = "fpl_manager_gameweek_picks"
    __table_args__ = (UniqueConstraint("snapshot_id", "squad_position", name="uq_fpl_snapshot_pick_position"), UniqueConstraint("snapshot_id", "player_id", name="uq_fpl_snapshot_pick_player"), Index("ix_fpl_manager_picks_player", "player_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("fpl_manager_gameweek_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False)
    squad_position: Mapped[int] = mapped_column(Integer, nullable=False)
    multiplier: Mapped[int] = mapped_column(Integer, nullable=False)
    is_captain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_vice_captain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    purchase_price: Mapped[int | None] = mapped_column(Integer)
    selling_price: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    snapshot: Mapped[FPLManagerGameweekSnapshot] = relationship(back_populates="picks")
    player: Mapped[Player] = relationship(back_populates="squad_picks")


class FPLMatchCenterSnapshot(Base):
    __tablename__ = "fpl_match_center_snapshots"
    __table_args__ = (UniqueConstraint("gameweek", name="uq_fpl_match_center_snapshot_gameweek"), CheckConstraint("status IN ('available', 'partial', 'unavailable')", name="ck_fpl_match_center_snapshot_status"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gameweek: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    fixtures: Mapped[list["FPLMatchCenterFixtureState"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    players: Mapped[list["FPLMatchCenterPlayerState"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    managers: Mapped[list["FPLMatchCenterManagerState"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")


class FPLMatchCenterFixtureState(Base):
    __tablename__ = "fpl_match_center_fixture_states"
    __table_args__ = (UniqueConstraint("snapshot_id", "official_fixture_id", name="uq_fpl_match_center_fixture"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("fpl_match_center_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    official_fixture_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    home_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    away_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kickoff_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started: Mapped[bool] = mapped_column(Boolean, nullable=False)
    finished: Mapped[bool] = mapped_column(Boolean, nullable=False)
    finished_provisional: Mapped[bool | None] = mapped_column(Boolean)
    fixture_minutes: Mapped[int | None] = mapped_column(Integer)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    snapshot: Mapped[FPLMatchCenterSnapshot] = relationship(back_populates="fixtures")


class FPLMatchCenterPlayerState(Base):
    __tablename__ = "fpl_match_center_player_states"
    __table_args__ = (UniqueConstraint("snapshot_id", "player_id", name="uq_fpl_match_center_player"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("fpl_match_center_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False, index=True)
    club_id: Mapped[int | None] = mapped_column(Integer, index=True)
    position: Mapped[str | None] = mapped_column(String(8))
    total_points: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes: Mapped[int | None] = mapped_column(Integer); goals_scored: Mapped[int | None] = mapped_column(Integer); assists: Mapped[int | None] = mapped_column(Integer); clean_sheets: Mapped[int | None] = mapped_column(Integer); goals_conceded: Mapped[int | None] = mapped_column(Integer); bonus: Mapped[int | None] = mapped_column(Integer); bps: Mapped[int | None] = mapped_column(Integer)
    expected_goals: Mapped[float | None] = mapped_column(Float); expected_assists: Mapped[float | None] = mapped_column(Float); expected_goal_involvements: Mapped[float | None] = mapped_column(Float); expected_goals_conceded: Mapped[float | None] = mapped_column(Float)
    raw_stats: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now()); updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    snapshot: Mapped[FPLMatchCenterSnapshot] = relationship(back_populates="players")
    player: Mapped[Player] = relationship()


class FPLMatchCenterManagerState(Base):
    __tablename__ = "fpl_match_center_manager_states"
    __table_args__ = (UniqueConstraint("snapshot_id", "manager_id", name="uq_fpl_match_center_manager"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("fpl_match_center_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    manager_id: Mapped[int] = mapped_column(ForeignKey("fpl_managers.id", ondelete="RESTRICT"), nullable=False, index=True)
    squad_snapshot_id: Mapped[int] = mapped_column(ForeignKey("fpl_manager_gameweek_snapshots.id", ondelete="RESTRICT"), nullable=False)
    official_event_points: Mapped[int | None] = mapped_column(Integer); provisional_live_points: Mapped[int | None] = mapped_column(Integer); active_chip: Mapped[str | None] = mapped_column(String(64)); automatic_subs: Mapped[list | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now()); updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    snapshot: Mapped[FPLMatchCenterSnapshot] = relationship(back_populates="managers")
    manager: Mapped[FPLManager] = relationship(); squad_snapshot: Mapped[FPLManagerGameweekSnapshot] = relationship()


class DecisionSessionStatus:
    DRAFT = "draft"
    FINALIZED = "finalized"


class DecisionOptionType:
    HOLD = "hold"
    TRANSFER = "transfer"


class DecisionSession(Base):
    """A user-owned, frozen planning surface; it never alters official FPL state."""

    __tablename__ = "decision_sessions"
    __table_args__ = (
        UniqueConstraint("manager_id", "snapshot_id", name="uq_decision_session_manager_snapshot"),
        CheckConstraint("status IN ('draft', 'finalized')", name="ck_decision_sessions_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    manager_id: Mapped[int] = mapped_column(ForeignKey("fpl_managers.id", ondelete="RESTRICT"), nullable=False, index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("fpl_manager_gameweek_snapshots.id", ondelete="RESTRICT"), nullable=False, index=True)
    gameweek: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    frozen_bank: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DecisionSessionStatus.DRAFT)
    selected_option_id: Mapped[str | None] = mapped_column(String(36))
    finalized_option_id: Mapped[str | None] = mapped_column(String(36))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    manager: Mapped[FPLManager] = relationship()
    snapshot: Mapped[FPLManagerGameweekSnapshot] = relationship()
    options: Mapped[list["DecisionOption"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    frozen_picks: Mapped[list["DecisionSessionPick"]] = relationship(back_populates="session", cascade="all, delete-orphan", order_by="DecisionSessionPick.squad_position")


class DecisionSessionPick(Base):
    __tablename__ = "decision_session_picks"
    __table_args__ = (UniqueConstraint("session_id", "player_id", name="uq_decision_session_pick_player"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False)
    squad_position: Mapped[int] = mapped_column(Integer, nullable=False)
    selling_price: Mapped[int | None] = mapped_column(Integer)
    session: Mapped[DecisionSession] = relationship(back_populates="frozen_picks")
    player: Mapped[Player] = relationship()


class DecisionOption(Base):
    __tablename__ = "decision_options"
    __table_args__ = (CheckConstraint("option_type IN ('hold', 'transfer')", name="ck_decision_options_type"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    option_type: Mapped[str] = mapped_column(String(16), nullable=False)
    is_legal: Mapped[bool] = mapped_column(Boolean, nullable=False)
    validation_errors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    budget_available: Mapped[int | None] = mapped_column(Integer)
    budget_required: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    session: Mapped[DecisionSession] = relationship(back_populates="options")
    movements: Mapped[list["DecisionMovement"]] = relationship(back_populates="option", cascade="all, delete-orphan", order_by="DecisionMovement.sequence")


class DecisionMovement(Base):
    __tablename__ = "decision_movements"
    __table_args__ = (UniqueConstraint("option_id", "sequence", name="uq_decision_movement_sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    option_id: Mapped[str] = mapped_column(ForeignKey("decision_options.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    outgoing_player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False)
    incoming_player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False)
    outgoing_synthesis_id: Mapped[str | None] = mapped_column(ForeignKey("research_player_syntheses.id", ondelete="RESTRICT"))
    incoming_synthesis_id: Mapped[str | None] = mapped_column(ForeignKey("research_player_syntheses.id", ondelete="RESTRICT"))
    option: Mapped[DecisionOption] = relationship(back_populates="movements")
    outgoing_player: Mapped[Player] = relationship(foreign_keys=[outgoing_player_id])
    incoming_player: Mapped[Player] = relationship(foreign_keys=[incoming_player_id])


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
            "'team_selection', 'transfer', 'tactical_role', 'fixture', 'manager_comment', 'freshness', 'other')",
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


class ResearchEvidenceBundle(Base):
    __tablename__ = "research_evidence_bundles"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'assessed')", name="ck_research_evidence_bundles_status"),
        Index("ix_research_evidence_bundles_player_dimension_cutoff", "player_id", "dimension", "research_cutoff"),
        Index("ix_research_evidence_bundles_thread_situation", "thread_id", "situation_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False)
    situation_id: Mapped[str | None] = mapped_column(ForeignKey("research_situations.id", ondelete="SET NULL"), nullable=True)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    research_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ResearchEvidenceBundleStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    members: Mapped[list["ResearchEvidenceBundleMember"]] = relationship(back_populates="bundle", cascade="all, delete-orphan")
    assessment: Mapped["ResearchDimensionAssessment | None"] = relationship(back_populates="bundle", cascade="all, delete-orphan", uselist=False)


class ResearchEvidenceBundleMember(Base):
    __tablename__ = "research_evidence_bundle_members"
    __table_args__ = (UniqueConstraint("bundle_id", "evidence_id", name="uq_research_evidence_bundle_member"), CheckConstraint("role IN ('current', 'superseded', 'contextual')", name="ck_research_evidence_bundle_members_role"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    bundle_id: Mapped[str] = mapped_column(ForeignKey("research_evidence_bundles.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("research_evidence.id", ondelete="RESTRICT"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="current")
    bundle: Mapped[ResearchEvidenceBundle] = relationship(back_populates="members")
    evidence: Mapped[ResearchEvidence] = relationship()


class ResearchDimensionAssessment(Base):
    __tablename__ = "research_dimension_assessments"
    __table_args__ = (CheckConstraint("bundle_strength IN ('strong', 'adequate', 'thin', 'unresolved')", name="ck_research_dimension_assessments_strength"), CheckConstraint("confidence IN ('high', 'medium', 'low', 'unresolved')", name="ck_research_dimension_assessments_confidence"), Index("ix_research_dimension_assessments_player_dimension_cutoff", "player_id", "dimension", "research_cutoff"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    bundle_id: Mapped[str] = mapped_column(ForeignKey("research_evidence_bundles.id", ondelete="CASCADE"), nullable=False, unique=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False)
    situation_id: Mapped[str | None] = mapped_column(ForeignKey("research_situations.id", ondelete="SET NULL"), nullable=True)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    research_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bundle_strength: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    thesis: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    contradiction_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_information: Mapped[list | None] = mapped_column(JSON, nullable=True)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    distinct_source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    independent_source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    contradiction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    superseded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    bundle: Mapped[ResearchEvidenceBundle] = relationship(back_populates="assessment")


research_deep_run_assessments = Table("research_deep_run_assessments", Base.metadata, Column("deep_run_id", String(36), ForeignKey("research_deep_runs.id", ondelete="CASCADE"), primary_key=True), Column("dimension_assessment_id", String(36), ForeignKey("research_dimension_assessments.id", ondelete="RESTRICT"), primary_key=True))
research_deep_run_quality_runs = Table("research_deep_run_quality_runs", Base.metadata, Column("deep_run_id", String(36), ForeignKey("research_deep_runs.id", ondelete="CASCADE"), primary_key=True), Column("quality_run_id", String(36), ForeignKey("research_quality_runs.id", ondelete="RESTRICT"), primary_key=True))
research_blind_spot_finding_evidence = Table("research_blind_spot_finding_evidence", Base.metadata, Column("finding_id", String(36), ForeignKey("research_blind_spot_findings.id", ondelete="CASCADE"), primary_key=True), Column("evidence_id", String(36), ForeignKey("research_evidence.id", ondelete="RESTRICT"), primary_key=True))
research_cycle_player_triggers = Table("research_cycle_player_triggers", Base.metadata, Column("cycle_player_id", String(36), ForeignKey("research_cycle_players.id", ondelete="CASCADE"), primary_key=True), Column("research_trigger_id", String(36), ForeignKey("player_research_triggers.id", ondelete="RESTRICT"), primary_key=True))

class ResearchCycleStatus:
    PENDING="pending"; MONITORING="monitoring"; PREPARED="prepared"; EXECUTING="executing"; COMPLETED="completed"; PARTIAL="partial"; FAILED="failed"
class ResearchCyclePlayerState:
    MONITORED="monitored"; TRIGGERED="triggered"; SELECTED="selected"; DEFERRED="deferred"; RESEARCHING="researching"; RESEARCHED="researched"; FAILED="failed"

class ResearchCycle(Base):
    __tablename__="research_cycles"
    __table_args__=(CheckConstraint("status IN ('pending','monitoring','prepared','executing','completed','partial','failed')",name="ck_research_cycles_status"),CheckConstraint("max_deep_runs >= 1 AND max_deep_runs <= 15",name="ck_research_cycles_budget"),Index("ix_research_cycles_gameweek_status","gameweek","status"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid4())); gameweek: Mapped[int]=mapped_column(Integer,nullable=False,index=True); research_cutoff: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False); status: Mapped[str]=mapped_column(String(16),nullable=False,default=ResearchCycleStatus.PENDING,index=True); max_deep_runs: Mapped[int]=mapped_column(Integer,nullable=False,default=15); orchestration_version: Mapped[str]=mapped_column(String(64),nullable=False); started_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); prepared_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); completed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); failure_reason: Mapped[str|None]=mapped_column(Text); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now()); updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now(),onupdate=func.now())
    players: Mapped[list["ResearchCyclePlayer"]]=relationship(back_populates="cycle",cascade="all, delete-orphan")

class ResearchCyclePlayer(Base):
    __tablename__="research_cycle_players"
    __table_args__=(UniqueConstraint("cycle_id","player_id",name="uq_research_cycle_player"),CheckConstraint("state IN ('monitored','triggered','selected','deferred','researching','researched','failed')",name="ck_research_cycle_players_state"),Index("ix_research_cycle_players_player_state","player_id","state"),Index("ix_research_cycle_players_cycle_selected","cycle_id","selected_for_deep_research"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid4())); cycle_id: Mapped[str]=mapped_column(ForeignKey("research_cycles.id",ondelete="CASCADE"),nullable=False); player_id: Mapped[int]=mapped_column(ForeignKey("players.id",ondelete="RESTRICT"),nullable=False); watchlist_entry_id: Mapped[str|None]=mapped_column(ForeignKey("watchlist_entries.id",ondelete="SET NULL")); pulse_id: Mapped[str|None]=mapped_column(ForeignKey("player_gameweek_pulses.id",ondelete="SET NULL")); state: Mapped[str]=mapped_column(String(16),nullable=False,default=ResearchCyclePlayerState.MONITORED); selected_for_deep_research: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False); queue_rank: Mapped[int|None]=mapped_column(Integer); selection_reason: Mapped[list|None]=mapped_column(JSON); deep_run_id: Mapped[str|None]=mapped_column(ForeignKey("research_deep_runs.id",ondelete="SET NULL")); failure_reason: Mapped[str|None]=mapped_column(Text); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now()); updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now(),onupdate=func.now())
    cycle: Mapped[ResearchCycle]=relationship(back_populates="players"); player: Mapped[Player]=relationship(); pulse: Mapped[PlayerGameweekPulse|None]=relationship(); deep_run: Mapped["ResearchDeepRun|None"]=relationship(); triggers: Mapped[list[PlayerResearchTrigger]]=relationship(secondary=research_cycle_player_triggers)


class ResearchDeepRun(Base):
    __tablename__ = "research_deep_runs"
    __table_args__ = (CheckConstraint("status IN ('pending', 'running', 'research_complete', 'blind_spot_complete', 'completed', 'partial', 'failed')", name="ck_research_deep_runs_status"), Index("ix_research_deep_runs_player_cutoff", "player_id", "research_cutoff"), Index("ix_research_deep_runs_thread_id", "thread_id"), Index("ix_research_deep_runs_status", "status"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False)
    situation_id: Mapped[str | None] = mapped_column(ForeignKey("research_situations.id", ondelete="SET NULL"))
    trigger_id: Mapped[str | None] = mapped_column(ForeignKey("player_research_triggers.id", ondelete="SET NULL"))
    research_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ResearchDeepRunStatus.PENDING)
    target_dimensions: Mapped[list] = mapped_column(JSON, nullable=False)
    discovery_execution_id: Mapped[str | None] = mapped_column(ForeignKey("research_discovery_executions.id", ondelete="SET NULL"))
    orchestration_version: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now()); updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    assessments: Mapped[list[ResearchDimensionAssessment]] = relationship(secondary=research_deep_run_assessments)
    quality_runs: Mapped[list[ResearchQualityRun]] = relationship(secondary=research_deep_run_quality_runs)
    blind_spots: Mapped[list["ResearchBlindSpotFinding"]] = relationship(back_populates="deep_run", cascade="all, delete-orphan")
    synthesis: Mapped["ResearchPlayerSynthesis | None"] = relationship(back_populates="deep_run", uselist=False, cascade="all, delete-orphan")


class ResearchBlindSpotFinding(Base):
    __tablename__ = "research_blind_spot_findings"
    __table_args__ = (CheckConstraint("status IN ('open', 'researched', 'unresolved')", name="ck_research_blind_spot_findings_status"), Index("ix_research_blind_spot_findings_run_status", "deep_run_id", "status"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4())); deep_run_id: Mapped[str] = mapped_column(ForeignKey("research_deep_runs.id", ondelete="CASCADE"), nullable=False)
    dimension: Mapped[str | None] = mapped_column(String(64)); category: Mapped[str] = mapped_column(String(64), nullable=False); question: Mapped[str] = mapped_column(Text, nullable=False); why_it_matters: Mapped[str] = mapped_column(Text, nullable=False); status: Mapped[str] = mapped_column(String(16), nullable=False, default="open"); resolution_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now()); updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deep_run: Mapped[ResearchDeepRun] = relationship(back_populates="blind_spots"); evidence: Mapped[list[ResearchEvidence]] = relationship(secondary=research_blind_spot_finding_evidence)


class ResearchPlayerSynthesis(Base):
    __tablename__ = "research_player_syntheses"
    __table_args__ = (CheckConstraint("overall_research_state IN ('clear', 'mixed', 'thin', 'unresolved')", name="ck_research_player_syntheses_state"), Index("ix_research_player_syntheses_player_cutoff", "player_id", "research_cutoff"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4())); deep_run_id: Mapped[str] = mapped_column(ForeignKey("research_deep_runs.id", ondelete="CASCADE"), nullable=False, unique=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("research_threads.id", ondelete="CASCADE"), nullable=False); player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False); situation_id: Mapped[str | None] = mapped_column(ForeignKey("research_situations.id", ondelete="SET NULL")); research_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    overall_research_state: Mapped[str] = mapped_column(String(16), nullable=False); executive_summary: Mapped[str] = mapped_column(Text, nullable=False); dimension_summaries: Mapped[list] = mapped_column(JSON, nullable=False); key_strengths: Mapped[list] = mapped_column(JSON, nullable=False); key_risks: Mapped[list] = mapped_column(JSON, nullable=False); contradictions: Mapped[list] = mapped_column(JSON, nullable=False); missing_information: Mapped[list] = mapped_column(JSON, nullable=False); future_monitoring: Mapped[list] = mapped_column(JSON, nullable=False); prompt_version: Mapped[str] = mapped_column(String(64), nullable=False); model_metadata: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now()); updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deep_run: Mapped[ResearchDeepRun] = relationship(back_populates="synthesis")


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


class ResearchQueueStatus:
    QUEUED = "queued"; RUNNING = "running"; COMPLETED = "completed"; FAILED = "failed"; REMOVED = "removed"; SNOOZED = "snoozed"
    ACTIVE = frozenset({QUEUED, RUNNING, SNOOZED})

class ResearchQueueSource:
    USER = "user"; DECISION_CENTER = "decision_center"; ACCEPTED_SIGNAL = "accepted_signal"; RESEARCH_MONITORING = "research_monitoring"

class ResearchQueueItem(Base):
    __tablename__ = "research_queue_items"
    __table_args__ = (CheckConstraint("status IN ('queued','running','completed','failed','removed','snoozed')", name="ck_research_queue_status"), CheckConstraint("source IN ('user','decision_center','accepted_signal','research_monitoring')", name="ck_research_queue_source"), Index("ix_research_queue_active_order", "status", "queue_order"), Index("ix_research_queue_player_status", "player_id", "status"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ResearchQueueStatus.QUEUED, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False); reason: Mapped[str | None] = mapped_column(Text)
    queue_order: Mapped[int] = mapped_column(Integer, nullable=False, index=True); requested_gameweek: Mapped[int | None] = mapped_column(Integer); snoozed_until_gameweek: Mapped[int | None] = mapped_column(Integer)
    source_context: Mapped[dict | None] = mapped_column(JSON); trigger_id: Mapped[str | None] = mapped_column(ForeignKey("player_research_triggers.id", ondelete="SET NULL")); research_situation_id: Mapped[str | None] = mapped_column(ForeignKey("research_situations.id", ondelete="SET NULL")); cycle_id: Mapped[str | None] = mapped_column(ForeignKey("research_cycles.id", ondelete="SET NULL")); cycle_player_id: Mapped[str | None] = mapped_column(ForeignKey("research_cycle_players.id", ondelete="SET NULL")); deep_run_id: Mapped[str | None] = mapped_column(ForeignKey("research_deep_runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now()); updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()); queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    player: Mapped[Player] = relationship()
