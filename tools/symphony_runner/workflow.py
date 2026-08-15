from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .config import RunnerConfig
from .models import ConfigurationError, Issue

VARIABLE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*}}")


def load_workflow(path: Path) -> RunnerConfig:
    if not path.is_file():
        raise ConfigurationError(f"missing workflow file: {path}")
    text = path.read_text(encoding="utf-8")
    raw: dict[str, Any] = {}
    prompt = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) != 3:
            raise ConfigurationError("workflow front matter is not terminated")
        try:
            value = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"workflow parse error: {exc}") from exc
        if not isinstance(value, dict):
            raise ConfigurationError("workflow front matter must be a map")
        raw, prompt = value, parts[2].strip()
    return RunnerConfig.from_mapping(raw, prompt, path.resolve())


def render_prompt(config: RunnerConfig, issue: Issue, branch: str, workspace: Path, attempt: int) -> str:
    values = {
        "issue.identifier": issue.identifier, "issue.number": str(issue.number), "issue.title": issue.title,
        "issue.description": issue.body, "issue.body": issue.body, "issue.url": issue.url,
        "issue.state": issue.state, "issue.labels": ", ".join(issue.labels), "repository": config.repository,
        "branch": branch, "workspace": str(workspace), "attempt": str(attempt),
    }
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ConfigurationError(f"unknown workflow template variable: {key}")
        return values[key]
    rendered = VARIABLE.sub(replace, config.prompt)
    return rendered + f"\n\nIssue body (acceptance criteria):\n{issue.body}\n\nWorkspace: {workspace}\nBranch: {branch}\nAttempt: {attempt}. Resume existing Git and Workpad progress; do not auto-merge."
