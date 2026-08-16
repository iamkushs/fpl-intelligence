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
from .dependencies import cycle_members, parse_dependencies
from .github import GitHubClient
from .logging import StructuredLogger, exception_location
from .maintenance import MaintenancePolicy
from .models import (AppServerMessageTooLarge, AppServerProtocolError, ConfigurationError,
    FailureClass, Incident, Issue, IssuePhase, ReviewerDecision, ReviewerVerdict, RunRecord,
    RetryableError, RunnerError, utc_now)
from .recovery import (REVIEWER_SCHEMA, record_incident, repair_prompt, rescue_review,
    reviewer_prompt, safe_exception_bundle)
from .state import StateStore
from .status_server import StatusServer
from .workflow import render_prompt
from .workspace import HostGitLifecycle, Workspace, WorkspaceManager
from .routing import ModelRouter


class Orchestrator:
    def __init__(self, config: RunnerConfig, github: GitHubClient | None = None, workspace: WorkspaceManager | None = None,
                 store: StateStore | None = None, logger: StructuredLogger | None = None,
                 git_lifecycle: HostGitLifecycle | None = None):
        assert config.paths
        self.config = config
        self.github = github or GitHubClient(config.repository, config.tracker_token)
        self.workspace = workspace or WorkspaceManager(config.paths.workspaces, config.repository)
        self.store = store or StateStore(config.paths.state_file); self.store.load()
        self.logger = logger or StructuredLogger(config.paths.logs)
        self.git_lifecycle = git_lifecycle or HostGitLifecycle()
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

    def _queue_record(self, issue: Issue) -> RunRecord:
        record = self.store.records.get(issue.number)
        if record is None:
            record = RunRecord(issue.number, str(self.workspace.path_for(issue)), self.workspace.branch_for(issue))
            self.store.records[issue.number] = record
        record.waiting_for_active_issue = None
        return record

    def _dependency_state(self, candidates: list[Issue]) -> dict[int, tuple[int, ...]]:
        graph: dict[int, tuple[int, ...]] = {}
        errors: dict[int, str] = {}
        for issue in candidates:
            try: graph[issue.number] = parse_dependencies(issue)
            except ConfigurationError as exc: errors[issue.number] = str(exc)
        cyclic = cycle_members(graph)
        for issue in candidates:
            record = self._queue_record(issue)
            record.blocked_by = []
            record.eligible = False
            if issue.number in errors:
                record.phase, record.status = IssuePhase.BLOCKED.value, "invalid_dependency"
                record.queue_reason = errors[issue.number]
                continue
            if issue.number in cyclic:
                record.phase, record.status = IssuePhase.BLOCKED.value, "invalid_dependency"
                record.queue_reason = "dependency cycle detected"
                continue
            unresolved = []
            for number in graph.get(issue.number, ()):
                dependency = self.github.get_issue(number)
                if dependency.state.lower() not in self.config.terminal_states:
                    unresolved.append(number)
            record.blocked_by = unresolved
            if unresolved:
                record.phase, record.status = IssuePhase.WAITING_DEPENDENCY.value, "waiting_dependency"
                record.queue_reason = "dependencies must be closed"
            else:
                record.eligible = self.eligible(issue)
                if record.phase in {IssuePhase.QUEUED.value, IssuePhase.WAITING_DEPENDENCY.value,
                                    IssuePhase.WAITING_CAPACITY.value, IssuePhase.BLOCKED.value} and record.status in {
                                    "pending", "waiting_dependency", "waiting_capacity", "invalid_dependency"}:
                    record.phase, record.status = IssuePhase.QUEUED.value, "pending"
                record.queue_reason = None
        self.store.save()
        return graph

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
        resumed_phase = record.phase
        record.attempt += 1; record.status = "running"; record.phase = IssuePhase.CODING.value
        record.last_activity = utc_now(); record.retry_at = None
        self.store.records[issue.number] = record; self.store.save()
        self.logger.event("issue_start", issue=issue.number, branch=workspace.branch, workspace=workspace.path, attempt=record.attempt)
        prompt = render_prompt(self.config, issue, workspace.branch, workspace.path, record.attempt)
        if resumed_phase == IssuePhase.RECOVERY_PLANNED.value and record.current_incident_id:
            value = self._incident_value(record, record.current_incident_id)
            decision_payload = {"verdict": value.get("recovery_action", "RETRY"),
                "failure_class": value.get("failure_class", "UNKNOWN"),
                "root_cause": value.get("previous_reviewer_diagnosis", ""), "confidence": value.get("reviewer_confidence", "low"),
                "productive_failure": bool(value.get("productive_failure")), "preserve_workspace": True,
                "preserve_thread": record.thread_id is not None, "rotate_thread": False,
                "escalate_coding_model": False, "repair_scope": value.get("reviewer_plan", []),
                "plan": value.get("reviewer_plan", []), "verification": [], "stop_conditions": [], "notes": ""}
            prompt = repair_prompt(issue, workpad.body, record, Incident(**value), ReviewerDecision.from_dict(decision_payload))
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
        def persist_thread(value: str) -> None:
            record.thread_id = value; record.last_activity = utc_now(); self.store.save()
        def persist_turn(value: str) -> None:
            record.turn_id = value; record.last_activity = utc_now(); self.store.save()
        try:
            catalog = await client.model_catalog()
            router = ModelRouter.load(catalog, self.config.model_policy_path)
            route, reason = router.reconcile(issue, record)
            resolved = router.resolve(route)
            record.resolved_model_id = resolved.model.id; record.reasoning_effort = resolved.effort
            record.routing_reason = reason + (f"; effort fell back to {resolved.effort}" if resolved.effort_fallback else "")
            self.store.save()
            routing = ("\n\n### Model Routing\n\n"
                f"Initial model: {record.requested_model_route}\n\nCurrent model: {route}\n\n"
                f"Actual model ID: `{resolved.model.id}`\n\nEffort: {resolved.effort}\n\n"
                f"Routing reason: {record.routing_reason}\n\nEscalations: {record.escalation_level}\n")
            base = workpad.body.split("\n\n### Model Routing", 1)[0]
            workpad = self.github.update_workpad(workpad.comment_id, base.rstrip() + routing)
            self.logger.event("model_route", issue=issue.number, route=route, model=resolved.model.id,
                              effort=resolved.effort, escalation_level=record.escalation_level)
            for turn_number in range(self.config.max_turns):
                result = await client.run_turn(prompt if turn_number == 0 else "Resume from the existing workspace and Codex Workpad. Complete only remaining acceptance criteria and request host handoff.",
                                               record.thread_id, model=resolved.model.id, effort=resolved.effort,
                                               on_thread=persist_thread, on_turn=persist_turn)
                record.thread_id, record.turn_id = result.thread_id, None
                record.coding_completion_summary = result.output
                record.last_activity = utc_now(); self.store.save()
                current = self.github.get_issue(issue.number); labels = set(current.labels)
                pr = self.github.pr_for_branch(workspace.branch); pad = self.github.find_workpad(issue.number)
                if pad and self._handoff_ready(pad.body):
                    record.phase = IssuePhase.VERIFYING.value; self.store.save()
                    self.git_lifecycle.verify(workspace)
                    record.phase = IssuePhase.REVIEWING.value; self.store.save()
                    decision = await self._review(issue, workspace, pad.body, record, catalog,
                                                  verification={"verify_all": "passed"})
                    self._apply_review(record, decision, None)
                    pad = self._update_recovery_workpad(pad, record, decision)
                    if decision.verdict == ReviewerVerdict.PASS:
                        record.phase = IssuePhase.HOST_HANDOFF.value
                        commit = self._host_handoff(issue, workspace, pad)
                        for value in record.incidents:
                            if value.get("status") == "resolved" and not value.get("resolution_commit"):
                                value["resolution_commit"] = commit
                        record.turn_id = None; record.status = "review"; record.last_error = None
                        self.logger.event("host_handoff", issue=issue.number, commit=commit); break
                    if decision.verdict == ReviewerVerdict.CHANGES_REQUIRED:
                        record.phase = IssuePhase.CODING.value
                        prompt = repair_prompt(issue, pad.body, record, None, decision); continue
                    if decision.verdict == ReviewerVerdict.RETRY:
                        record.phase = IssuePhase.CODING.value
                        prompt = repair_prompt(issue, pad.body, record, None, decision); continue
                    record.phase = (IssuePhase.EXTERNAL_WAIT.value if decision.verdict == ReviewerVerdict.BLOCKED_EXTERNAL
                                    else IssuePhase.BLOCKED.value)
                    record.status = "external_wait" if decision.verdict == ReviewerVerdict.BLOCKED_EXTERNAL else "human_required"
                    break
                if "symphony-review" in labels and "symphony" not in labels and pr and pad and "### Validation" in pad.body:
                    record.phase = IssuePhase.HOST_HANDOFF.value
                    record.status = "review"; record.last_error = None; break
                if "symphony-blocked" in labels and "symphony" not in labels and pad and "### Blockers" in pad.body:
                    record.phase = IssuePhase.BLOCKED.value; record.status = "blocked"; break
                if not self.eligible(current):
                    self._normalize_ineligible(record, current); break
            else:
                router.record_productive_failure(record)
                raise RetryableError("Productive implementation attempt exhausted its turn budget while issue remains eligible")
            self.logger.event("issue_handoff", issue=issue.number, status=record.status, thread_id=record.thread_id)
        except asyncio.CancelledError:
            try: self._normalize_ineligible(record, self.github.get_issue(issue.number))
            except RetryableError: record.phase, record.status = IssuePhase.BLOCKED.value, "ineligible"
            record.last_activity = utc_now(); self.store.save(); raise
        except Exception as exc:
            record.last_error = self.config.redact(str(exc))[:1000]
            incident = record_incident(self.config, record, exc, phase=record.phase)
            record.phase = IssuePhase.INCIDENT_REVIEW.value; self.store.save()
            bundle = safe_exception_bundle(self.config, exc, issue=issue.number, phase=record.phase,
                attempt=record.attempt, thread_id=record.thread_id, turn_id=record.turn_id,
                model=record.resolved_model_id, incident_id=incident.incident_id)
            self.logger.event("incident_opened", failure_class=incident.failure_class,
                              signature=incident.failure_signature, **bundle)
            decision: ReviewerDecision | None = None
            current = self._incident_value(record, incident.incident_id)
            if int(current.get("repair_attempts", 0)) < self.config.max_incident_repairs and record.total_repair_attempts < self.config.max_issue_repairs:
                try:
                    decision = await self._review(issue, workspace, workpad.body, record,
                        locals().get("catalog"), incident=incident, failure=exc)
                    self._apply_review(record, decision, incident)
                    authoritative = FailureClass(self._incident_value(record, incident.incident_id)["failure_class"])
                    if decision.productive_failure and authoritative in {
                            FailureClass.PRODUCT_IMPLEMENTATION, FailureClass.PRODUCT_TEST_FAILURE} and "catalog" in locals() \
                            and "Productive implementation attempt" not in str(exc):
                        ModelRouter.load(catalog, self.config.model_policy_path).record_productive_failure(record)
                    workpad = self._update_recovery_workpad(workpad, record, decision)
                except Exception as review_exc:
                    self.logger.event("reviewer_failure", issue=issue.number, incident=incident.incident_id,
                                      error=self.config.redact(str(review_exc))[:1000])
            if decision and decision.verdict in {ReviewerVerdict.CHANGES_REQUIRED, ReviewerVerdict.RETRY}:
                if MaintenancePolicy.eligible(incident, decision.confidence.value):
                    # Never send runner-infrastructure repairs into the affected product workspace.
                    # The separate bounded maintenance API may be driven by an operator; this issue stays parked.
                    record.parked_for_maintenance = True; record.phase = IssuePhase.BLOCKED.value
                    record.status = "maintenance_required"; record.retry_at = None
                elif decision.rotate_thread and isinstance(exc, AppServerProtocolError):
                    self._rotate_unhealthy_thread(record, issue.number, record.last_error)
                    record.phase = IssuePhase.RECOVERY_PLANNED.value; record.status = "retrying"; record.retry_at = time.time()
                else:
                    record.phase = IssuePhase.RECOVERY_PLANNED.value; record.status = "retrying"; record.retry_at = time.time()
            elif decision and decision.verdict == ReviewerVerdict.BLOCKED_EXTERNAL:
                record.phase = IssuePhase.EXTERNAL_WAIT.value; record.status = "external_wait"
                record.retry_at = time.time() + self.config.max_retry_backoff_ms / 1000
            elif decision and decision.verdict == ReviewerVerdict.HUMAN_REQUIRED:
                record.phase = IssuePhase.BLOCKED.value; record.status = "human_required"; record.retry_at = None
            elif int(current.get("repair_attempts", 0)) >= self.config.max_incident_repairs or record.total_repair_attempts >= self.config.max_issue_repairs:
                record.phase = IssuePhase.BLOCKED.value; record.status = "blocked"; record.retry_at = None
            elif isinstance(exc, AppServerProtocolError):
                self._rotate_unhealthy_thread(record, issue.number, record.last_error)
            deterministic = isinstance(exc, ConfigurationError) or (isinstance(exc, RunnerError) and not exc.retryable)
            if decision or record.status == "blocked":
                pass
            elif deterministic:
                record.status = "failed"; record.retry_at = None
                if isinstance(exc, ConfigurationError) and "conflicting model labels" in str(exc):
                    body = workpad.body.rstrip() + f"\n\nModel routing configuration error: {record.last_error}\n"
                    self.github.update_workpad(workpad.comment_id, body)
                    self.github.remove_label(issue.number, "symphony"); self.github.add_label(issue.number, "symphony-blocked")
                    record.status = "blocked"
                self.logger.event("issue_failed", issue=issue.number, error=record.last_error)
            else:
                record.status = "retrying"
                delay_ms = min(self.config.max_retry_backoff_ms, 1000 * (2 ** min(record.attempt, 8)))
                record.retry_at = time.time() + (delay_ms / 1000) * random.uniform(0.75, 1.25)
                metadata = {"configured_limit": exc.limit, "observed_size": exc.observed} if isinstance(exc, AppServerMessageTooLarge) else {}
                location = None
                if not isinstance(exc, RunnerError):
                    location = exception_location(exc)
                self.logger.event("issue_retry", issue=issue.number, attempt=record.attempt, retry_at=record.retry_at,
                                  thread_id=record.thread_id, turn_id=record.turn_id, error=record.last_error,
                                  exception_type=type(exc).__name__, exception_location=location, **metadata)
        finally:
            await client.close(); record.last_activity = utc_now(); self.store.save()

    def _rotate_unhealthy_thread(self, record: RunRecord, issue_number: int, reason: str) -> bool:
        """Durably forget only a protocol-damaged thread; never touch its workspace."""
        if not record.thread_id:
            return False
        old_thread = record.thread_id
        if old_thread not in record.unhealthy_thread_ids:
            record.unhealthy_thread_ids.append(old_thread)
        record.thread_id = None
        record.turn_id = None
        record.thread_rotations += 1
        self.store.save()
        self.logger.event("thread_rotated", issue=issue_number, old_thread_id=old_thread,
                          reason=reason, workspace=record.workspace)
        return True

    @staticmethod
    def _incident_value(record: RunRecord, incident_id: str) -> dict[str, Any]:
        return next(value for value in record.incidents if value.get("incident_id") == incident_id)

    async def _review(self, issue: Issue, workspace: Workspace, workpad: str, record: RunRecord,
                      catalog: Any, *, incident: Incident | None = None,
                      verification: dict[str, Any] | None = None,
                      failure: BaseException | None = None) -> ReviewerDecision:
        if catalog is None:
            probe = CodexAppServer(self.config, workspace.path, [], lambda *_: {})
            try: catalog = await probe.model_catalog()
            finally: await probe.close()
        index = int(self._incident_value(record, incident.incident_id).get("repair_attempts", 0)) if incident else record.review_iterations
        route = self._reviewer_route(index)
        resolved = ModelRouter.load(catalog, self.config.model_policy_path).resolve(route)
        effort = "high" if "high" in resolved.model.efforts else resolved.effort
        record.reviewer_route, record.reviewer_model_id, record.reviewer_effort = route, resolved.model.id, effort
        context = {"workspace": str(workspace.path), "branch": workspace.branch,
            "diff_stat": self._git_read(workspace.path, "diff", "--stat"),
            "changed_paths": self.git_lifecycle.changed_files(workspace),
            "diff": self._git_read(workspace.path, "diff", "--", ".")[-12000:],
            "verification": verification or {}, "phase": record.phase, "thread_id": record.thread_id,
            "turn_id": record.turn_id, "coding_route": record.requested_model_route,
            "coding_model": record.resolved_model_id, "coding_completion_summary": record.coding_completion_summary,
            "incident": incident.to_dict() if incident else None, "incident_history": record.incidents[-3:]}
        prompt = reviewer_prompt(issue, workpad, context, failure=failure is not None)
        if incident and incident.failure_class in {FailureClass.APP_SERVER_PROTOCOL.value, FailureClass.APP_SERVER_CONTEXT.value}:
            decision = rescue_review(self.config, workspace.path, prompt, resolved.model.id)
        else:
            reviewer = CodexAppServer(self.config, workspace.path, [], lambda *_: {})
            def persist(value: str) -> None: record.reviewer_thread_id = value; self.store.save()
            try:
                result = await reviewer.run_turn(prompt, record.reviewer_thread_id, model=resolved.model.id,
                    effort=effort, on_thread=persist, output_schema=REVIEWER_SCHEMA, read_only=True)
            finally: await reviewer.close()
            if result.output is None: raise AppServerProtocolError("reviewer completed without structured output")
            import json
            try: payload = json.loads(result.output)
            except (TypeError, ValueError) as parse_exc:
                raise AppServerProtocolError("reviewer output was not valid JSON") from parse_exc
            decision = ReviewerDecision.from_dict(payload)
        self.logger.event("reviewer_verdict", issue=issue.number,
            incident=incident.incident_id if incident else None, verdict=decision.verdict.value,
            route=route, model=resolved.model.id, effort=effort)
        return decision

    def _reviewer_route(self, diagnosis_index: int) -> str:
        """Bounded ordinary progression: 5.5 high, Terra high, then Sol high."""
        return self.config.reviewer_routes[min(max(diagnosis_index, 0), 2, len(self.config.reviewer_routes) - 1)]

    @staticmethod
    def _git_read(path: Path, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False)
        return (result.stdout if result.returncode == 0 else result.stderr)[:20000]

    @staticmethod
    def _normalize_ineligible(record: RunRecord, issue: Issue) -> None:
        labels = {value.lower() for value in issue.labels}
        if "symphony-review" in labels:
            record.phase, record.status = IssuePhase.HOST_HANDOFF.value, "review"
        elif "symphony-blocked" in labels:
            record.phase, record.status = IssuePhase.BLOCKED.value, "blocked"
        elif issue.state.lower() == "closed":
            record.phase, record.status = IssuePhase.HOST_HANDOFF.value, "complete"
        else:
            record.phase, record.status = IssuePhase.BLOCKED.value, "ineligible"

    def _apply_review(self, record: RunRecord, decision: ReviewerDecision, incident: Incident | None) -> None:
        record.reviewer_verdict = decision.verdict.value; record.review_iterations += 1
        if decision.verdict == ReviewerVerdict.PASS:
            now = utc_now()
            for value in record.incidents:
                if value.get("status") == "open":
                    value["status"] = "resolved"; value["resolved_at"] = now
            record.current_incident_id = None
        if incident:
            value = self._incident_value(record, incident.incident_id)
            value["failure_class"] = self._authoritative_class(value["failure_class"], decision.failure_class).value
            value["previous_reviewer_diagnosis"] = decision.root_cause
            value["reviewer_confidence"] = decision.confidence.value; value["reviewer_plan"] = decision.plan
            value["reviewer_route"], value["reviewer_model"], value["reviewer_effort"] = (
                record.reviewer_route, record.reviewer_model_id, record.reviewer_effort)
            value["recovery_action"] = decision.verdict.value
            if decision.verdict in {ReviewerVerdict.CHANGES_REQUIRED, ReviewerVerdict.RETRY}:
                value["repair_attempts"] = int(value.get("repair_attempts", 0)) + 1
                record.total_repair_attempts += 1
        self.store.save()

    @staticmethod
    def _authoritative_class(current: str, proposed: FailureClass) -> FailureClass:
        protected = {FailureClass.APP_SERVER_PROTOCOL, FailureClass.APP_SERVER_CONTEXT,
            FailureClass.GIT_HOST, FailureClass.EXTERNAL_SERVICE, FailureClass.ENVIRONMENT,
            FailureClass.INFRASTRUCTURE}
        current_value = FailureClass(current)
        return current_value if current_value in protected else proposed

    def _update_recovery_workpad(self, workpad: Any, record: RunRecord,
                                 decision: ReviewerDecision) -> Any:
        marker = "\n\n### Symphony Recovery\n"
        base = workpad.body.split(marker, 1)[0].rstrip()
        attempts = (self._incident_value(record, record.current_incident_id).get("repair_attempts", 0)
                    if record.current_incident_id else 0)
        bullets = lambda values: "\n".join(f"- {item}" for item in values[:8]) or "- None"
        section = (f"{marker}\nCurrent phase: {record.phase}\n\nCurrent incident: {record.current_incident_id or 'none'}\n\n"
            f"Reviewer verdict: {decision.verdict.value}\n\nReviewer diagnosis: {decision.root_cause[:1000]}\n\n"
            f"Current repair plan:\n{bullets(decision.plan)}\n\nRepair history: {attempts} incident repair attempt(s), "
            f"{record.total_repair_attempts} total\n\nVerification:\n{bullets(decision.verification)}\n\n"
            f"Blockers:\n{bullets(decision.stop_conditions)}\n")
        return self.github.update_workpad(workpad.comment_id, base + section)

    @staticmethod
    def _handoff_ready(body: str) -> bool:
        lower = body.lower()
        return "host handoff ready" in lower and "verify-all" in lower and any(word in lower for word in ("passed", "success"))

    def _host_handoff(self, issue: Issue, workspace: Workspace, workpad: Any) -> str:
        files = self.git_lifecycle.changed_files(workspace)
        self.git_lifecycle.validate_changes(workspace, files)
        merged = self.git_lifecycle.synchronize(workspace)
        # The host always verifies the effective final tree, including any merge result.
        self.git_lifecycle.verify(workspace)
        commit = self.git_lifecycle.commit_and_push(workspace, f"fix: {issue.title} (#{issue.number})")
        pr = self.github.pr_for_branch(workspace.branch)
        if not pr:
            pr = self.github.create_pr(workspace.branch, issue.title, f"Closes #{issue.number}\n\nHost-verified Symphony handoff.")
        suffix = (f"\n\nHost handoff: commit `{commit[:12]}` pushed; final verification passed; "
                  f"origin/main synchronization {'changed the tree and was reverified' if merged else 'was current'}. PR: {pr.get('url', '')}\n")
        self.github.update_workpad(workpad.comment_id, workpad.body.rstrip() + suffix)
        self.github.remove_label(issue.number, "symphony")
        self.github.add_label(issue.number, "symphony-review")
        return commit

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
        candidates = sorted(self.github.list_candidates(self.config.required_labels), key=lambda value: value.number)
        self._dependency_state(candidates)
        capacity = self.config.max_concurrent_agents - len(self.running)
        for issue in candidates:
            record = self.store.records.get(issue.number)
            if issue.number in self.running or not record or not record.eligible: continue
            if record and record.retry_at and record.retry_at > time.time(): continue
            if capacity <= 0:
                record.phase, record.status = IssuePhase.WAITING_CAPACITY.value, "waiting_capacity"
                record.waiting_for_active_issue = min(self.running) if self.running else None
                record.queue_reason = "serial capacity is occupied"
                continue
            self.running[issue.number] = asyncio.create_task(self._execute_issue(issue), name=issue.identifier)
            capacity -= 1
        self.store.save()

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
