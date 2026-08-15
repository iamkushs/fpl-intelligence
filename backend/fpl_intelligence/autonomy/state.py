# FPL Intelligence System — Persistent Build State
# Follows: spec/AUTONOMOUS_BUILD_SPEC.md, AGENTS.md, planning/BUILD_PLAN.md

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional


def load_state(state_file: Path) -> Dict:
    """Load persistent state from JSON file."""
    if not state_file.exists():
        return {"tasks": {}, "attempts": {}}

    try:
        with open(state_file, 'r', encoding='utf-8') as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {"tasks": {}, "attempts": {}}


def save_state(state: Dict, state_file: Path) -> None:
    """Save persistent state to JSON file using atomic replacement."""
    state_file.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file first, then rename for atomicity
    temp_file = state_file.with_suffix('.json.tmp')
    with open(temp_file, 'w', encoding='utf-8') as file:
        json.dump(state, file, indent=2)

    # Atomic replacement
    if state_file.exists():
        state_file.unlink()
    temp_file.rename(state_file)


def update_task_status(state: Dict, task_id: str, new_status: str, increment_attempts: bool = False) -> None:
    """Update task status in state.

    Status transitions never count as an attempt by default. The only thing
    that increments the attempt count is a real task launch via
    ``begin_attempt``. Callers that relied on the old behavior must switch to
    ``begin_attempt``.
    """
    if 'tasks' not in state:
        state['tasks'] = {}

    if task_id not in state['tasks']:
        state['tasks'][task_id] = {"status": "BACKLOG", "attempts": 0}

    state['tasks'][task_id]['status'] = new_status

    if increment_attempts:
        state['tasks'][task_id]['attempts'] = state['tasks'][task_id].get('attempts', 0) + 1


def begin_attempt(state: Dict, task_id: str, role: str, model: str, **extra) -> Dict:
    """Start a real task attempt.

    This is the single source of truth for attempt counting: it both appends to
    ``state['attempts'][task_id]`` and syncs the per-task ``attempts`` counter.
    """
    if 'attempts' not in state:
        state['attempts'] = {}

    if task_id not in state['attempts']:
        state['attempts'][task_id] = []

    attempt = {
        'timestamp': time.time(),
        'task_id': task_id,
        'role': role,
        'model': model,
    }
    attempt.update(extra)

    state['attempts'][task_id].append(attempt)
    _sync_attempt_count(state, task_id)
    return attempt


def get_attempt_count(state: Dict, task_id: str) -> int:
    """Return the number of real attempts for a task (single source of truth)."""
    return len(state.get('attempts', {}).get(task_id, []))


def _sync_attempt_count(state: Dict, task_id: str) -> None:
    """Sync the per-task attempts counter with the attempts list."""
    count = len(state.get('attempts', {}).get(task_id, []))
    if 'tasks' in state and task_id in state['tasks']:
        state['tasks'][task_id]['attempts'] = count


def record_attempt(state: Dict, task_id: str, role: str, model: str) -> Dict:
    """Backwards-compatible alias for begin_attempt.

    Deprecated: prefer begin_attempt.
    """
    return begin_attempt(state, task_id, role, model)


def repair_task_attempt_count(state: Dict, task_id: str) -> bool:
    """Repair a task's stored attempts counter to match the attempts list.

    Returns True if a change was made.
    """
    count = len(state.get('attempts', {}).get(task_id, []))
    task_state = state.get('tasks', {}).get(task_id)
    if task_state is None:
        return False
    current = task_state.get('attempts', 0)
    if current != count:
        task_state['attempts'] = count
        return True
    return False


def repair_task_status(state: Dict, task_id: str, new_status: str, reason: str = "") -> bool:
    """Set a task's status to a canonical value and log the repair.

    Returns True if the status changed or a reason was recorded.
    """
    task_state = state.get('tasks', {}).get(task_id)
    if task_state is None:
        return False

    changed = task_state.get('status') != new_status
    task_state['status'] = new_status
    task_state.setdefault('budget', 0)

    _record_repair(state, task_id, reason)
    return changed


def _record_repair(state: Dict, task_id: str, reason: str) -> None:
    """Append a durable repair record to the state file."""
    if 'repairs' not in state:
        state['repairs'] = []
    state['repairs'].append({
        'timestamp': time.time(),
        'task_id': task_id,
        'reason': reason,
        'status_after': state.get('tasks', {}).get(task_id, {}).get('status'),
        'attempts_after': state.get('tasks', {}).get(task_id, {}).get('attempts'),
        'budget': state.get('tasks', {}).get(task_id, {}).get('budget'),
    })


def initialize_state(state: Dict, tasks: list) -> Dict:
    """Initialize state from task list."""
    if 'tasks' not in state:
        state['tasks'] = {}
    if 'attempts' not in state:
        state['attempts'] = {}

    for task in tasks:
        task_id = task.get('id')
        if task_id and task_id not in state['tasks']:
            state['tasks'][task_id] = {
                'status': task.get('status', 'BACKLOG'),
                'attempts': 0
            }

    return state
