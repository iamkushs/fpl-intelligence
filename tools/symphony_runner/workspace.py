from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import ConfigurationError, Issue, RetryableError

Run = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(slots=True)
class Workspace:
    path: Path
    branch: str
    created: bool


class WorkspaceManager:
    def __init__(self, root: Path, repository: str, run: Run = subprocess.run):
        self.root, self.repository, self._run = root, repository, run
        self.clone_url = f"https://github.com/{repository}.git"

    @staticmethod
    def branch_for(issue: Issue) -> str:
        return f"symphony/gh-{issue.number}"

    def path_for(self, issue: Issue) -> Path:
        return self.root / issue.identifier

    def _git(self, cwd: Path | None, *args: str) -> str:
        result = self._run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RetryableError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()[:500]}")
        return result.stdout.strip()

    def prepare(self, issue: Issue) -> Workspace:
        path, branch = self.path_for(issue), self.branch_for(issue)
        created = not (path / ".git").is_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        if created:
            if path.exists() and any(path.iterdir()):
                raise ConfigurationError(f"Refusing to clone into non-empty workspace: {path}")
            self._git(None, "clone", self.clone_url, str(path))
        self._git(path, "config", "rerere.enabled", "true")
        self._git(path, "config", "rerere.autoupdate", "true")
        self._git(path, "fetch", "origin")
        branches = self._git(path, "branch", "--list", branch)
        if branches:
            self._git(path, "switch", branch)
        else:
            remote_exists = self._run(["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"], cwd=path).returncode == 0
            if remote_exists:
                self._git(path, "switch", "--track", f"origin/{branch}")
            else:
                self._git(path, "switch", "-c", branch, "origin/main")
        if not self._git(path, "status", "--porcelain"):
            behind = int(self._git(path, "rev-list", "--count", f"HEAD..origin/main") or "0")
            if behind:
                self._git(path, "merge", "--no-edit", "origin/main")
        return Workspace(path.resolve(), branch, created)

    def inspect(self, workspace: Workspace) -> dict[str, str]:
        return {"branch": self._git(workspace.path, "branch", "--show-current"),
            "head": self._git(workspace.path, "rev-parse", "HEAD"),
            "status": self._git(workspace.path, "status", "--short")}

    def cleanup(self, issue: Issue, *, dry_run: bool, allowed: bool, active: bool = False) -> bool:
        path = self.path_for(issue)
        if active or not allowed or not path.exists():
            return False
        if not dry_run:
            shutil.rmtree(path)
        return True


class HostGitLifecycle:
    """Protected Git lifecycle executed by the runner, never by the Codex child."""

    ALLOWED_MARKDOWN = {"AGENTS.md", "WORKFLOW.md"}
    FORBIDDEN_PARTS = {".env", ".next", "node_modules", "__pycache__", ".pytest_cache", "logs", "state"}

    def __init__(self, run: Run = subprocess.run):
        self._run = run

    def _git(self, path: Path, *args: str) -> str:
        result = self._run(["git", *args], cwd=path, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RetryableError(f"host git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()[:500]}")
        return result.stdout.strip()

    def changed_files(self, workspace: Workspace) -> list[str]:
        output = self._git(workspace.path, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        entries = output.split("\0") if output else []
        files: list[str] = []
        index = 0
        while index < len(entries):
            entry = entries[index]
            if not entry:
                index += 1; continue
            status, name = entry[:2], entry[3:]
            if status[0] in {"R", "C"}:
                index += 1
            files.append(name.replace("\\", "/")); index += 1
        return sorted(set(files))

    def validate_changes(self, workspace: Workspace, files: list[str]) -> None:
        root = workspace.path.resolve()
        for name in files:
            candidate = (root / name).resolve()
            try: candidate.relative_to(root)
            except ValueError as exc: raise ConfigurationError(f"Changed path escapes task workspace: {name}") from exc
            parts = set(Path(name).parts)
            if name != ".env.example" and (name == ".env" or name.startswith(".env.")):
                raise ConfigurationError(f"Refusing secret/runtime file: {name}")
            if parts & self.FORBIDDEN_PARTS or name.endswith((".pyc", ".pyo", ".tsbuildinfo")):
                raise ConfigurationError(f"Refusing runtime/artifact path: {name}")
            if name.lower().endswith(".md") and name not in self.ALLOWED_MARKDOWN:
                raise ConfigurationError(f"Markdown policy rejects: {name}")
            ignored = self._run(["git", "check-ignore", "--quiet", "--", name], cwd=root, capture_output=True, text=True, check=False)
            if ignored.returncode == 0:
                raise ConfigurationError(f"Refusing ignored path: {name}")

    def synchronize(self, workspace: Workspace) -> bool:
        before = self._git(workspace.path, "rev-parse", "HEAD")
        self._git(workspace.path, "config", "rerere.enabled", "true")
        self._git(workspace.path, "config", "rerere.autoupdate", "true")
        self._git(workspace.path, "fetch", "origin")
        behind = int(self._git(workspace.path, "rev-list", "--count", "HEAD..origin/main") or "0")
        if behind:
            self._git(workspace.path, "merge", "--no-edit", "origin/main")
        return before != self._git(workspace.path, "rev-parse", "HEAD")

    def verify(self, workspace: Workspace) -> None:
        script = workspace.path / "scripts" / ("verify-all.ps1" if os.name == "nt" else "verify-all.sh")
        command = (["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
                   if os.name == "nt" else ["bash", str(script)])
        result = self._run(command, cwd=workspace.path, capture_output=True, text=True, check=False)
        if result.returncode:
            raise ConfigurationError(f"host verification failed: {(result.stderr or result.stdout).strip()[-1000:]}")

    def commit_and_push(self, workspace: Workspace, message: str) -> str:
        files = self.changed_files(workspace)
        if files:
            self.validate_changes(workspace, files)
            self._git(workspace.path, "add", "--", *files)
            staged = [line for line in self._git(workspace.path, "diff", "--cached", "--name-only", "-z").split("\0") if line]
            if sorted(staged) != files:
                raise ConfigurationError("Staged files differ from inspected task changes")
            self._git(workspace.path, "commit", "-m", message)
        commit = self._git(workspace.path, "rev-parse", "HEAD")
        self._git(workspace.path, "push", "-u", "origin", workspace.branch)
        remote = self._git(workspace.path, "rev-parse", f"origin/{workspace.branch}")
        if remote != commit: raise RetryableError("pushed branch does not match host commit")
        return commit
