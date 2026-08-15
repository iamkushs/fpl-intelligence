# FPL Intelligence System — Build Plan Parser and Graph Validator
# Follows: spec/AUTONOMOUS_BUILD_SPEC.md, AGENTS.md, planning/BUILD_PLAN.md

import re
import yaml
from typing import List, Dict, Set, Optional, Any

# Section headers we associate with tasks
SECTION_HEADERS = {
    'objective': ['### Objective', '## Objective'],
    'instructions': ['### Detailed instructions', '### Detailed Implementation Instructions'],
    'out_of_scope': ['### Explicitly Out of Scope', '### Out of Scope'],
    'acceptance': ['### Task-Level Acceptance Criteria', '### Task acceptance criteria', '### Acceptance Criteria'],
    'verification': ['### Verification'],
    'completion_evidence': ['### Completion Evidence'],
}

# Canonical task states
TASK_STATES = {
    'BACKLOG', 'READY', 'IN_PROGRESS', 'VERIFYING', 'REVIEWING',
    'COMPLETED', 'FAILED_RETRYABLE', 'STALLED', 'DEFERRED_EXTERNAL', 'SUPERSEDED'
}

# Valid autonomy classes
AUTONOMY_CLASSES = {'A0', 'A1', 'A2', 'A3', 'A4'}

REQUIRED_TASK_FIELDS = ['id', 'milestone', 'status', 'dependencies', 'spec_refs']

# Allowed shell prefixes for verification entries
ALLOWED_SHELLS = {'default', 'powershell', 'pwsh', 'bash', 'sh', 'cmd'}

# Task heading pattern: "## M00-T01 — <title>"
TASK_HEADING_RE = re.compile(r'^\s*##\s+(M\d{2}-T\d{2})\b\s*(.*)$')

# Any heading pattern used for section boundaries
ANY_HEADING_RE = re.compile(r'^\s*#{1,6}\s+\S')

# Checkbox pattern: "- [ ] text" or "- [x] text"
CHECKBOX_RE = re.compile(r'^\s*-\s*\[\s*([ xX])\s*\]\s*(.*)$')

# Code fence
CODE_FENCE_RE = re.compile(r'^\s*```')


def parse_build_plan(content) -> List[Dict]:
    """Parse fenced YAML task blocks and markdown sections from BUILD_PLAN.md.

    A task is delimited by its task heading (``## Mxx-Tyy — title``) and ends
    immediately before the next task heading. All markdown sections
    (Objective, Detailed instructions, Acceptance, Verification, ...) are
    associated with exactly the task they appear under.

    Args:
        content: Either a string of markdown content, or a Path to the file.
    """
    if hasattr(content, 'read_text'):
        content = content.read_text(encoding='utf-8', errors='replace')

    segments = _split_task_segments(content)
    tasks = []
    for segment in segments:
        task = _parse_task_segment(segment)
        if task is None:
            continue
        tasks.append(task)

    return tasks


def _split_task_segments(content: str) -> List[Dict]:
    """Split the document into task segments bounded by task headings."""
    lines = content.split('\n')
    segments = []
    current = None
    for i, line in enumerate(lines):
        match = TASK_HEADING_RE.match(line)
        if match:
            if current is not None:
                current['end'] = i
                segments.append(current)
            current = {
                'id': match.group(1),
                'title': match.group(2).strip().lstrip('—–-').strip(),
                'lines': lines[i + 1:],
                'start': i,
                'end': len(lines),
            }
        elif current is not None:
            # Do nothing; line belongs to current segment
            pass

    if current is not None:
        current['end'] = len(lines)
        segments.append(current)

    return segments


def _parse_task_segment(segment: Dict) -> Optional[Dict]:
    """Parse YAML task metadata and markdown sections from one segment."""
    content_lines = segment.get('lines', [])
    yaml_block = _extract_first_yaml_block(content_lines)
    if yaml_block is None:
        return None

    task = parse_task_yaml(yaml_block['yaml'])
    if task is None:
        return None

    task['id'] = task.get('id') or segment['id']
    task['title'] = segment.get('title', task.get('title', ''))
    task['milestone'] = task.get('milestone') or _milestone_from_id(task['id'])

    # Extract markdown sections bounded to this segment
    section_lines = content_lines[:yaml_block['start']] + content_lines[yaml_block['end'] + 1:]
    sections = extract_task_sections_from_lines(section_lines)
    for key, value in sections.items():
        task[key] = value

    return task


def _milestone_from_id(task_id: str) -> str:
    m = re.match(r'^(M\d{2})-', task_id)
    return m.group(1) if m else ''


def extract_fenced_yaml_blocks(content: str) -> List[Dict]:
    """Extract fenced YAML blocks with their line positions (kept for compatibility)."""
    blocks = []
    lines = content.split('\n')
    in_block = False
    block_start = 0
    block_content = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('```yaml'):
            in_block = True
            block_start = i
            block_content = []
        elif in_block and stripped.startswith('```'):
            blocks.append({
                'start': block_start,
                'end': i,
                'yaml': '\n'.join(block_content),
            })
            in_block = False
        elif in_block:
            block_content.append(line)

    return blocks


def _extract_first_yaml_block(lines: List[str]) -> Optional[Dict]:
    """Extract the first fenced YAML block from a list of lines."""
    in_block = False
    block_start = 0
    block_content = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('```yaml'):
            in_block = True
            block_start = i
            block_content = []
        elif in_block and stripped.startswith('```'):
            return {'start': block_start, 'end': i, 'yaml': '\n'.join(block_content)}
        elif in_block:
            block_content.append(line)
    return None


def parse_task_yaml(yaml_str: str) -> Optional[Dict]:
    """Parse a single task YAML block."""
    try:
        parsed = yaml.safe_load(yaml_str)
        if not isinstance(parsed, dict):
            return None
        task = parsed.get('task')
        if not isinstance(task, dict):
            return None
        return task
    except yaml.YAMLError:
        return None


def extract_task_sections_from_lines(lines: List[str]) -> Dict:
    """Extract markdown sections from a task's own lines (bounded by segment)."""
    sections = {}
    for section_key, headers in SECTION_HEADERS.items():
        header_idx = None
        for i, line in enumerate(lines):
            if line.strip() in headers:
                header_idx = i
                break
        if header_idx is None:
            continue

        content_lines = []
        for i in range(header_idx + 1, len(lines)):
            stripped = lines[i].strip()
            if not stripped:
                if content_lines and not content_lines[-1].strip():
                    continue
                content_lines.append('')
                continue
            if ANY_HEADING_RE.match(lines[i]):
                break
            content_lines.append(lines[i])

        processed = process_section_content(section_key, content_lines)
        if processed:
            sections[section_key] = processed

    return sections


def extract_task_sections(content: str, task_start: int, task_end: int) -> Dict:
    """Compatibility wrapper that extracts sections between two line indices."""
    lines = content.split('\n')
    segment_lines = lines[task_start:task_end]
    return extract_task_sections_from_lines(segment_lines)


def process_section_content(section_key: str, lines: List[str]) -> Any:
    """Process section content based on its type."""
    # Remove leading/trailing blank lines
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]

    if not lines:
        return None

    if section_key in ('acceptance', 'out_of_scope', 'completion_evidence'):
        return _parse_checkbox_items(lines)

    if section_key == 'verification':
        entries = _parse_verification_lines(lines)
        return entries if entries else None

    # objective / instructions: join text lines
    text = '\n'.join(line.rstrip() for line in lines).strip()
    return text if text else None


def _parse_checkbox_items(lines: List[str]) -> List[str]:
    """Parse bullet/checkbox lines into clean text items."""
    items = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('```'):
            continue
        match = CHECKBOX_RE.match(stripped)
        if match:
            items.append(match.group(2).strip())
            continue
        if stripped.startswith('-'):
            items.append(stripped[1:].strip())
            continue
        items.append(stripped)
    return [item for item in items if item]


def _parse_verification_lines(lines: List[str]) -> List[Dict]:
    """Parse verification section lines into structured command entries.

    Handles ```text fenced blocks containing the actual commands. Each command
    is normalized into: {"type": "command", "shell": <shell>, "command": <cmd>}.
    A "PowerShell:" prefix is metadata, not part of the command.
    """
    inner = _extract_fence_inner(lines)
    entries = []
    for line in inner:
        entry = normalize_verification_entry(line)
        if entry is not None:
            entries.append(entry)
    return entries


def _extract_fence_inner(lines: List[str]) -> List[str]:
    """Extract the inner lines of the first code fence, or return the raw lines."""
    # If the section is a single fenced block, return only its inner lines
    in_fence = False
    inner = []
    fenced_found = False
    for line in lines:
        stripped = line.strip()
        if CODE_FENCE_RE.match(stripped):
            if not in_fence:
                in_fence = True
                fenced_found = True
            else:
                in_fence = False
            continue
        if in_fence:
            inner.append(line)

    if fenced_found:
        # Only keep lines that were inside a fence
        return [line for line in inner if line.strip()]

    # No fence: treat each non-empty line as a command
    return [line for line in lines if line.strip()]


def normalize_verification_entry(line: str) -> Optional[Dict]:
    """Normalize one verification command line into a structured entry."""
    text = line.strip()
    if not text:
        return None

    if text.startswith('```') or text.startswith('#'):
        return None

    if ANY_HEADING_RE.match(text):
        return None

    if CHECKBOX_RE.match(text):
        return None

    # Detect a language prefix such as "PowerShell: cmd"
    shell = 'default'
    command = text
    prefix_match = re.match(r'^([A-Za-z]+):\s*(.*)$', text)
    if prefix_match:
        candidate = prefix_match.group(1).lower()
        if candidate in ('powershell', 'pwsh', 'bash', 'sh', 'cmd'):
            shell = candidate
            command = prefix_match.group(2).strip()
        else:
            # Unknown prefix: treat whole line as command (not valid shell metadata)
            return None

    if not command:
        return None

    return {'type': 'command', 'shell': shell, 'command': command}


def validate_verification_entries(verification: List) -> None:
    """Validate parsed verification entries; raise ValueError on corruption."""
    for entry in verification:
        if isinstance(entry, str):
            if not entry.strip():
                raise ValueError("Empty accidental verification command")
            continue

        if not isinstance(entry, dict):
            raise ValueError(f"Malformed verification entry: {entry!r}")

        if entry.get('type') != 'command':
            raise ValueError(f"Unsupported verification entry type: {entry!r}")

        shell = entry.get('shell')
        if shell not in ALLOWED_SHELLS:
            raise ValueError(f"Unsupported execution prefix/shell: {shell!r}")

        command = entry.get('command', '').strip()
        if not command:
            raise ValueError("Empty accidental verification command")

        if TASK_HEADING_RE.match(command):
            raise ValueError(f"Another task heading inside verification: {command!r}")

        if re.search(r'\bM\d{2}-T\d{2}\b', command) and command.startswith('##'):
            raise ValueError(f"Unparsed task heading in verification: {command!r}")


def validate_plan_structure(tasks: List[Dict]) -> None:
    """Validate all parsed tasks for structural integrity (no cross-task bleed)."""
    for task in tasks:
        task_id = task.get('id', '?')
        for field in ('objective', 'instructions', 'acceptance', 'verification'):
            value = task.get(field)
            if value is None:
                continue
            if isinstance(value, list):
                blob = '\n'.join(_entry_text(v) for v in value)
            else:
                blob = str(value)

            # No task heading may appear inside a task's own fields
            for heading_match in TASK_HEADING_RE.finditer(blob):
                heading_id = heading_match.group(1)
                if heading_id != task_id:
                    raise ValueError(
                        f"Task {task_id} field '{field}' contains another task heading ({heading_id})"
                    )

        if 'verification' in task:
            validate_verification_entries(task['verification'])


def _entry_text(entry: Any) -> str:
    if isinstance(entry, dict):
        return entry.get('command', '')
    return str(entry)


def validate_graph(tasks: List[Dict]) -> bool:
    """Validate the task dependency graph."""
    if not tasks:
        raise ValueError("No tasks provided")

    task_ids = set()

    for task in tasks:
        task_id = task.get('id')
        if not task_id:
            raise ValueError(f"Task missing required field 'id': {task}")

        if task_id in task_ids:
            raise ValueError(f"Duplicate task ID: {task_id}")
        task_ids.add(task_id)

        for field in REQUIRED_TASK_FIELDS:
            if field not in task:
                raise ValueError(f"Task {task_id} missing required field: {field}")

        if not task.get('milestone'):
            raise ValueError(f"Task {task_id} missing milestone")

        autonomy_class = task.get('autonomy_class', 'A1')
        if autonomy_class not in AUTONOMY_CLASSES:
            raise ValueError(f"Task {task_id} has invalid autonomy class: {autonomy_class}")

        status = task.get('status')
        if status not in TASK_STATES:
            raise ValueError(f"Task {task_id} has invalid status: {status}")

        dependencies = task.get('dependencies', [])
        if not isinstance(dependencies, list):
            raise ValueError(f"Task {task_id} dependencies must be a list")

        for dep in dependencies:
            if dep == task_id:
                raise ValueError(f"Task {task_id} depends on itself")

    for task in tasks:
        task_id = task.get('id')
        for dep in task.get('dependencies', []):
            if dep not in task_ids:
                raise ValueError(f"Task {task_id} has unknown dependency: {dep}")

    detect_cycles(tasks, task_ids)
    return True


def detect_cycles(tasks: List[Dict], task_ids: Set[str]) -> None:
    """Detect cycles in the task dependency graph using DFS."""
    graph = {task_id: [] for task_id in task_ids}
    for task in tasks:
        graph[task['id']] = task.get('dependencies', [])

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}

    def dfs(node, path):
        color[node] = GRAY
        path.append(node)
        for neighbor in graph.get(node, []):
            if color[neighbor] == GRAY:
                cycle_start = path.index(neighbor) if neighbor in path else 0
                cycle = path[cycle_start:] + [neighbor]
                raise ValueError(f"Dependency cycle detected: {' -> '.join(cycle)}")
            if color[neighbor] == WHITE:
                dfs(neighbor, path)
        path.pop()
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            dfs(node, [])


def select_ready_tasks(tasks: List[Dict], state: Dict = None) -> List[Dict]:
    """Select tasks that are ready to execute."""
    if state is None:
        state = {'tasks': {}}

    task_states = state.get('tasks', {})

    completed_tasks = set()
    for task_id, task_state in task_states.items():
        status = task_state.get('status', '')
        if status in ('COMPLETED', 'SUPERSEDED'):
            completed_tasks.add(task_id)

    ready_tasks = []
    for task in tasks:
        task_id = task['id']
        current_status = task_states.get(task_id, {}).get('status', task.get('status', 'BACKLOG'))

        if current_status in ('COMPLETED', 'SUPERSEDED'):
            continue

        if current_status in ('STALLED', 'DEFERRED_EXTERNAL'):
            continue

        if current_status in ('IN_PROGRESS', 'VERIFYING', 'REVIEWING'):
            continue

        dependencies_satisfied = all(
            dep in completed_tasks for dep in task.get('dependencies', [])
        )

        if dependencies_satisfied:
            ready_tasks.append(task)

    return ready_tasks
