# FPL Intelligence System — Autonomous Task Session Launcher
# Follows: spec/AUTONOMOUS_BUILD_SPEC.md, AGENTS.md, planning/BUILD_PLAN.md

import subprocess
import uuid
import json
import time
from pathlib import Path
from typing import Dict, Optional, List
from ..model_pools import select_model_for_role


class TaskSessionLauncher:
    """Launches real non-interactive OpenCode child sessions for autonomous tasks."""

    def __init__(self, opencode_path: str = "opencode", timeout: int = 900):
        self.opencode_path = opencode_path
        self.timeout = timeout

    def launch_task_session(
        self,
        task: Dict,
        role: str,
        model: Optional[str] = None,
        repo_dir: Optional[str] = None,
    ) -> Dict:
        """Launch a real non-interactive OpenCode session and capture its output.

        Returns a structured result dict with stdout, stderr, exit_code,
        role, model, and timing metadata.
        """
        session_id = str(uuid.uuid4())
        start_time = time.time()

        if model is None:
            model = select_model_for_role(role)

        prompt = self._prepare_task_prompt(task)
        repo_dir = repo_dir or str(Path.cwd())

        command = [
            self.opencode_path,
            "run",
            "--auto",
            f"--agent={role}",
            f"--model={model}",
            "--format=json",
            f"--dir={repo_dir}",
            f"--title={task['id']}: {task.get('title', 'Task')}",
            prompt,
        ]

        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=repo_dir,
            )
            elapsed = time.time() - start_time
            return self._build_result(
                session_id=session_id,
                task_id=task['id'],
                role=role,
                model=model,
                command=command,
                exit_code=process.returncode,
                stdout=process.stdout,
                stderr=process.stderr,
                elapsed=elapsed,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            return self._build_result(
                session_id=session_id,
                task_id=task['id'],
                role=role,
                model=model,
                command=command,
                exit_code=-1,
                stdout="",
                stderr=f"OpenCode session timed out after {self.timeout}s",
                elapsed=elapsed,
                error_class="TIMEOUT",
            )
        except FileNotFoundError:
            elapsed = time.time() - start_time
            return self._build_result(
                session_id=session_id,
                task_id=task['id'],
                role=role,
                model=model,
                command=command,
                exit_code=-1,
                stdout="",
                stderr=f"OpenCode executable not found at: {self.opencode_path}",
                elapsed=elapsed,
                error_class="NOT_FOUND",
            )
        except Exception as e:
            elapsed = time.time() - start_time
            return self._build_result(
                session_id=session_id,
                task_id=task['id'],
                role=role,
                model=model,
                command=command,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                elapsed=elapsed,
                error_class="EXCEPTION",
            )

    def launch_smoke_session(
        self,
        repo_dir: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict:
        """Launch one real bounded OpenCode smoke session for bootstrap validation.

        The smoke prompt asks the child to inspect AGENTS.md and return the
        repository name and a task ID in the required structured format, without
        modifying files.
        """
        smoke_task = {
            'id': 'SMOKE-001',
            'title': 'OpenCode child-session smoke test',
            'milestone': 'M00',
            'autonomy_class': 'A1',
            'dependencies': [],
            'spec_refs': [],
            'system_acceptance_refs': [],
            'objective': (
                'Inspect the repository and return the repository name and task ID '
                'SMOKE-001 in structured format. Do not modify any files.'
            ),
            'instructions': (
                'Read AGENTS.md. Return exactly one JSON object with keys '
                '"repo_name" and "task_id". repo_name should be the name of the '
                'repository from AGENTS.md or the directory. task_id should be '
                '"SMOKE-001". Do not write files.'
            ),
            'acceptance': ['Returns valid JSON with repo_name and task_id'],
            'verification': [],
        }
        return self.launch_task_session(
            smoke_task,
            role='AUTONOMOUS_IMPLEMENTER',
            model=model,
            repo_dir=repo_dir,
        )

    def _prepare_task_prompt(self, task: Dict) -> str:
        """Prepare a structured task prompt for OpenCode."""
        lines = [
            f"You are executing task {task['id']} in the autonomous FPL Intelligence build.",
            "",
            f"## Task: {task['id']}",
            f"Title: {task.get('title', 'Task')}",
            f"Milestone: {task.get('milestone', 'M00')}",
            f"Autonomy Class: {task.get('autonomy_class', 'A1')}",
            "",
            "## Objective",
            task.get('objective', 'Implement the task as specified.'),
            "",
            "## Instructions",
            task.get('instructions', 'Follow the standard implementation workflow described in AGENTS.md.'),
            "",
            "## Acceptance Criteria",
            self._format_list(task.get('acceptance', [])),
            "",
            "## Verification Commands",
            self._format_list(task.get('verification', [])),
            "",
            "## System Acceptance References",
            self._format_list(task.get('system_acceptance_refs', [])),
            "",
            "## Spec References",
            self._format_list(task.get('spec_refs', [])),
            "",
            "Produce the implementation. Run verification commands yourself before declaring completion.",
        ]
        return "\n".join(lines)

    def _format_list(self, items) -> str:
        if not items:
            return "- (none)"
        lines = []
        for item in items:
            if isinstance(item, dict):
                shell = item.get('shell', 'default')
                command = item.get('command', '')
                if shell in ('powershell', 'pwsh'):
                    lines.append(f"- (PowerShell) {command}")
                else:
                    lines.append(f"- {command}")
            else:
                lines.append(f"- {item}")
        return "\n".join(lines)

    def _build_result(
        self,
        session_id: str,
        task_id: str,
        role: str,
        model: str,
        command: List[str],
        exit_code: int,
        stdout: str,
        stderr: str,
        elapsed: float,
        error_class: Optional[str] = None,
    ) -> Dict:
        """Build a structured session result."""
        success = exit_code == 0 and error_class is None
        return {
            'session_id': session_id,
            'task_id': task_id,
            'role': role,
            'model': model,
            'command': ' '.join(command),
            'exit_code': exit_code,
            'stdout': stdout,
            'stderr': stderr,
            'elapsed_seconds': round(elapsed, 2),
            'success': success,
            'error_class': error_class,
        }

    def parse_json_output(self, session_result: Dict) -> Optional[Dict]:
        """Attempt to parse a JSON object from the session stdout.

        OpenCode --format=json produces one JSON event per line.
        We look for the last complete JSON object that contains assistant content.
        """
        if not session_result.get('success') or not session_result.get('stdout'):
            return None

        parsed_obj = None
        for line in session_result['stdout'].splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                parsed_obj = obj
            except json.JSONDecodeError:
                continue

        return parsed_obj


# Singleton instance for application use
task_session_launcher = TaskSessionLauncher()

# Convenience function for direct use
launch_task_session = task_session_launcher.launch_task_session
launch_smoke_session = task_session_launcher.launch_smoke_session
