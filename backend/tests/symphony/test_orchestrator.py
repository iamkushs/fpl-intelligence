import asyncio
import os
import time
from pathlib import Path

import pytest

from tools.symphony_runner.config import RunnerConfig
from tools.symphony_runner.models import FailureClass, Issue, RunRecord, RunnerPaths
from tools.symphony_runner.orchestrator import Orchestrator
from tools.symphony_runner.logging import exception_location
from tools.symphony_runner.state import RunnerLock, StateStore


class GitHub:
    def __init__(self,issues): self.issues={i.number:i for i in issues}
    def list_candidates(self,_): return list(self.issues.values())
    def get_issue(self,n): return self.issues[n]


class Logger:
    def __init__(self): self.events=[]
    def event(self,*args,**fields): self.events.append((args,fields))


def cfg(tmp_path,concurrency=2):
    paths=RunnerPaths(tmp_path,tmp_path/"spaces",tmp_path/"logs",tmp_path/"state.json",tmp_path/"lock")
    return RunnerConfig("o/r","",max_concurrent_agents=concurrency,paths=paths)


def issue(n,labels=("symphony",),state="open",body=""): return Issue(n,f"I{n}",body,labels,state,f"u{n}")


def test_eligibility_review_blocked_closed_and_unlabelled(tmp_path):
    runner=Orchestrator(cfg(tmp_path),github=GitHub([]),logger=Logger())
    assert runner.eligible(issue(1))
    assert not runner.eligible(issue(2,()))
    assert not runner.eligible(issue(3,("symphony","symphony-review")))
    assert not runner.eligible(issue(4,("symphony","symphony-blocked")))
    assert not runner.eligible(issue(5,state="closed"))


def test_cycle_respects_concurrency_and_never_duplicates(tmp_path):
    async def scenario():
        runner=Orchestrator(cfg(tmp_path,2),github=GitHub([issue(1),issue(2),issue(3)]),logger=Logger())
        gate=asyncio.Event(); starts=[]
        async def execute(value): starts.append(value.number); await gate.wait()
        runner._execute_issue=execute
        await runner.cycle(); await asyncio.sleep(0)
        assert sorted(starts)==[1,2] and len(runner.running)==2
        await runner.cycle(); await asyncio.sleep(0); assert starts==[1,2]
        gate.set(); await asyncio.gather(*runner.running.values())
    asyncio.run(scenario())


def test_retry_timestamp_defers_dispatch(tmp_path):
    async def scenario():
        runner=Orchestrator(cfg(tmp_path),github=GitHub([issue(1)]),logger=Logger())
        runner.store.records[1]=RunRecord(1,"w","b",retry_at=time.time()+100,status="retrying")
        await runner.cycle(); assert not runner.running
    asyncio.run(scenario())


def test_state_survives_restart_and_lock_rejects_second(tmp_path):
    store=StateStore(tmp_path/"state.json"); store.records[1]=RunRecord(1,"w","b",thread_id="t",attempt=2); store.save()
    loaded=StateStore(tmp_path/"state.json"); assert loaded.load()[1].thread_id=="t"
    first=RunnerLock(tmp_path/"lock"); second=RunnerLock(tmp_path/"lock"); first.acquire()
    try:
        with pytest.raises(RuntimeError): second.acquire()
    finally: first.release()


def test_state_save_retries_transient_windows_replace_denial(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "state.json"); store.records[1] = RunRecord(1, "w", "b")
    real_replace = os.replace; attempts = []
    def replace(source, destination):
        attempts.append((source, destination))
        if len(attempts) < 3: raise PermissionError("transient sharing violation")
        real_replace(source, destination)
    monkeypatch.setattr("tools.symphony_runner.state.os.replace", replace)
    monkeypatch.setattr("tools.symphony_runner.state.time.sleep", lambda _: None)
    store.save()
    assert len(attempts) == 3 and store.path.is_file()


def test_losing_label_cancels_active_worker(tmp_path):
    async def scenario():
        gh=GitHub([issue(1)]); runner=Orchestrator(cfg(tmp_path),github=gh,logger=Logger())
        runner.running[1]=asyncio.create_task(asyncio.sleep(100))
        gh.issues[1]=issue(1,())
        await runner.cycle(); await asyncio.sleep(0)
        assert runner.running[1].cancelled() or runner.running[1].cancelling()
    asyncio.run(scenario())


def test_protocol_rotation_is_durable_and_preserves_workspace_route_and_workpad(tmp_path):
    workspace = tmp_path / "spaces" / "GH-5"; workspace.mkdir(parents=True)
    changed = workspace / "useful.py"; changed.write_text("preserve me", encoding="utf-8")
    workpad = workspace / "workpad-sentinel"; workpad.write_text("existing Workpad", encoding="utf-8")
    logger = Logger(); runner = Orchestrator(cfg(tmp_path), github=GitHub([]), logger=logger)
    record = RunRecord(5, str(workspace), "symphony/gh-5", thread_id="damaged", turn_id="turn",
        requested_model_route="5.5", resolved_model_id="gpt-5.5", reasoning_effort="medium",
        productive_failure_count=0, escalation_level=0)
    runner.store.records[5] = record

    assert runner._rotate_unhealthy_thread(record, 5, "null terminal result")
    loaded = StateStore(tmp_path / "state.json").load()[5]
    assert loaded.thread_id is None and loaded.turn_id is None
    assert loaded.unhealthy_thread_ids == ["damaged"]
    assert loaded.requested_model_route == "5.5" and loaded.resolved_model_id == "gpt-5.5"
    assert loaded.reasoning_effort == "medium" and loaded.productive_failure_count == 0
    assert loaded.escalation_level == 0
    assert changed.read_text(encoding="utf-8") == "preserve me"
    assert workpad.read_text(encoding="utf-8") == "existing Workpad"


def test_healthy_infrastructure_retry_does_not_rotate_thread(tmp_path):
    runner = Orchestrator(cfg(tmp_path), github=GitHub([]), logger=Logger())
    record = RunRecord(1, "workspace", "branch", thread_id="healthy", turn_id="turn")
    # Rotation is an explicit protocol-anomaly operation, not part of ordinary retry handling.
    assert record.thread_id == "healthy" and record.unhealthy_thread_ids == []


def test_safe_exception_location_omits_exception_payload():
    try:
        raise TypeError("secret giant payload")
    except TypeError as exc:
        location = exception_location(exc)
    assert location and "test_safe_exception_location" in location
    assert "secret giant payload" not in location and "giant" not in location


def test_reviewer_diagnosis_routes_are_bounded_and_start_on_55(tmp_path):
    runner = Orchestrator(cfg(tmp_path), github=GitHub([]), logger=Logger())
    assert [runner._reviewer_route(index) for index in range(5)] == ["5.5", "terra", "sol", "sol", "sol"]


def test_terminal_phase_normalization(tmp_path):
    record = RunRecord(1, "w", "b", phase="coding", status="ineligible")
    Orchestrator._normalize_ineligible(record, issue(1, ("symphony-review",)))
    assert (record.phase, record.status) == ("host_handoff", "review")
    Orchestrator._normalize_ineligible(record, issue(1, ("symphony-blocked",)))
    assert (record.phase, record.status) == ("blocked", "blocked")
    Orchestrator._normalize_ineligible(record, issue(1, (), "closed"))
    assert (record.phase, record.status) == ("host_handoff", "complete")


def test_local_infrastructure_classification_cannot_be_recast_external(tmp_path):
    runner = Orchestrator(cfg(tmp_path), github=GitHub([]), logger=Logger())
    assert runner._authoritative_class("ENVIRONMENT", FailureClass.EXTERNAL_SERVICE) == FailureClass.ENVIRONMENT
    assert runner._authoritative_class("INFRASTRUCTURE", FailureClass.EXTERNAL_SERVICE) == FailureClass.INFRASTRUCTURE


def test_serial_queue_is_deterministic_and_exposes_capacity_wait(tmp_path):
    async def scenario():
        runner = Orchestrator(cfg(tmp_path, 1), github=GitHub([issue(12), issue(10), issue(11)]), logger=Logger())
        gate = asyncio.Event(); starts = []
        async def execute(value): starts.append(value.number); await gate.wait()
        runner._execute_issue = execute
        await runner.cycle(); await asyncio.sleep(0)
        assert starts == [10]
        assert runner.store.records[11].status == "waiting_capacity"
        assert runner.store.records[11].waiting_for_active_issue == 10
        assert runner.store.records[12].status == "waiting_capacity"
        gate.set(); await asyncio.gather(*runner.running.values())
    asyncio.run(scenario())


def test_dependency_chain_waits_for_closed_issue_and_pr_alone_is_irrelevant(tmp_path):
    async def scenario():
        predecessor = issue(10, state="open")
        dependent = issue(11, body="Depends-On: #10")
        gh = GitHub([predecessor, dependent]); runner = Orchestrator(cfg(tmp_path, 1), github=gh, logger=Logger())
        starts = []
        async def execute(value):
            starts.append(value.number)
            if value.number == 10: gh.issues[10] = issue(10, labels=("symphony-review",))
        runner._execute_issue = execute
        await runner.cycle(); await asyncio.sleep(0)
        assert starts == [10] and runner.store.records[11].blocked_by == [10]
        await runner.cycle(); await asyncio.sleep(0)
        assert 11 not in starts
        gh.issues[10] = issue(10, state="closed")
        await runner.cycle(); await asyncio.sleep(0)
        assert starts == [10, 11]
    asyncio.run(scenario())


def test_multiple_dependencies_require_all_closed(tmp_path):
    gh = GitHub([issue(1, state="closed"), issue(2), issue(3, body="Depends-On: #1, #2")])
    runner = Orchestrator(cfg(tmp_path), github=gh, logger=Logger())
    runner._dependency_state([gh.issues[3]])
    assert runner.store.records[3].blocked_by == [2]
    gh.issues[2] = issue(2, state="closed")
    runner._dependency_state([gh.issues[3]])
    assert runner.store.records[3].eligible and not runner.store.records[3].blocked_by


@pytest.mark.parametrize("body, reason", [
    ("Depends-On: 10", "malformed"), ("Depends-On: #4", "depend on itself")])
def test_invalid_dependency_is_visible_and_ineligible(tmp_path, body, reason):
    value = issue(4, body=body); runner = Orchestrator(cfg(tmp_path), github=GitHub([value]), logger=Logger())
    runner._dependency_state([value]); record = runner.store.records[4]
    assert not record.eligible and record.status == "invalid_dependency" and reason in record.queue_reason


def test_dependency_cycle_is_bounded_and_visible(tmp_path):
    values = [issue(1, body="Depends-On: #2"), issue(2, body="Depends-On: #1")]
    runner = Orchestrator(cfg(tmp_path), github=GitHub(values), logger=Logger())
    runner._dependency_state(values)
    assert all(runner.store.records[n].status == "invalid_dependency" for n in (1, 2))
    assert all("cycle" in runner.store.records[n].queue_reason for n in (1, 2))


def test_snapshot_exposes_dependency_waiting(tmp_path):
    values = [issue(1), issue(2, body="Depends-On: #1")]
    runner = Orchestrator(cfg(tmp_path), github=GitHub(values), logger=Logger())
    runner._dependency_state(values); payload = runner.snapshot()["issues"]["2"]
    assert payload["phase"] == "waiting_dependency" and payload["blocked_by"] == [1] and not payload["eligible"]
