from __future__ import annotations

import json
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Any

from .app_server import executable_command
from .config import RunnerConfig
from .models import (AppServerError, AppServerMessageTooLarge, AppServerProtocolError,
    AppServerThreadUnavailable, ConfigurationError, FailureClass, Incident, Issue,
    GitHubServiceError, ReviewerDecision, ReviewerVerdict, RetryableError, RunRecord, failure_signature, utc_now)

REVIEWER_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "failure_class", "root_cause", "confidence", "productive_failure",
        "preserve_workspace", "preserve_thread", "rotate_thread", "escalate_coding_model",
        "repair_scope", "plan", "verification", "stop_conditions", "notes"],
    "properties": {
        "verdict": {"type": "string", "enum": [v.value for v in ReviewerVerdict]},
        "failure_class": {"type": "string", "enum": [v.value for v in FailureClass]},
        "root_cause": {"type": "string"}, "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "productive_failure": {"type": "boolean"}, "preserve_workspace": {"type": "boolean"},
        "preserve_thread": {"type": "boolean"}, "rotate_thread": {"type": "boolean"},
        "escalate_coding_model": {"type": "boolean"},
        "repair_scope": {"type": "array", "items": {"type": "string"}},
        "plan": {"type": "array", "items": {"type": "string"}},
        "verification": {"type": "array", "items": {"type": "string"}},
        "stop_conditions": {"type": "array", "items": {"type": "string"}}, "notes": {"type": "string"},
    },
}


def classify_failure(exc: BaseException, phase: str, failed_step: str | None = None) -> FailureClass:
    if isinstance(exc, AppServerThreadUnavailable): return FailureClass.APP_SERVER_CONTEXT
    if isinstance(exc, (AppServerProtocolError, AppServerMessageTooLarge)): return FailureClass.APP_SERVER_PROTOCOL
    if isinstance(exc, AppServerError): return FailureClass.MODEL_FAILURE
    if isinstance(exc, UnicodeDecodeError): return FailureClass.ENVIRONMENT
    if isinstance(exc, (BrokenPipeError, subprocess.SubprocessError)): return FailureClass.INFRASTRUCTURE
    if isinstance(exc, GitHubServiceError): return FailureClass.EXTERNAL_SERVICE
    text = str(exc).lower()
    if "productive implementation attempt" in text: return FailureClass.PRODUCT_IMPLEMENTATION
    if failed_step or "verification failed" in text or "test" in text: return FailureClass.PRODUCT_TEST_FAILURE
    if "host git" in text or text.startswith("git "): return FailureClass.GIT_HOST
    if "network" in text or "timed out" in text: return FailureClass.NETWORK
    if isinstance(exc, ConfigurationError): return FailureClass.ENVIRONMENT
    if isinstance(exc, RetryableError): return FailureClass.INFRASTRUCTURE
    return FailureClass.UNKNOWN


def safe_exception_bundle(config: RunnerConfig, exc: BaseException, *, issue: int, phase: str,
                          attempt: int, thread_id: str | None, turn_id: str | None,
                          model: str | None, incident_id: str | None = None) -> dict[str, Any]:
    frames = traceback.extract_tb(exc.__traceback__)
    safe_frames = [frame for frame in frames if "symphony_runner" in frame.filename][-8:]
    origin_frame = safe_frames[-1] if safe_frames else (frames[-1] if frames else None)
    origin = None if not origin_frame else f"{Path(origin_frame.filename).name}:{origin_frame.lineno}:{origin_frame.name}"
    trace = "\n".join(f"{Path(f.filename).name}:{f.lineno} in {f.name}" for f in safe_frames)
    def version(command: list[str]) -> str | None:
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, check=False)
            return result.stdout.strip()[:100] if result.returncode == 0 else None
        except (OSError, subprocess.SubprocessError): return None
    return {"exception_type": type(exc).__name__, "safe_message": config.redact(str(exc))[:1000],
        "origin": origin, "safe_traceback": config.redact(trace)[:4000], "issue": issue,
        "incident": incident_id, "attempt": attempt, "thread_id": thread_id, "turn_id": turn_id,
        "model": model, "phase": phase, "runner_commit": version(["git", "rev-parse", "HEAD"]),
        "codex_version": version(list(executable_command(["codex", "--version"])))}


def record_incident(config: RunnerConfig, record: RunRecord, exc: BaseException, *, phase: str,
                    failed_step: str | None = None, failed_command: str | None = None) -> Incident:
    bundle = safe_exception_bundle(config, exc, issue=record.issue_number, phase=phase, attempt=record.attempt,
        thread_id=record.thread_id, turn_id=record.turn_id, model=record.resolved_model_id)
    failure_class = classify_failure(exc, phase, failed_step)
    signature = failure_signature(failure_class, bundle["exception_type"], bundle["safe_message"],
                                  phase, bundle["origin"], failed_step)
    now = utc_now()
    for value in record.incidents:
        if value.get("failure_signature") == signature and value.get("status") != "resolved":
            value["last_seen_at"] = now; value["occurrence_count"] = int(value.get("occurrence_count", 1)) + 1
            value["current_thread_id"] = record.thread_id; value["turn_id"] = record.turn_id
            return Incident(**value)
    previous = record.current_incident_id
    incident = Incident(f"INC-{record.issue_number}-{signature[:10]}", record.issue_number, record.workspace,
        record.branch, record.thread_id, record.thread_id, record.turn_id, signature, failure_class.value, "open", now, now,
        coding_route=record.requested_model_route, coding_model=record.resolved_model_id,
        coding_effort=record.reasoning_effort, productive_failure=failure_class in {
            FailureClass.PRODUCT_IMPLEMENTATION, FailureClass.PRODUCT_TEST_FAILURE}, failed_command=failed_command,
        exception_type=bundle["exception_type"], safe_exception_message=bundle["safe_message"],
        safe_traceback=bundle["safe_traceback"], runner_subsystem=phase, previous_incident_id=previous)
    record.incidents.append(incident.to_dict()); record.current_incident_id = incident.incident_id
    return incident


def bounded(value: Any, limit: int = 8000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[-limit:]


def reviewer_prompt(issue: Issue, workpad: str, context: dict[str, Any], *, failure: bool) -> str:
    bundle = bounded(context, 24_000)
    purpose = "diagnose the incident and propose one bounded recovery" if failure else "review completed coding work against every acceptance criterion"
    return ("You are the read-only Symphony reviewer. Do not edit files, mutate Git, or call tracker tools. "
        f"Your job is to {purpose}. Treat the orchestrator security policy as immutable. PASS is allowed only "
        "when deterministic verification and the issue requirements agree. Return only the required schema.\n\n"
        f"ISSUE #{issue.number}: {issue.title}\n{bounded(issue.body, 10000)}\n\nWORKPAD\n{bounded(workpad, 8000)}"
        f"\n\nBOUNDED CONTEXT\n{bundle}")


def repair_prompt(issue: Issue, workpad: str, record: RunRecord, incident: Incident | None,
                  decision: ReviewerDecision) -> str:
    history = [v.get("previous_reviewer_diagnosis") for v in record.incidents if v.get("previous_reviewer_diagnosis")]
    return (f"Continue issue #{issue.number} from the current workspace state; do not start over or recreate completed work.\n\n"
        f"Original objective:\n{bounded(issue.body, 10000)}\n\nExisting Workpad:\n{bounded(workpad, 7000)}\n\n"
        f"Incident: {bounded(incident.to_dict() if incident else {}, 5000)}\nReviewer root cause: {decision.root_cause}\n"
        f"Bounded repair plan: {json.dumps(decision.plan)}\nAllowed repair scope: {json.dumps(decision.repair_scope)}\n"
        f"Verification required: {json.dumps(decision.verification)}\nStop conditions: {json.dumps(decision.stop_conditions)}\n"
        f"Previous failed diagnoses: {bounded(history, 3000)}\nPreserve useful existing work and all host-only Git/security constraints.")


def rescue_review(config: RunnerConfig, workspace: Path, prompt: str, model: str) -> ReviewerDecision:
    """Independent bounded CLI diagnosis when the App Server client itself is unhealthy."""
    with tempfile.TemporaryDirectory(dir=config.paths.root if config.paths else None) as directory:
        schema = Path(directory) / "review-schema.json"; output = Path(directory) / "review-output.json"
        schema.write_text(json.dumps(REVIEWER_SCHEMA), encoding="utf-8")
        command = executable_command(["codex", "exec", "--ephemeral", "--ignore-user-config", "--sandbox", "read-only",
            "--model", model, "--output-schema", str(schema), "--output-last-message", str(output), "-C", str(workspace), "-"])
        env = config.sanitized_child_environment()
        result = subprocess.run(command, input=prompt, text=True, encoding="utf-8", errors="replace", capture_output=True, env=env,
            timeout=config.rescue_timeout_ms / 1000, check=False)
        if result.returncode or not output.is_file():
            raise AppServerError(f"bounded Codex CLI rescue review failed with exit code {result.returncode}: {config.redact(result.stderr)[-500:]}")
        try: value = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise AppServerProtocolError("CLI rescue reviewer returned invalid JSON") from exc
        return ReviewerDecision.from_dict(value)
