from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


class RunnerError(RuntimeError):
    retryable = False


class RetryableError(RunnerError):
    retryable = True


class AppServerError(RetryableError):
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
