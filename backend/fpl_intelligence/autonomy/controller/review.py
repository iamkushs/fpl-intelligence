# FPL Intelligence System — Autonomous Review Applicator
# Follows: spec/AUTONOMOUS_BUILD_SPEC.md, AGENTS.md, planning/BUILD_PLAN.md

import json
import re
import subprocess
from typing import Dict, Optional
from ..controller.session import launch_task_session

class ReviewApplicator:
    """Launches real AUTONOMOUS_REVIEWER sessions and parses their structured outcomes."""

    def __init__(self, opencode_path: str = "opencode"):
        self.opencode_path = opencode_path

    def apply_review_outcome(self, task: Dict, execution_context: Dict) -> Dict:
        """Launch a real OpenCode Reviewer session and process its structured output.

        The execution_context must contain:
        - verification_result: Dict containing exit_code, stdout, stderr of verification commands
        - implementer_result: Dict containing the result of the Implementer session
        """
        task_id = task['id']

        # Get actual Git diff
        git_diff = self._get_git_diff()

        # Format verification results for the reviewer
        verification_success = execution_context.get('verification_result', {}).get('success', False)
        verification_stdout = execution_context.get('verification_result', {}).get('stdout', '')
        verification_stderr = execution_context.get('verification_result', {}).get('stderr', '')

        verification_summary = f"Success: {verification_success}\nStdout:\n{verification_stdout}\nStderr:\n{verification_stderr}"

        # Build prompt for the Reviewer
        prompt = self._prepare_reviewer_prompt(task, git_diff, verification_summary)

        # Launch Reviewer session
        # We wrap the prompt in a temporary task object so launch_task_session can use it
        reviewer_task = {
            'id': task_id,
            'title': f"Review of {task_id}",
            'milestone': task.get('milestone', 'M00'),
            'autonomy_class': task.get('autonomy_class', 'A1'),
            'objective': "Perform independent code and test review.",
            'instructions': prompt,
            'acceptance': [],
            'verification': [],
            'likely_paths': task.get('likely_paths', [])
        }

        print(f"  Launching real Reviewer session for {task_id}")
        session_result = launch_task_session(reviewer_task, 'AUTONOMOUS_REVIEWER')

        if not session_result.get('success'):
            return {
                'approved': False,
                'outcome': 'CHANGES_REQUIRED',
                'summary': 'Reviewer process failed to run or timed out.',
                'blocking_issues': ['Reviewer process failed to execute successfully.'],
                'non_blocking_notes': []
            }

        # Parse structured output from stdout
        stdout = session_result.get('stdout', '')
        parsed_outcome = self._parse_reviewer_json(stdout)

        if parsed_outcome is None:
            return {
                'approved': False,
                'outcome': 'CHANGES_REQUIRED',
                'summary': 'Malformed Reviewer output. Could not parse JSON.',
                'blocking_issues': ['Reviewer failed to output a valid structured JSON block.'],
                'non_blocking_notes': []
            }

        outcome_str = parsed_outcome.get('outcome', 'CHANGES_REQUIRED').upper()
        approved = outcome_str in ['APPROVE', 'APPROVE_WITH_NOTES']

        return {
            'approved': approved,
            'outcome': outcome_str,
            'summary': parsed_outcome.get('summary', 'No summary provided.'),
            'blocking_issues': parsed_outcome.get('blocking_issues', []),
            'non_blocking_notes': parsed_outcome.get('non_blocking_notes', [])
        }

    def _get_git_diff(self) -> str:
        """Run git diff to get all changes since HEAD."""
        try:
            diff = subprocess.run(['git', 'diff', 'HEAD'], capture_output=True, text=True)
            if diff.stdout:
                return diff.stdout
            return "No changes detected in Git diff."
        except Exception as e:
            return f"Error getting git diff: {e}"

    def _prepare_reviewer_prompt(self, task: Dict, git_diff: str, verification_results: str) -> str:
        """Prepare instructions for the Reviewer agent to enforce correct JSON schema."""
        return f"""
You are the AUTONOMOUS_REVIEWER. Your goal is to independently review the implementation of task {task['id']}.

### Task Specifications
ID: {task['id']}
Objective: {task.get('objective', 'No objective')}
Instructions: {task.get('instructions', 'No instructions')}
Acceptance Criteria: {task.get('acceptance', [])}

### Actual Implementation Changes (Git Diff)
{git_diff}

### Verification Command Results
{verification_results}

### Enforced Rules
1. Never accept a change that breaks verification.
2. Verify that unit and integration tests are present if appropriate for the task.
3. Mimic the codebase style conventions strictly.
4. If there are any blocking issues, the outcome MUST be "CHANGES_REQUIRED".
5. Your output must end with a valid JSON block enclosed in ```json and ```. Do not output any text after the JSON block.

Required JSON Schema:
```json
{{
  "outcome": "APPROVE" | "APPROVE_WITH_NOTES" | "CHANGES_REQUIRED" | "ARCHITECTURE_REVIEW_REQUIRED" | "BLOCKED",
  "summary": "Detailed summary explaining your decision...",
  "blocking_issues": ["Issue 1", "Issue 2"],
  "non_blocking_notes": ["Note 1", "Note 2"]
}}
```
"""

    def _parse_reviewer_json(self, stdout: str) -> Optional[Dict]:
        """Extract and parse the JSON block from the Reviewer's stdout."""
        # Find JSON block inside markdown fenced code block
        match = re.search(r'```json\s*(\{.*?\})\s*```', stdout, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            # Fallback: look for first { to last }
            match = re.search(r'(\{.*\})', stdout, re.DOTALL)
            if match:
                json_str = match.group(1)
            else:
                return None

        try:
            parsed = json.loads(json_str)
            # Enforce minimal schema keys
            required_keys = ['outcome', 'summary', 'blocking_issues', 'non_blocking_notes']
            if all(k in parsed for k in required_keys):
                return parsed
            return None
        except json.JSONDecodeError:
            return None

# Singleton instance for application use
review_applicator = ReviewApplicator()

# Convenience function for direct use
apply_review_outcome = review_applicator.apply_review_outcome
