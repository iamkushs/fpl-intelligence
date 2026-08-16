from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ConfigurationError, RunnerPaths

ENV_REF = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")
APP_SERVER_MAX_MESSAGE_BYTES = 16 * 1024 * 1024


def resolve_env(value: Any, *, secrets: set[str] | None = None) -> Any:
    if isinstance(value, str) and (match := ENV_REF.fullmatch(value)):
        name = match.group(1)
        if secrets is not None:
            secrets.add(name)
        return os.environ.get(name, "")
    if isinstance(value, dict):
        return {key: resolve_env(item, secrets=secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_env(item, secrets=secrets) for item in value]
    return value


def default_data_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "FPLSymphony"


@dataclass(slots=True)
class RunnerConfig:
    repository: str
    tracker_token: str
    required_labels: tuple[str, ...] = ("symphony",)
    active_states: tuple[str, ...] = ("open",)
    terminal_states: tuple[str, ...] = ("closed",)
    poll_interval_ms: int = 30_000
    max_concurrent_agents: int = 1
    max_turns: int = 20
    max_retry_backoff_ms: int = 300_000
    codex_command: tuple[str, ...] = ("codex", "app-server")
    approval_policy: str = "never"
    thread_sandbox: str = "workspace-write"
    sandbox_policy: dict[str, Any] = field(default_factory=lambda: {"type": "workspaceWrite", "networkAccess": True})
    turn_timeout_ms: int = 3_600_000
    read_timeout_ms: int = 5_000
    stall_timeout_ms: int = 300_000
    app_server_max_message_bytes: int = APP_SERVER_MAX_MESSAGE_BYTES
    hooks: dict[str, Any] = field(default_factory=dict)
    prompt: str = ""
    secret_environment_names: set[str] = field(default_factory=set)
    paths: RunnerPaths | None = None
    status_host: str = "127.0.0.1"
    status_port: int = 4000
    model_policy_path: Path = Path("tooling/symphony-models.json")
    max_incident_repairs: int = 3
    max_issue_repairs: int = 9
    reviewer_routes: tuple[str, ...] = ("5.5", "terra", "sol")
    rescue_timeout_ms: int = 900_000

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], prompt: str, workflow_path: Path) -> RunnerConfig:
        secret_names: set[str] = {"GITHUB_TOKEN", "GH_TOKEN", "LINEAR_API_KEY"}
        resolved = resolve_env(raw, secrets=secret_names)
        tracker = resolved.get("tracker", {})
        if tracker.get("kind") != "github":
            raise ConfigurationError("This runner supports tracker.kind=github only")
        provider = tracker.get("provider", {})
        repository = str(provider.get("repo", "")).strip()
        if not repository or "/" not in repository:
            raise ConfigurationError("tracker.provider.repo must be OWNER/REPO")
        token = str(provider.get("token", ""))
        workspace_value = resolved.get("workspace", {}).get("root")
        data_root = default_data_root().resolve()
        workspace_root = Path(workspace_value).expanduser() if workspace_value else data_root / "workspaces"
        if not workspace_root.is_absolute():
            workspace_root = (workflow_path.parent / workspace_root).resolve()
        else:
            workspace_root = workspace_root.resolve()
        paths = RunnerPaths(data_root, workspace_root, data_root / "logs", data_root / "state.json", data_root / "runner.lock")
        codex = resolved.get("codex", {})
        command = str(codex.get("command", "codex app-server")).split()
        if not command or command[0].lower() != "codex":
            raise ConfigurationError("codex.command must launch codex app-server")
        agent = resolved.get("agent", {})
        polling = resolved.get("polling", {})
        server = resolved.get("server", {})
        recovery = resolved.get("recovery", {})
        config = cls(
            repository=repository,
            tracker_token=token,
            required_labels=tuple(str(v).strip().lower() for v in tracker.get("required_labels", ["symphony"])),
            active_states=tuple(str(v).strip().lower() for v in tracker.get("active_states", ["open"])),
            terminal_states=tuple(str(v).strip().lower() for v in tracker.get("terminal_states", ["closed"])),
            poll_interval_ms=int(polling.get("interval_ms", 30_000)),
            max_concurrent_agents=int(agent.get("max_concurrent_agents", 1)),
            max_turns=int(agent.get("max_turns", 20)),
            max_retry_backoff_ms=int(agent.get("max_retry_backoff_ms", 300_000)),
            codex_command=tuple(command), approval_policy=str(codex.get("approval_policy", "never")),
            thread_sandbox=str(codex.get("thread_sandbox", "workspace-write")),
            sandbox_policy=dict(codex.get("turn_sandbox_policy", {"type": "workspaceWrite", "networkAccess": True})),
            turn_timeout_ms=int(codex.get("turn_timeout_ms", 3_600_000)),
            read_timeout_ms=int(codex.get("read_timeout_ms", 5_000)),
            stall_timeout_ms=int(codex.get("stall_timeout_ms", 300_000)),
            app_server_max_message_bytes=int(codex.get("max_message_bytes", APP_SERVER_MAX_MESSAGE_BYTES)),
            hooks=dict(resolved.get("hooks", {})), prompt=prompt, secret_environment_names=secret_names, paths=paths,
            status_host=str(server.get("host", "127.0.0.1")), status_port=int(server.get("port", 4000)),
            model_policy_path=(workflow_path.parent / str(resolved.get("models", {}).get("policy", "tooling/symphony-models.json"))).resolve(),
            max_incident_repairs=int(recovery.get("max_incident_repairs", 3)),
            max_issue_repairs=int(recovery.get("max_issue_repairs", 9)),
            reviewer_routes=tuple(recovery.get("reviewer_routes", ["5.5", "terra", "sol"])),
            rescue_timeout_ms=int(recovery.get("rescue_timeout_ms", 900_000)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.max_concurrent_agents <= 0 or self.max_turns <= 0 or self.poll_interval_ms <= 0:
            raise ConfigurationError("polling and agent limits must be positive")
        if self.app_server_max_message_bytes <= 0:
            raise ConfigurationError("codex.max_message_bytes must be positive")
        if self.max_incident_repairs <= 0 or self.max_issue_repairs <= 0:
            raise ConfigurationError("recovery budgets must be positive")
        if not self.reviewer_routes or any(route not in {"5.5", "terra", "sol"} for route in self.reviewer_routes):
            raise ConfigurationError("recovery reviewer routes must use 5.5, terra, or sol")
        if self.thread_sandbox != "workspace-write" or self.approval_policy != "never":
            raise ConfigurationError("Windows runner requires approval_policy=never and workspace-write sandbox")
        if self.status_host != "127.0.0.1":
            raise ConfigurationError("status server must bind to 127.0.0.1")

    def sanitized_child_environment(self) -> dict[str, str]:
        blocked = {name.upper() for name in self.secret_environment_names}
        blocked.update({"GITHUB_TOKEN", "GH_TOKEN", "LINEAR_API_KEY", "GITHUB_PAT"})
        return {key: value for key, value in os.environ.items() if key.upper() not in blocked and not key.upper().endswith("_GITHUB_TOKEN")}

    def redact(self, value: str) -> str:
        result = value
        candidates = [self.tracker_token, *(os.environ.get(name, "") for name in self.secret_environment_names)]
        for secret in candidates:
            if secret:
                result = result.replace(secret, "[REDACTED]")
        return result
