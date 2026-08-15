# FPL Intelligence System â€” Autonomous State Tests
# Follows: spec/AUTONOMOUS_BUILD_SPEC.md, AGENTS.md, planning/BUILD_PLAN.md

import pytest
import copy
from pathlib import Path
from fpl_intelligence.autonomy.state import (
    load_state,
    save_state,
    update_task_status,
    record_attempt,
    begin_attempt,
    get_attempt_count,
    repair_task_attempt_count,
    initialize_state,
)

# Mock data for testing
test_state = {
    "tasks": {
        "M00-T01": {
            "status": "BACKLOG",
            "attempts": 0
        },
        "M00-T02": {
            "status": "BACKLOG",
            "attempts": 0
        }
    },
    "attempts": {}
}

def test_load_save_state(tmp_path):
    state_file = tmp_path / "state.json"
    save_state(test_state, state_file)
    loaded_state = load_state(state_file)
    assert loaded_state == test_state

    # Test missing file
    missing_file = tmp_path / "missing.json"
    loaded_state = load_state(missing_file)
    assert loaded_state == {"tasks": {}, "attempts": {}}

def test_update_task_status():
    state = copy.deepcopy(test_state)
    update_task_status(state, "M00-T01", "IN_PROGRESS")
    assert state["tasks"]["M00-T01"]["status"] == "IN_PROGRESS"
    # Status transitions must NOT count as attempts
    assert state["tasks"]["M00-T01"]["attempts"] == 0

    # Multiple transitions still must not increment attempts
    update_task_status(state, "M00-T01", "VERIFYING")
    update_task_status(state, "M00-T01", "FAILED_RETRYABLE")
    update_task_status(state, "M00-T01", "IN_PROGRESS")
    assert state["tasks"]["M00-T01"]["attempts"] == 0

    # Test new task
    update_task_status(state, "M00-T03", "BACKLOG")
    assert state["tasks"]["M00-T03"]["status"] == "BACKLOG"

def test_begin_attempt_counts_once():
    state = copy.deepcopy(test_state)
    attempt = begin_attempt(state, "M00-T01", "AUTONOMOUS_IMPLEMENTER", "nvidia/qwen/qwen3-coder-480b-a35b-instruct")
    assert attempt['task_id'] == 'M00-T01'
    assert attempt['role'] == 'AUTONOMOUS_IMPLEMENTER'
    assert attempt['model'] == 'nvidia/qwen/qwen3-coder-480b-a35b-instruct'
    assert 'attempts' in state
    assert len(state['attempts']['M00-T01']) == 1
    # Per-task counter must be synced to the list (single source of truth)
    assert state['tasks']['M00-T01']['attempts'] == 1
    assert get_attempt_count(state, "M00-T01") == 1

    # Status transitions after a launch must not add attempts
    update_task_status(state, "M00-T01", "IN_PROGRESS")
    update_task_status(state, "M00-T01", "VERIFYING")
    update_task_status(state, "M00-T01", "FAILED_RETRYABLE")
    assert get_attempt_count(state, "M00-T01") == 1
    assert state['tasks']['M00-T01']['attempts'] == 1

    # A second real launch is the only thing that increments
    begin_attempt(state, "M00-T01", "AUTONOMOUS_IMPLEMENTER", "auto/best-coding")
    assert get_attempt_count(state, "M00-T01") == 2
    assert state['tasks']['M00-T01']['attempts'] == 2

def test_record_attempt():
    state = copy.deepcopy(test_state)
    attempt = record_attempt(state, "M00-T01", "AUTONOMOUS_IMPLEMENTER", "nvidia/qwen/qwen3-coder-480b-a35b-instruct")
    assert attempt['task_id'] == 'M00-T01'
    assert attempt['role'] == 'AUTONOMOUS_IMPLEMENTER'
    assert attempt['model'] == 'nvidia/qwen/qwen3-coder-480b-a35b-instruct'
    assert 'attempts' in state
    assert len(state['attempts']['M00-T01']) == 1
    assert state['tasks']['M00-T01']['attempts'] == 1

def test_repair_task_attempt_count():
    # State drifted: task says 3 attempts, but only 1 real attempt logged
    state = {
        'tasks': {
            'M00-T01': {'status': 'STALLED', 'attempts': 3}
        },
        'attempts': {
            'M00-T01': [
                {'task_id': 'M00-T01', 'timestamp': 1786207711.12, 'role': 'AUTONOMOUS_IMPLEMENTER', 'model': 'auto/best-coding'}
            ]
        }
    }
    changed = repair_task_attempt_count(state, "M00-T01")
    assert changed is True
    assert state['tasks']['M00-T01']['attempts'] == 1
    assert get_attempt_count(state, "M00-T01") == 1

    # Second repair is a no-op
    assert repair_task_attempt_count(state, "M00-T01") is False

def test_initialize_state():
    tasks = [
        {'id': 'M00-T01', 'status': 'BACKLOG'},
        {'id': 'M00-T02', 'status': 'BACKLOG'},
    ]
    state = initialize_state({}, tasks)
    assert 'tasks' in state
    assert 'M00-T01' in state['tasks']
    assert state['tasks']['M00-T01']['status'] == 'BACKLOG'
    assert 'attempts' in state
