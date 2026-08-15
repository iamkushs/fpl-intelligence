# FPL Intelligence System — Autonomous Controller Tests
# Follows: spec/AUTONOMOUS_BUILD_SPEC.md, AGENTS.md, planning/BUILD_PLAN.md

import pytest
from unittest.mock import patch, MagicMock
import json
from pathlib import Path
from fpl_intelligence.autonomy.controller.session import TaskSessionLauncher
from fpl_intelligence.autonomy.controller.verification import VerificationRunner, robustify_powershell_command
from fpl_intelligence.autonomy.controller.review import ReviewApplicator
from fpl_intelligence.autonomy.model_pools import ModelPoolSelector

def test_task_session_launcher():
    launcher = TaskSessionLauncher()
    task = {
        'id': 'M00-T01',
        'milestone': 'M00',
        'status': 'READY',
        'dependencies': [],
        'spec_refs': [],
        'system_acceptance_refs': [],
        'objective': 'Create repository structure',
        'instructions': 'Follow the instructions'
    }
    result = launcher.launch_task_session(task, 'AUTONOMOUS_IMPLEMENTER')
    assert 'session_id' in result
    assert result['task_id'] == 'M00-T01'
    assert result['role'] == 'AUTONOMOUS_IMPLEMENTER'

def test_verification_runner():
    runner = VerificationRunner()
    task = {
        'id': 'M00-T01',
        'verification': ['echo "Verification passed"']
    }
    result = runner.run_verification(task)
    assert result['success'] is True

    # Test command failure
    task = {
        'id': 'M00-T01',
        'verification': ['exit 1']
    }
    result = runner.run_verification(task)
    assert result['success'] is False

    # Test no verification commands
    task = {
        'id': 'M00-T01',
        'verification': []
    }
    result = runner.run_verification(task)
    assert result['success'] is True

def test_verification_runner_structured_entries():
    runner = VerificationRunner()
    task = {
        'id': 'M00-T02',
        'verification': [
            {'type': 'command', 'shell': 'default', 'command': 'echo ok'},
            {'type': 'command', 'shell': 'default', 'command': 'exit 1'},
        ]
    }
    result = runner.run_verification(task)
    assert result['success'] is False
    assert result['commands'][0]['success'] is True
    assert result['commands'][1]['success'] is False

def test_verification_runner_persists_evidence(tmp_path):
    runner = VerificationRunner(evidence_dir=tmp_path)
    task = {'id': 'M00-T99', 'verification': ['echo ok']}
    result = runner.run_verification(task)
    evidence_file = tmp_path / 'M00-T99' / 'verification_evidence.json'
    assert evidence_file.exists()
    evidence = json.loads(evidence_file.read_text())
    assert evidence['task_id'] == 'M00-T99'
    assert evidence['success'] is True

def test_robustify_powershell_command():
    raw = "Test-Path spec; Test-Path planning/BUILD_PLAN.md"
    wrapped = robustify_powershell_command(raw)
    # Each Test-Path must be wrapped in an explicit failing check
    assert "if (-not (Test-Path 'spec'))" in wrapped
    assert "if (-not (Test-Path 'planning/BUILD_PLAN.md'))" in wrapped
    assert "exit 1" in wrapped

    # Non-Test-Path parts pass through unchanged
    wrapped2 = robustify_powershell_command("git status; Test-Path spec")
    assert "git status" in wrapped2
    assert "if (-not (Test-Path 'spec'))" in wrapped2

def test_powershell_boolean_check_fails_loudly():
    """A missing path in a PowerShell Test-Path check must fail the run."""
    runner = VerificationRunner()
    task = {
        'id': 'M00-T01',
        'verification': [
            {'type': 'command', 'shell': 'powershell',
             'command': 'Test-Path __definitely_missing_fpl_path_xyz__'}
        ]
    }
    result = runner.run_verification(task)
    assert result['success'] is False
    assert result['commands'][0]['exit_code'] != 0

def test_verification_runner_malformed_entry():
    runner = VerificationRunner()
    task = {'id': 'M00-T01', 'verification': [{'type': 'bogus', 'command': 'ls'}]}
    result = runner.run_verification(task)
    assert result['success'] is False

def test_review_applicator_approval_from_parsed_output():
    """Prove that approval comes from the parsed Reviewer JSON output, not verification."""
    task = {'id': 'M00-T01', 'objective': 'Test', 'instructions': 'Test', 'acceptance': []}
    execution_context = {'verification_result': {'success': True}, 'implementer_result': {}}

    reviewer_stdout = json.dumps({
        "outcome": "APPROVE",
        "summary": "No blocking issues",
        "blocking_issues": [],
        "non_blocking_notes": []
    })

    with patch('fpl_intelligence.autonomy.controller.review.subprocess.run') as mock_run, \
         patch('fpl_intelligence.autonomy.controller.review.launch_task_session') as mock_launch:
        mock_run.return_value = MagicMock(stdout="No changes", returncode=0)
        mock_launch.return_value = {
            'success': True,
            'stdout': reviewer_stdout,
            'stderr': ''
        }

        applicator = ReviewApplicator()
        review_outcome = applicator.apply_review_outcome(task, execution_context)

        assert 'approved' in review_outcome
        assert review_outcome['approved'] is True
        assert review_outcome['outcome'] == 'APPROVE'

def test_model_pool_selector():
    selector = ModelPoolSelector()
    model = selector.select_model_for_role('AUTONOMOUS_PLANNER')
    assert model.startswith('auto/') or model.startswith('nvidia/')

    # Test all roles
    for role in ['AUTONOMOUS_PLANNER', 'AUTONOMOUS_IMPLEMENTER', 'AUTONOMOUS_REVIEWER', 'AUTONOMOUS_REFEREE', 'AUTONOMOUS_HELPER']:
        model = selector.select_model_for_role(role)
        assert model  # non-empty

    # Test unknown role
    with pytest.raises(ValueError):
        selector.select_model_for_role('UNKNOWN_ROLE')

    # Test provider approval
    assert selector.is_provider_approved('nvidia') is True
    assert selector.is_provider_approved('unknown_provider') is False
