from __future__ import annotations

import asyncio
import random
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .app_server import CodexAppServer
from .config import RunnerConfig
from .github import GitHubClient
from .logging import StructuredLogger
from .models import ConfigurationError, Issue, RunRecord, RetryableError, RunnerError, utc_now
from .state import StateStore
from .status_server import StatusServer
from .workflow import render_prompt
from .workspace import Workspace, WorkspaceManager


class Orchestrator:
    def __init__(self, config: RunnerConfig, github: GitHubClient | None = None, workspace: WorkspaceManager | None = None,
                 store: StateStore | None = None, logger: StructuredLogger | None = None):
        assert config.paths
        self.config = config
        self.github = github or GitHubClient(config.repository, config.tracker_token)
        self.workspace = workspace or WorkspaceManager(config.paths.workspaces, config.repository)
        self.store = store or StateStore(config.paths.state_file); self.store.load()
        self.logger = logger or StructuredLogger(config.paths.logs)
        self.running: dict[int, asyncio.Task[None]] = {}
        self.stop_event = asyncio.Event(); self.started = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        return {"status": "stopping" if self.stop_event.is_set() else "running", "repository": self.config.repository,
            "uptime_seconds": round(time.monotonic() - self.started, 1), "active": sorted(self.running),
            "issues": {str(key): value.to_dict() for key, value in self.store.records.items()}}

    def eligible(self, issue: Issue) -> bool:
        labels = {value.lower() for value in issue.labels}
        return (not issue.is_pull_request and issue.state.lower() in self.config.active_states
            and all(label in labels for label in self.config.required_labels)
            and "symphony-review" not in labels and "symphony-blocked" not in labels)

    async def _run_hook(self, name: str, workspace: Workspace) -> None:
        windows_key = f"{name}_windows"
        value = self.config.hooks.get(windows_key)
        if value is None:
            if self.config.hooks.get(name) and name == "after_create":
                raise ConfigurationError(f"POSIX hook '{name}' has no Windows '{windows_key}' equivalent")
            return
        commands = value if isinstance(value, list) else [value]
        timeout = self.config.hooks.get("timeout_ms", 60_000) / 1000
        for command in commands:
            parts = [str(part) for part in command] if isinstance(command, list) else ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", str(command)]
            process = await asyncio.create_subprocess_exec(*parts, cwd=workspace.path)
            try: code = await asyncio.wait_for(process.wait(), timeout)
            except TimeoutError: process.kill(); await process.wait(); raise ConfigurationError(f"Windows hook {windows_key} timed out")
            if code: raise ConfigurationError(f"Windows hook {windows_key} failed with exit code {code}")

    async def _execute_issue(self, issue: Issue) -> None:
        workspace = self.workspace.prepare(issue)
        if workspace.created: await self._run_hook("after_create", workspace)
        workpad = self.github.ensure_workpad(issue)
        record = self.store.records.get(issue.number) or RunRecord(issue.number, str(workspace.path), workspace.branch)
        record.attempt += 1; record.status = "running"; record.last_activity = utc_now(); record.retry_at = None
        self.store.records[issue.number] = record; self.store.save()
        self.logger.event("issue_start", issue=issue.number, branch=workspace.branch, workspace=workspace.path, attempt=record.attempt)
        prompt = render_prompt(self.config, issue, workspace.branch, workspace.path, record.attempt)
        def scoped_tool(name: str, arguments: dict[str, Any]) -> Any:
            if "issue_number" in arguments and int(arguments["issue_number"]) != issue.number:
                raise ValueError("GitHub tools are scoped to the current issue")
            if "branch" in arguments and str(arguments["branch"]) != workspace.branch:
                raise ValueError("GitHub tools are scoped to the current issue branch")
            if name == "create_pr":
                body = str(arguments.get("body", ""))
                if issue.url not in body and f"#{issue.number}" not in body:
                    raise ValueError("PR body must reference the current issue")
            return self.github.invoke_tool(name, arguments)
        client = CodexAppServer(self.config, workspace.path, self.github.tool_specs(), scoped_tool)
        try:
            for turn_number in range(self.config.max_turns):
                result = await client.run_turn(prompt if turn_number == 0 else "Resume from Git and the existing Codex Workpad. Complete only remaining acceptance criteria and handoff requirements.", record.thread_id)
                record.thread_id, record.turn_id = result.thread_id, result.turn_id
                record.last_activity = utc_now(); self.store.save()
                current = self.github.get_issue(issue.number); labels = set(current.labels)
                pr = self.github.pr_for_branch(workspace.branch); pad = self.github.find_workpad(issue.number)
                if "symphony-review" in labels and "symphony" not in labels and pr and pad and "### Validation" in pad.body:
                    record.status = "review"; record.last_error = None; break
                if "symphony-blocked" in labels and "symphony" not in labels and pad and "### Blockers" in pad.body:
                    record.status = "blocked"; break
                if not self.eligible(current): record.status = "ineligible"; break
            else:
                raise RetryableError("Maximum Codex turns reached while issue remains eligible")
            self.logger.event("issue_handoff", issue=issue.number, status=record.status, thread_id=record.thread_id)
        except asyncio.CancelledError:
            record.status = "ineligible"; record.last_activity = utc_now(); self.store.save(); raise
        except Exception as exc:
            record.last_error = self.config.redact(str(exc))[:1000]
            deterministic = isinstance(exc, ConfigurationError) or (isinstance(exc, RunnerError) and not exc.retryable)
            if deterministic:
                record.status = "failed"; record.retry_at = None
                self.logger.event("issue_failed", issue=issue.number, error=record.last_error)
            else:
                record.status = "retrying"
                delay_ms = min(self.config.max_retry_backoff_ms, 1000 * (2 ** min(record.attempt, 8)))
                record.retry_at = time.time() + (delay_ms / 1000) * random.uniform(0.75, 1.25)
                self.logger.event("issue_retry", issue=issue.number, attempt=record.attempt, retry_at=record.retry_at, error=record.last_error)
        finally:
            await client.close(); record.last_activity = utc_now(); self.store.save()

    async def cycle(self) -> None:
        self.logger.event("poll_cycle", active=len(self.running))
        for number, task in list(self.running.items()):
            if task.done():
                try: task.result()
                except asyncio.CancelledError: pass
                except Exception as exc: self.logger.event("worker_error", issue=number, error=str(exc))
                self.running.pop(number, None)
                continue
            try: current = self.github.get_issue(number)
            except RetryableError: continue
            if not self.eligible(current): task.cancel()
        candidates = self.github.list_candidates(self.config.required_labels)
        capacity = self.config.max_concurrent_agents - len(self.running)
        for issue in candidates:
            if capacity <= 0: break
            record = self.store.records.get(issue.number)
            if issue.number in self.running or not self.eligible(issue): continue
            if record and record.retry_at and record.retry_at > time.time(): continue
            self.running[issue.number] = asyncio.create_task(self._execute_issue(issue), name=issue.identifier)
            capacity -= 1

    async def run(self) -> None:
        server = StatusServer(self.config.status_host, self.config.status_port, self.snapshot); server.start()
        self.logger.event("runner_start", codex_version=CodexAppServer.version(), concurrency=self.config.max_concurrent_agents)
        try:
            while not self.stop_event.is_set():
                try: await self.cycle()
                except RetryableError as exc: self.logger.event("poll_retryable_error", error=str(exc))
                try: await asyncio.wait_for(self.stop_event.wait(), self.config.poll_interval_ms / 1000)
                except TimeoutError: pass
        finally:
            self.stop_event.set()
            for task in self.running.values(): task.cancel()
            if self.running: await asyncio.gather(*self.running.values(), return_exceptions=True)
            self.store.save(); server.stop(); self.logger.event("runner_stop")

    def stop(self) -> None: self.stop_event.set()
