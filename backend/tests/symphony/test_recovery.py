import json

import pytest

from tools.symphony_runner.config import RunnerConfig
from tools.symphony_runner.models import (AppServerProtocolError, Confidence, FailureClass, GitHubServiceError,
    ReviewerDecision, ReviewerVerdict, RunRecord, RunnerPaths, failure_signature)
from tools.symphony_runner.recovery import classify_failure, record_incident
from tools.symphony_runner.maintenance import MaintenancePolicy
from tools.symphony_runner.state import StateStore


def config(tmp_path):
    paths = RunnerPaths(tmp_path, tmp_path / "spaces", tmp_path / "logs", tmp_path / "state.json", tmp_path / "lock")
    return RunnerConfig("o/r", "secret", paths=paths)


def test_failure_signature_joins_volatile_manifestations_and_separates_causes():
    first = failure_signature(FailureClass.APP_SERVER_PROTOCOL, "TypeError",
        "thread abc123456789 returned json.loads(None) at 2026-08-16T00:00:00Z", "app_server", "app_server.py:read")
    second = failure_signature(FailureClass.APP_SERVER_PROTOCOL, "TypeError",
        "thread def123456789 returned json.loads(None) at 2026-08-17T00:00:00Z", "app_server", "app_server.py:read")
    other = failure_signature(FailureClass.APP_SERVER_PROTOCOL, "LimitOverrunError",
        "large JSONL reader overflow", "app_server", "app_server.py:read")
    assert first == second and first != other


def test_incident_recurrence_is_durable_and_does_not_count_product_failure(tmp_path):
    cfg = config(tmp_path); record = RunRecord(5, "workspace", "branch", thread_id="thread")
    for _ in range(2):
        try: raise AppServerProtocolError("completed turn contained null agent output")
        except AppServerProtocolError as exc: incident = record_incident(cfg, record, exc, phase="coding")
    assert len(record.incidents) == 1 and record.incidents[0]["occurrence_count"] == 2
    assert incident.failure_class == FailureClass.APP_SERVER_PROTOCOL.value
    assert not incident.productive_failure and record.productive_failure_count == 0
    store = StateStore(tmp_path / "state.json"); store.records[5] = record; store.save()
    assert StateStore(tmp_path / "state.json").load()[5].incidents[0]["incident_id"] == incident.incident_id


def test_reviewer_schema_is_strict_and_never_infers_pass():
    with pytest.raises(AppServerProtocolError): ReviewerDecision.from_dict({"verdict": "PASS"})
    valid = {"verdict": "CHANGES_REQUIRED", "failure_class": "PRODUCT_IMPLEMENTATION",
        "root_cause": "missing requirement", "confidence": "high", "productive_failure": False,
        "preserve_workspace": True, "preserve_thread": True, "rotate_thread": False,
        "escalate_coding_model": False, "repair_scope": ["tools/symphony_runner"],
        "plan": ["fix"], "verification": ["pytest"], "stop_conditions": [], "notes": ""}
    decision = ReviewerDecision.from_dict(valid)
    assert decision.verdict == ReviewerVerdict.CHANGES_REQUIRED and decision.confidence == Confidence.HIGH


def test_deterministic_classification_is_authoritative_for_protocol():
    assert classify_failure(AppServerProtocolError("null result"), "coding") == FailureClass.APP_SERVER_PROTOCOL


def test_maintenance_scope_is_narrow_and_workspace_is_separate(tmp_path):
    MaintenancePolicy.validate_paths(["tools/symphony_runner/app_server.py", "backend/tests/symphony/test_app_server.py", "WORKFLOW.md"])
    with pytest.raises(Exception, match="out-of-scope"): MaintenancePolicy.validate_paths(["backend/fpl_intelligence/api.py"])


def test_unicode_decode_error_is_environment_not_external_service(tmp_path):
    exc = UnicodeDecodeError("utf-8", b"\x9d", 0, 1, "invalid start byte")
    assert classify_failure(exc, "coding") == FailureClass.ENVIRONMENT
    assert classify_failure(exc, "coding") != FailureClass.EXTERNAL_SERVICE
    record = RunRecord(8, "product-workspace", "branch")
    incident = record_incident(config(tmp_path), record, exc, phase="coding")
    assert MaintenancePolicy.eligible(incident, "high")


def test_genuine_github_remote_failure_is_external_service():
    assert classify_failure(GitHubServiceError("GitHub authentication failed"), "coding") == FailureClass.EXTERNAL_SERVICE
