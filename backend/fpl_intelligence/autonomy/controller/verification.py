# FPL Intelligence System — Autonomous Verification Runner
# Follows: spec/AUTONOMOUS_BUILD_SPEC.md, AGENTS.md, planning/BUILD_PLAN.md

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pathlib import Path

POWERSHELL = "powershell"


def robustify_powershell_command(command: str) -> str:
    """Convert bare PowerShell boolean checks (e.g. `Test-Path x`) into
    explicit commands that exit non-zero when the check is false.

    `Test-Path` prints True/False but never sets a failing exit code, so a
    bare call would silently pass. This transforms:
        Test-Path spec; Test-Path planning/BUILD_PLAN.md
    into an equivalent that exits 1 on the first missing path.
    """
    parts = [part.strip() for part in command.split(';')]
    wrapped = []
    for part in parts:
        if not part:
            continue
        match = re.match(r'^Test-Path\s+(.+)$', part)
        if match:
            path = match.group(1).strip().strip('"\'').strip()
            wrapped.append(
                "if (-not (Test-Path '{0}')) {{ Write-Error 'Path not found: {0}'; exit 1 }}".format(path)
            )
        else:
            wrapped.append(part)
    return "; ".join(wrapped)


class VerificationRunner:
    def __init__(self, timeout: int = 300, evidence_dir: Optional[Path] = None):
        self.timeout = timeout
        self.evidence_dir = evidence_dir

    def run_verification(self, task: Dict, evidence_dir: Optional[Path] = None) -> Dict:
        """Run task verification commands.

        Each entry may be a plain string or a structured dict of the form
        ``{"type": "command", "shell": <shell>, "command": <cmd>}`` as produced
        by the BUILD_PLAN parser. PowerShell entries are made explicit so that
        boolean checks (Test-Path) fail loudly instead of silently passing.

        Results are persisted as evidence to ``evidence_dir`` when provided
        (or ``self.evidence_dir``).
        """
        verification_commands = task.get('verification', [])
        if not verification_commands:
            result = {
                'success': True,
                'message': "No verification commands provided",
                'commands': []
            }
            self._persist_evidence(task, result, evidence_dir)
            return result

        results = []
        all_success = True

        for cmd in verification_commands:
            result = self._run_command(cmd)
            results.append(result)
            all_success = all_success and result['success']

        result = {
            'success': all_success,
            'commands': results
        }
        self._persist_evidence(task, result, evidence_dir)
        return result

    def _run_command(self, entry) -> Dict:
        """Run a single verification entry with timeout."""
        if isinstance(entry, str):
            entry = {'type': 'command', 'shell': 'default', 'command': entry}

        if not isinstance(entry, dict) or entry.get('type') != 'command':
            return {
                'command': str(entry),
                'success': False,
                'exit_code': -1,
                'stdout': "",
                'stderr': f"Malformed verification entry: {entry!r}",
                'duration': 0.0,
                'message': "Malformed verification entry"
            }

        shell = entry.get('shell', 'default')
        command = entry.get('command', '')
        start_time = time.time()

        if shell in ('powershell', 'pwsh'):
            return self._run_powershell(command, start_time)
        return self._run_shell_command(command, start_time)

    def _run_powershell(self, command: str, start_time: float) -> Dict:
        """Run a command under PowerShell with explicit boolean handling."""
        executable = 'powershell'
        full_command = robustify_powershell_command(command)
        invocation = [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command", full_command,
        ]
        try:
            process = subprocess.Popen(
                invocation,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors='replace',
            )
        except FileNotFoundError:
            return self._make_result(command, False, -1, "", "PowerShell not available",
                                     time.time() - start_time, "PowerShell not found")
        except Exception as e:
            return self._make_result(command, False, -1, "", str(e),
                                     time.time() - start_time, "Error launching PowerShell")

        try:
            stdout, stderr = process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return self._make_result(command, False, -1, stdout, stderr,
                                     time.time() - start_time,
                                     f"Command timed out after {self.timeout} seconds")

        success = process.returncode == 0
        message = "Command completed" if success else f"Exit code {process.returncode}"
        return self._make_result(command, success, process.returncode, stdout, stderr,
                                 time.time() - start_time, message)

    def _run_shell_command(self, command: str, start_time: float) -> Dict:
        """Run a command via the default shell (cmd on Windows)."""
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors='replace',
            )
        except Exception as e:
            return self._make_result(command, False, -1, "", str(e),
                                     time.time() - start_time, "Error launching command")

        try:
            stdout, stderr = process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return self._make_result(command, False, -1, stdout, stderr,
                                     time.time() - start_time,
                                     f"Command timed out after {self.timeout} seconds")

        success = process.returncode == 0
        message = "Command completed" if success else f"Exit code {process.returncode}"
        return self._make_result(command, success, process.returncode, stdout, stderr,
                                 time.time() - start_time, message)

    @staticmethod
    def _make_result(command: str, success: bool, exit_code: int, stdout: str, stderr: str,
                     duration: float, message: str) -> Dict:
        return {
            'command': command,
            'success': success,
            'exit_code': exit_code,
            'stdout': stdout,
            'stderr': stderr,
            'duration': round(duration, 3),
            'message': message,
        }

    def _persist_evidence(self, task: Dict, result: Dict, evidence_dir: Optional[Path]) -> Optional[Path]:
        """Write verification evidence for a task to logs/."""
        task_id = task.get('id', 'UNKNOWN')
        base = evidence_dir or self.evidence_dir
        if base is None:
            base = Path.cwd() / 'logs' / 'autonomous'
        base = Path(base)
        dir_path = base / task_id
        dir_path.mkdir(parents=True, exist_ok=True)

        evidence = {
            'task_id': task_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'success': result.get('success', False),
            'commands': result.get('commands', []),
        }
        evidence_file = dir_path / 'verification_evidence.json'
        with open(evidence_file, 'w', encoding='utf-8') as f:
            json.dump(evidence, f, indent=2)
        return evidence_file


# Singleton instance for application use
verification_runner = VerificationRunner()

# Convenience function for direct use
run_verification = verification_runner.run_verification