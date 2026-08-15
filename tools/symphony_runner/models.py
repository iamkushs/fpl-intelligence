from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum
from typing import Any
import hashlib
import re


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Issue:
    number: int
    title: str
    body: str
    labels: tuple[str, ...]
    state: str
    url: str
    created_at: str | None = None
    updated_at: str | None = None
    is_pull_request: bool = False

    @property
    def identifier(self) -> str:
        return f"GH-{self.number}"


@dataclass(slots=True)
class RunRecord:
    issue_number: int
    workspace: str
    branch: str
    thread_id: str | None = None
    turn_id: str | None = None
    attempt: int = 0
    status: str = "pending"
    last_issue_state: str = "open"
    retry_at: float | None = None
    last_error: str | None = None
    last_activity: str = field(default_factory=utc_now)
    requested_model_route: str | None = None
    resolved_model_id: str | None = None
    reasoning_effort: str | None = None
    routing_reason: str | None = None
    escalation_level: int = 0
    productive_failure_count: int = 0
    previous_routes: list[str] = field(default_factory=list)
    unhealthy_thread_ids: list[str] = field(default_factory=list)
    phase: str = "queued"
    incidents: list[dict[str, Any]] = field(default_factory=list)
    current_incident_id: str | None = None
    reviewer_thread_id: str | None = None
    reviewer_route: str | None = None
    reviewer_model_id: str | None = None
    reviewer_effort: str | None = None
    reviewer_verdict: str | None = None
    review_iterations: int = 0
    total_repair_attempts: int = 0
    thread_rotations: int = 0
    parked_for_maintenance: bool = False
    coding_completion_summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunRecord:
        return cls(**value)


@dataclass(slots=True)
class RunnerPaths:
    root: Path
    workspaces: Path
    logs: Path
    state_file: Path
    lock_file: Path


@dataclass(slots=True)
class TurnResult:
    thread_id: str
    turn_id: str
    status: str
    events: int
    error: str | None = None
    output: str | None = None


class IssuePhase(str, Enum):
    QUEUED = "queued"
    CODING = "coding"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    INCIDENT_REVIEW = "incident_review"
    RECOVERY_PLANNED = "recovery_planned"
    HOST_HANDOFF = "host_handoff"
    BLOCKED = "blocked"
    EXTERNAL_WAIT = "external_wait"


class FailureClass(str, Enum):
    PRODUCT_IMPLEMENTATION = "PRODUCT_IMPLEMENTATION"
    PRODUCT_TEST_FAILURE = "PRODUCT_TEST_FAILURE"
    REQUIREMENTS_AMBIGUITY = "REQUIREMENTS_AMBIGUITY"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    APP_SERVER_PROTOCOL = "APP_SERVER_PROTOCOL"
    APP_SERVER_CONTEXT = "APP_SERVER_CONTEXT"
    GIT_HOST = "GIT_HOST"
    NETWORK = "NETWORK"
    DEPENDENCY = "DEPENDENCY"
    ENVIRONMENT = "ENVIRONMENT"
    MODEL_FAILURE = "MODEL_FAILURE"
    EXTERNAL_SERVICE = "EXTERNAL_SERVICE"
    UNKNOWN = "UNKNOWN"


class ReviewerVerdict(str, Enum):
    PASS = "PASS"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    RETRY = "RETRY"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(slots=True)
class ReviewerDecision:
    verdict: ReviewerVerdict
    failure_class: FailureClass
    root_cause: str
    confidence: Confidence
    productive_failure: bool
    preserve_workspace: bool
    preserve_thread: bool
    rotate_thread: bool
    escalate_coding_model: bool
    repair_scope: list[str]
    plan: list[str]
    verification: list[str]
    stop_conditions: list[str]
    notes: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReviewerDecision":
        required = {"verdict", "failure_class", "root_cause", "confidence", "productive_failure",
            "preserve_workspace", "preserve_thread", "rotate_thread", "escalate_coding_model",
            "repair_scope", "plan", "verification", "stop_conditions", "notes"}
        if not isinstance(value, dict) or set(value) != required:
            raise AppServerProtocolError(f"reviewer response fields do not match schema: {sorted(set(value) if isinstance(value, dict) else [])}")
        for name in ("productive_failure", "preserve_workspace", "preserve_thread", "rotate_thread", "escalate_coding_model"):
            if type(value[name]) is not bool: raise AppServerProtocolError(f"reviewer field {name} must be boolean")
        for name in ("repair_scope", "plan", "verification", "stop_conditions"):
            if not isinstance(value[name], list) or not all(isinstance(v, str) for v in value[name]):
                raise AppServerProtocolError(f"reviewer field {name} must be an array of strings")
        if not isinstance(value["root_cause"], str) or not isinstance(value["notes"], str):
            raise AppServerProtocolError("reviewer root_cause and notes must be strings")
        try:
            return cls(ReviewerVerdict(value["verdict"]), FailureClass(value["failure_class"]),
                value["root_cause"], Confidence(value["confidence"]), value["productive_failure"],
                value["preserve_workspace"], value["preserve_thread"], value["rotate_thread"],
                value["escalate_coding_model"], value["repair_scope"], value["plan"],
                value["verification"], value["stop_conditions"], value["notes"])
        except ValueError as exc: raise AppServerProtocolError(f"reviewer response enum is invalid: {exc}") from exc


@dataclass(slots=True)
class Incident:
    incident_id: str
    issue_number: int
    workspace: str
    branch: str
    original_thread_id: str | None
    current_thread_id: str | None
    turn_id: str | None
    failure_signature: str
    failure_class: str
    status: str
    first_seen_at: str
    last_seen_at: str
    occurrence_count: int = 1
    coding_route: str | None = None
    coding_model: str | None = None
    coding_effort: str | None = None
    reviewer_route: str | None = None
    reviewer_model: str | None = None
    reviewer_effort: str | None = None
    productive_failure: bool = False
    failed_command: str | None = None
    exception_type: str | None = None
    safe_exception_message: str | None = None
    safe_traceback: str | None = None
    runner_subsystem: str | None = None
    previous_reviewer_diagnosis: str | None = None
    reviewer_confidence: str | None = None
    reviewer_plan: list[str] = field(default_factory=list)
    recovery_action: str | None = None
    repair_attempts: int = 0
    previous_incident_id: str | None = None
    resolved_at: str | None = None
    resolution_commit: str | None = None

    def to_dict(self) -> dict[str, Any]: return asdict(self)


def failure_signature(failure_class: FailureClass, exception_type: str, message: str,
                      subsystem: str, origin: str | None = None, failed_step: str | None = None) -> str:
    normalized = message.lower()
    normalized = re.sub(r"\b(?:turn|thread|request|incident)[-_ ]?[a-z0-9.:-]+", "<id>", normalized)
    normalized = re.sub(r"\b\d{4}-\d\d-\d\dt[^ ]+|\b\d{6,}\b", "<volatile>", normalized)
    normalized = re.sub(r"[a-f0-9]{12,}", "<id>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()[:500]
    material = "|".join((failure_class.value, exception_type, normalized, subsystem, origin or "", failed_step or ""))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


class RunnerError(RuntimeError):
    retryable = False


class RetryableError(RunnerError):
    retryable = True


class AppServerError(RetryableError):
    pass


class AppServerProtocolError(AppServerError):
    """A schema/state anomaly which makes the current thread unsafe to resume."""

    rotate_thread = True


class AppServerThreadUnavailable(AppServerProtocolError):
    """The server explicitly rejected the persisted thread identifier."""


class AppServerTurnFailed(AppServerError):
    pass


class AppServerTurnInterrupted(AppServerError):
    pass


class AppServerTimeout(AppServerError):
    pass


class AppServerMessageTooLarge(AppServerError):
    def __init__(self, limit: int, observed: int):
        self.limit = limit
        self.observed = observed
        limit_mib = limit / (1024 * 1024)
        display_limit = f"{limit_mib:g} MiB" if limit_mib >= 1 else f"{limit} byte{'s' if limit != 1 else ''}"
        super().__init__(f"Codex App Server message exceeded configured {display_limit} limit")


class ConfigurationError(RunnerError):
    pass
