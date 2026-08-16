from __future__ import annotations

from pathlib import Path
import subprocess

from .app_server import executable_command
from .config import RunnerConfig
from .models import AppServerError, ConfigurationError, Incident
from .workspace import HostGitLifecycle, Workspace


class MaintenancePolicy:
    """Deterministic boundary for the optional, separately cloned runner-repair lane."""

    ALLOWED_PREFIXES = ("tools/symphony_runner/", "scripts/", "tooling/", "backend/tests/symphony/")
    ALLOWED_FILES = {"WORKFLOW.md"}

    @classmethod
    def validate_paths(cls, paths: list[str]) -> None:
        for raw in paths:
            name = raw.replace("\\", "/").lstrip("./")
            if name in cls.ALLOWED_FILES or any(name.startswith(prefix) for prefix in cls.ALLOWED_PREFIXES):
                continue
            raise ConfigurationError(f"maintenance repair attempted out-of-scope path: {raw}")

    @staticmethod
    def workspace(root: Path, incident: Incident) -> Path:
        path = (root / "maintenance" / incident.incident_id).resolve()
        maintenance_root = (root / "maintenance").resolve()
        try: path.relative_to(maintenance_root)
        except ValueError as exc: raise ConfigurationError("maintenance workspace escaped durable root") from exc
        if Path(incident.workspace).resolve() == path:
            raise ConfigurationError("maintenance and product workspaces must be separate")
        return path

    @staticmethod
    def eligible(incident: Incident, confidence: str) -> bool:
        return incident.failure_class in {"INFRASTRUCTURE", "ENVIRONMENT", "APP_SERVER_PROTOCOL", "APP_SERVER_CONTEXT"} and confidence == "high"

    @classmethod
    def run_cli_repair(cls, config: RunnerConfig, workspace: Workspace, prompt: str,
                       model: str, git: HostGitLifecycle) -> str:
        """Bounded App-Server-independent repair; host still verifies and owns Git."""
        command = executable_command(["codex", "exec", "--ephemeral", "--ignore-user-config",
            "-c", 'approval_policy="never"', "--sandbox", "workspace-write", "--model", model,
            "-C", str(workspace.path), "-"])
        result = subprocess.run(command, input=prompt, text=True, encoding="utf-8", errors="replace", capture_output=True,
            env=config.sanitized_child_environment(), timeout=config.rescue_timeout_ms / 1000, check=False)
        if result.returncode:
            raise AppServerError(f"bounded maintenance rescue failed with exit code {result.returncode}: {config.redact(result.stderr)[-500:]}")
        changed = git.changed_files(workspace)
        cls.validate_paths(changed)
        git.validate_changes(workspace, changed)
        # Verification is deterministic and host-side. No commit/push occurs here.
        git.verify(workspace)
        return config.redact(result.stdout)[-2000:]
