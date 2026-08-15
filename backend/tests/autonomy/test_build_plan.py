# FPL Intelligence System — Build Plan Parser Tests
# Follows: spec/AUTONOMOUS_BUILD_SPEC.md, AGENTS.md, planning/BUILD_PLAN.md

import pytest
from pathlib import Path
from fpl_intelligence.autonomy.build_plan import (
    parse_build_plan,
    validate_graph,
    select_ready_tasks,
    validate_plan_structure,
    validate_verification_entries,
    normalize_verification_entry,
    TASK_HEADING_RE,
)

# Realistic build plan content mirroring planning/BUILD_PLAN.md format:
# each task is delimited by its "## Mxx-Tyy - title" heading; the YAML block
# and the markdown sections below it belong to exactly that task.
test_build_plan_content = """
# FPL Intelligence System — Autonomous Build Plan

## M00-T01 — Normalize repository structure and specification inventory

```yaml
task:
  id: M00-T01
  milestone: M00
  status: BACKLOG
  autonomy_class: A1
  dependencies: []
  spec_refs: []
  system_acceptance_refs: []
  likely_paths: []
```

### Objective

Create the canonical monorepo layout and place all frozen design artifacts in stable locations.

### Detailed instructions

1. Inspect the repository first.
2. Create the canonical directory layout.

### Task acceptance criteria

- [ ] Canonical directories exist.
- [ ] Frozen specs exist once in authoritative locations.

### Verification

```text
git status
git diff --check
PowerShell: Test-Path spec; Test-Path planning/BUILD_PLAN.md
```

## M00-T02 — Implement BUILD_PLAN parser and persistent build state

```yaml
task:
  id: M00-T02
  milestone: M00
  status: BACKLOG
  autonomy_class: A1
  dependencies: [M00-T01]
  spec_refs: []
  system_acceptance_refs: []
  likely_paths: []
```

### Objective

Implement the build plan parser and persistent state.

### Detailed instructions

Follow the instructions in M00-T02.

### Task acceptance criteria

- [ ] Every task block parses.
- [ ] Invalid graph fails before execution.

### Verification

```text
uv run pytest backend/tests/autonomy/test_build_plan.py
uv run python scripts/check_build_plan.py
```
"""


def test_parse_build_plan():
    tasks = parse_build_plan(test_build_plan_content)
    assert len(tasks) == 2
    assert tasks[0]['id'] == 'M00-T01'
    assert tasks[1]['id'] == 'M00-T02'
    assert 'objective' in tasks[0]
    assert 'instructions' in tasks[0]
    assert 'acceptance' in tasks[0]
    assert 'verification' in tasks[0]

    # Test with a file path
    temp_file = Path('temp_build_plan.md')
    with open(temp_file, 'w') as f:
        f.write(test_build_plan_content)

    tasks_from_file = parse_build_plan(temp_file)
    assert len(tasks_from_file) == 2
    assert tasks_from_file[0]['id'] == 'M00-T01'

    # Clean up
    temp_file.unlink()


def test_task_boundaries_no_bleed():
    """Markdown sections must stay inside their own task, not bleed into neighbors."""
    tasks = parse_build_plan(test_build_plan_content)
    assert len(tasks) == 2

    t1 = tasks[0]
    t2 = tasks[1]

    # M00-T01 owns its objective/acceptance/verification
    assert 'monorepo layout' in t1['objective']
    assert t1['objective'] not in t2.get('objective', '')

    assert 'Canonical directories exist.' in t1['acceptance']
    assert 'Canonical directories exist.' not in t2.get('acceptance', [])

    # M00-T02's verification must not contain M00-T01's verification commands
    t1_commands = [e['command'] for e in t1['verification']]
    t2_commands = [e['command'] for e in t2['verification']]
    assert 'git status' in t1_commands
    assert 'git status' not in t2_commands

    # No task heading may appear inside any other task's fields
    validate_plan_structure(tasks)


def test_task_boundaries_with_blank_lines():
    """Extra blank lines between tasks must not break boundary detection."""
    content = (
        "# Plan\n\n"
        "## M00-T01 — First task\n\n"
        "```yaml\ntask:\n  id: M00-T01\n  milestone: M00\n  status: BACKLOG\n  dependencies: []\n  spec_refs: []\n  system_acceptance_refs: []\n```\n\n"
        "### Objective\n\nFirst objective.\n\n"
        "\n\n"
        "\n\n"
        "## M00-T02 — Second task\n\n"
        "```yaml\ntask:\n  id: M00-T02\n  milestone: M00\n  status: BACKLOG\n  dependencies: []\n  spec_refs: []\n  system_acceptance_refs: []\n```\n\n"
        "### Objective\n\nSecond objective.\n"
    )
    tasks = parse_build_plan(content)
    assert len(tasks) == 2
    assert tasks[0]['objective'].strip() == 'First objective.'
    assert tasks[1]['objective'].strip() == 'Second objective.'


def test_acceptance_checkbox_parsing():
    """Markdown checkboxes must be stripped to plain text."""
    content = (
        "# Plan\n\n"
        "## M00-T01 — Checkbox task\n\n"
        "```yaml\ntask:\n  id: M00-T01\n  milestone: M00\n  status: BACKLOG\n  dependencies: []\n  spec_refs: []\n  system_acceptance_refs: []\n```\n\n"
        "### Task acceptance criteria\n\n"
        "- [ ] Canonical directories exist.\n"
        "- [x] Frozen specs exist once.\n"
        "- [X] README links to specs.\n"
        "- [ ]  No leading-space item.\n"
    )
    tasks = parse_build_plan(content)
    acceptance = tasks[0]['acceptance']
    assert acceptance == [
        'Canonical directories exist.',
        'Frozen specs exist once.',
        'README links to specs.',
        'No leading-space item.',
    ]
    assert all('[ ]' not in item and '[x]' not in item for item in acceptance)


def test_verification_normalization():
    """Verification lines normalize into structured command entries."""
    content = (
        "# Plan\n\n"
        "## M00-T01 — Verification task\n\n"
        "```yaml\ntask:\n  id: M00-T01\n  milestone: M00\n  status: BACKLOG\n  dependencies: []\n  spec_refs: []\n  system_acceptance_refs: []\n```\n\n"
        "### Verification\n\n"
        "```text\n"
        "git status\n"
        "PowerShell: Test-Path spec; Test-Path planning/BUILD_PLAN.md\n"
        "uv run python scripts/check_build_plan.py\n"
        "```\n"
    )
    tasks = parse_build_plan(content)
    verification = tasks[0]['verification']
    assert len(verification) == 3

    # default shell, plain command
    assert verification[0] == {'type': 'command', 'shell': 'default', 'command': 'git status'}

    # powershell shell, prefix metadata stripped
    assert verification[1]['type'] == 'command'
    assert verification[1]['shell'] == 'powershell'
    assert verification[1]['command'] == 'Test-Path spec; Test-Path planning/BUILD_PLAN.md'

    assert verification[2]['shell'] == 'default'
    assert verification[2]['command'] == 'uv run python scripts/check_build_plan.py'


def test_normalize_verification_entry_rejects_markdown():
    """Markdown structural text must not be treated as a command."""
    assert normalize_verification_entry('- [ ] some acceptance') is None
    assert normalize_verification_entry('### A heading') is None
    assert normalize_verification_entry('```text') is None
    assert normalize_verification_entry('   ') is None
    assert normalize_verification_entry('## M00-T05 — Another task') is None


def test_validate_verification_entries():
    good = [
        {'type': 'command', 'shell': 'default', 'command': 'git status'},
        {'type': 'command', 'shell': 'powershell', 'command': 'Test-Path spec'},
    ]
    validate_verification_entries(good)  # no exception

    # Empty command
    with pytest.raises(ValueError, match='Empty'):
        validate_verification_entries([{'type': 'command', 'shell': 'default', 'command': '  '}])

    # Unknown shell
    with pytest.raises(ValueError, match='shell'):
        validate_verification_entries([{'type': 'command', 'shell': 'zsh', 'command': 'ls'}])

    # Unsupported entry type
    with pytest.raises(ValueError, match='Unsupported'):
        validate_verification_entries([{'type': 'nope', 'command': 'ls'}])


def test_validate_graph():
    tasks = parse_build_plan(test_build_plan_content)
    assert validate_graph(tasks) is True

    # Test cycle detection (self-dependency)
    tasks_with_self_dep = [dict(t) for t in tasks]
    tasks_with_self_dep[0]['dependencies'] = ['M00-T01']
    with pytest.raises(ValueError, match="depends on itself"):
        validate_graph(tasks_with_self_dep)

    # Test unknown dependency
    tasks_with_unknown_dep = [dict(t) for t in tasks]
    tasks_with_unknown_dep[1]['dependencies'] = ['M00-T99']
    with pytest.raises(ValueError, match="unknown dependency"):
        validate_graph(tasks_with_unknown_dep)

    # Test diamond dependency (should NOT be a cycle)
    diamond_tasks = [
        {'id': 'D1', 'milestone': 'M00', 'status': 'BACKLOG', 'autonomy_class': 'A1', 'dependencies': [], 'spec_refs': [], 'system_acceptance_refs': []},
        {'id': 'D2', 'milestone': 'M00', 'status': 'BACKLOG', 'autonomy_class': 'A1', 'dependencies': ['D1'], 'spec_refs': [], 'system_acceptance_refs': []},
        {'id': 'D3', 'milestone': 'M00', 'status': 'BACKLOG', 'autonomy_class': 'A1', 'dependencies': ['D1'], 'spec_refs': [], 'system_acceptance_refs': []},
        {'id': 'D4', 'milestone': 'M00', 'status': 'BACKLOG', 'autonomy_class': 'A1', 'dependencies': ['D2', 'D3'], 'spec_refs': [], 'system_acceptance_refs': []},
    ]
    assert validate_graph(diamond_tasks) is True

    # Test multi-node cycle
    cycle_tasks = [
        {'id': 'C1', 'milestone': 'M00', 'status': 'BACKLOG', 'autonomy_class': 'A1', 'dependencies': ['C3'], 'spec_refs': [], 'system_acceptance_refs': []},
        {'id': 'C2', 'milestone': 'M00', 'status': 'BACKLOG', 'autonomy_class': 'A1', 'dependencies': ['C1'], 'spec_refs': [], 'system_acceptance_refs': []},
        {'id': 'C3', 'milestone': 'M00', 'status': 'BACKLOG', 'autonomy_class': 'A1', 'dependencies': ['C2'], 'spec_refs': [], 'system_acceptance_refs': []},
    ]
    with pytest.raises(ValueError, match="cycle"):
        validate_graph(cycle_tasks)


def test_select_ready_tasks():
    tasks = parse_build_plan(test_build_plan_content)

    # Create state with M00-T01 completed
    state = {
        'tasks': {
            'M00-T01': {'status': 'COMPLETED', 'attempts': 1},
            'M00-T02': {'status': 'BACKLOG', 'attempts': 0}
        }
    }

    ready_tasks = select_ready_tasks(tasks, state)
    assert len(ready_tasks) == 1
    assert ready_tasks[0]['id'] == 'M00-T02'

    # Test DEFERRED_EXTERNAL
    state['tasks']['M00-T02']['status'] = 'DEFERRED_EXTERNAL'
    ready_tasks = select_ready_tasks(tasks, state)
    assert len(ready_tasks) == 0

    # Test STALLED
    state['tasks']['M00-T02']['status'] = 'STALLED'
    ready_tasks = select_ready_tasks(tasks, state)
    assert len(ready_tasks) == 0

    # Test FAILED_RETRYABLE (should be selected)
    state['tasks']['M00-T02']['status'] = 'FAILED_RETRYABLE'
    ready_tasks = select_ready_tasks(tasks, state)
    assert len(ready_tasks) == 1

    # Test SUPERSEDED
    state['tasks']['M00-T02']['status'] = 'SUPERSEDED'
    ready_tasks = select_ready_tasks(tasks, state)
    assert len(ready_tasks) == 0

    # Test IN_PROGRESS (should not be selected)
    state['tasks']['M00-T02']['status'] = 'IN_PROGRESS'
    ready_tasks = select_ready_tasks(tasks, state)
    assert len(ready_tasks) == 0
