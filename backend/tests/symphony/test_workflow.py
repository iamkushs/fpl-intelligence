from pathlib import Path

import pytest

from tools.symphony_runner.models import ConfigurationError, Issue
from tools.symphony_runner.workflow import load_workflow, render_prompt


def workflow(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "WORKFLOW.md"; path.write_text(body, encoding="utf-8"); return path


def test_missing_workflow(tmp_path):
    with pytest.raises(ConfigurationError): load_workflow(tmp_path / "missing")


def test_front_matter_environment_and_unknown_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret"); monkeypatch.setenv("SYMPHONY_WORKSPACE_ROOT", str(tmp_path / "spaces"))
    path = workflow(tmp_path, "---\ntracker:\n  kind: github\n  provider: {repo: o/r, token: $GITHUB_TOKEN}\n  required_labels: [SYMPHONY]\nworkspace: {root: $SYMPHONY_WORKSPACE_ROOT}\nfuture: {x: 1}\n---\nHi {{ issue.number }}")
    config = load_workflow(path)
    assert config.repository == "o/r" and config.tracker_token == "secret"
    assert config.required_labels == ("symphony",) and config.paths.workspaces == (tmp_path / "spaces").resolve()
    assert "GITHUB_TOKEN" not in config.sanitized_child_environment()


def test_render_prompt_strict_and_windows_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = workflow(tmp_path, "---\ntracker: {kind: github, provider: {repo: o/r}}\n---\n{{ issue.identifier }} {{ branch }} {{ workspace }}")
    config = load_workflow(path); issue = Issue(12, "T", "Body", ("symphony",), "open", "u")
    rendered = render_prompt(config, issue, "symphony/gh-12", Path(r"C:\work\GH-12"), 1)
    assert "GH-12" in rendered and r"C:\work\GH-12" in rendered


def test_unknown_template_variable_fails(tmp_path):
    config = load_workflow(workflow(tmp_path, "---\ntracker: {kind: github, provider: {repo: o/r}}\n---\n{{ bad.value }}"))
    with pytest.raises(ConfigurationError): render_prompt(config, Issue(1,"","",(),"open",""), "b", tmp_path, 1)
