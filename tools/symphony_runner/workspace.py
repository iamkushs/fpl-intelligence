from __future__ import annotations

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
