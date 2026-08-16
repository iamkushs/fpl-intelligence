from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .models import GitHubOutputError, GitHubServiceError, Issue, RetryableError

Run = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(slots=True)
class Workpad:
    comment_id: int
    body: str


class GitHubClient:
    def __init__(self, repository: str, token: str = "", run: Run = subprocess.run):
        self.repository, self.token, self._run = repository, token, run

    def _gh(self, *args: str) -> str:
        env = os.environ.copy()
        if self.token:
            env["GH_TOKEN"] = self.token
        command = ["gh", *args]
        try:
            result = self._run(command, capture_output=True, text=True, encoding="utf-8", errors="strict", env=env, check=False)
        except UnicodeDecodeError as exc:
            raise GitHubOutputError(f"GitHub CLI output was not valid UTF-8 at byte {exc.start}") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitHubOutputError(f"GitHub CLI local process failed: {type(exc).__name__}: {exc}") from exc
        if result.returncode:
            message = (result.stderr or result.stdout).strip()
            raise GitHubServiceError(f"GitHub CLI exited {result.returncode}: {message[:500] or 'no diagnostic output'}")
        if not isinstance(result.stdout, str):
            raise GitHubOutputError("GitHub CLI returned no stdout value")
        return result.stdout

    @staticmethod
    def _decode(raw: str, operation: str) -> Any:
        if not isinstance(raw, str):
            raise GitHubOutputError(f"GitHub {operation} returned no JSON text")
        if not raw.strip():
            raise GitHubOutputError(f"GitHub {operation} returned empty required JSON output")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GitHubOutputError(f"GitHub {operation} returned malformed JSON at character {exc.pos}") from exc

    @staticmethod
    def _issue(value: dict[str, Any]) -> Issue:
        return Issue(number=int(value["number"]), title=value.get("title", ""), body=value.get("body", "") or "",
            labels=tuple(label["name"].lower() if isinstance(label, dict) else str(label).lower() for label in value.get("labels", [])),
            state=value.get("state", "open").lower(), url=value.get("url", ""), created_at=value.get("createdAt"),
            updated_at=value.get("updatedAt"), is_pull_request=bool(value.get("isPullRequest", False)))

    def list_candidates(self, labels: tuple[str, ...]) -> list[Issue]:
        args = ["issue", "list", "--repo", self.repository, "--state", "open", "--limit", "100",
            "--json", "number,title,body,labels,state,url,createdAt,updatedAt"]
        for label in labels:
            args.extend(["--label", label])
        return [self._issue(value) for value in self._decode(self._gh(*args), "issue list") if not value.get("isPullRequest")]

    def get_issue(self, number: int) -> Issue:
        raw = self._gh("issue", "view", str(number), "--repo", self.repository,
            "--json", "number,title,body,labels,state,url,createdAt,updatedAt")
        return self._issue(self._decode(raw, "issue view"))

    def comments(self, number: int) -> list[dict[str, Any]]:
        return self._decode(self._gh("api", f"repos/{self.repository}/issues/{number}/comments"), "comments")

    def find_workpad(self, number: int) -> Workpad | None:
        matches = [value for value in self.comments(number) if str(value.get("body", "")).startswith("## Codex Workpad")]
        if not matches:
            return None
        if len(matches) > 1:
            raise RuntimeError(f"Issue {number} has multiple Codex Workpad comments; refusing to choose another")
        first = matches[0]
        return Workpad(int(first["id"]), first["body"])

    def ensure_workpad(self, issue: Issue) -> Workpad:
        existing = self.find_workpad(issue.number)
        if existing:
            return existing
        body = "## Codex Workpad\n\n### Plan\n\n### Acceptance Criteria\n\n### Validation\n\n### State / Progress\n\n### Notes\n\n### Blockers\n"
        payload = self._decode(self._gh("api", f"repos/{self.repository}/issues/{issue.number}/comments", "-f", f"body={body}"), "create comment")
        return Workpad(int(payload["id"]), body)

    def update_workpad(self, comment_id: int, body: str) -> Workpad:
        if not body.startswith("## Codex Workpad"):
            raise ValueError("Workpad updates must preserve the ## Codex Workpad marker")
        payload = self._decode(self._gh("api", "--method", "PATCH", f"repos/{self.repository}/issues/comments/{comment_id}", "-f", f"body={body}"), "update comment")
        return Workpad(int(payload["id"]), payload["body"])

    def add_label(self, number: int, label: str) -> None:
        self._gh("issue", "edit", str(number), "--repo", self.repository, "--add-label", label)

    def remove_label(self, number: int, label: str) -> None:
        self._gh("issue", "edit", str(number), "--repo", self.repository, "--remove-label", label)

    def pr_for_branch(self, branch: str) -> dict[str, Any] | None:
        values = self._decode(self._gh("pr", "list", "--repo", self.repository, "--head", branch, "--state", "open", "--json", "number,url,title,body"), "PR list")
        return values[0] if values else None

    def create_pr(self, branch: str, title: str, body: str) -> dict[str, Any]:
        self._gh("pr", "create", "--repo", self.repository, "--head", branch, "--base", "main", "--title", title, "--body", body)
        result = self.pr_for_branch(branch)
        if not result:
            raise RetryableError("PR creation returned no discoverable PR")
        return result

    def tool_specs(self) -> list[dict[str, Any]]:
        def spec(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
            return {"type": "function", "name": name, "description": description,
                "inputSchema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}
        number = {"issue_number": {"type": "integer"}}
        return [{"type": "namespace", "name": "github", "description": "Narrow host-side GitHub issue and PR operations", "tools": [
            spec("read_issue", "Read the current issue", number, ["issue_number"]),
            spec("read_comments", "Read issue comments", number, ["issue_number"]),
            spec("ensure_workpad", "Find or create the single Codex Workpad", number, ["issue_number"]),
            spec("update_workpad", "Update the existing Codex Workpad", {**number, "body": {"type": "string"}}, ["issue_number", "body"]),
            spec("add_label", "Add an allowed Symphony label", {**number, "label": {"type": "string"}}, ["issue_number", "label"]),
            spec("remove_label", "Remove an allowed Symphony label", {**number, "label": {"type": "string"}}, ["issue_number", "label"]),
            spec("inspect_pr", "Inspect the PR for the current branch; final PR mutation is owned by the host runner", {"branch": {"type": "string"}}, ["branch"]),
        ]}]

    def invoke_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        allowed_labels = {"symphony", "symphony-review", "symphony-blocked"}
        if name == "read_issue": return asdict(self.get_issue(int(arguments["issue_number"])))
        if name == "read_comments": return self.comments(int(arguments["issue_number"]))
        if name == "ensure_workpad": return asdict(self.ensure_workpad(self.get_issue(int(arguments["issue_number"]))))
        if name == "update_workpad":
            current = self.find_workpad(int(arguments["issue_number"]))
            if not current: raise ValueError("Workpad does not exist; call ensure_workpad first")
            return asdict(self.update_workpad(current.comment_id, str(arguments["body"])))
        if name in {"add_label", "remove_label"}:
            label = str(arguments["label"]).lower()
            if label not in allowed_labels: raise ValueError("Only Symphony workflow labels are allowed")
            getattr(self, name)(int(arguments["issue_number"]), label); return {"ok": True}
        if name == "inspect_pr": return self.pr_for_branch(str(arguments["branch"]))
        raise ValueError(f"Unsupported GitHub tool: {name}")
