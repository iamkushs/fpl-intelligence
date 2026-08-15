# FPL Intelligence System — Autonomous Review Applicator Tests
# Follows: spec/AUTONOMOUS_BUILD_SPEC.md, AGENTS.md, planning/BUILD_PLAN.md

import pytest
from unittest.mock import patch, MagicMock
import json
from fpl_intelligence.autonomy.controller.review import ReviewApplicator

def test_review_applicator_approve():
    """Test reviewer returning APPROVE."""
    task = {'id': 'M00-T01', 'objective': 'Test', 'instructions': 'Test', 'acceptance': []}
    execution_context = {
        'verification_result': {'success': True, 'stdout': 'All tests passed', 'stderr': ''},
        'implementer_result': {'stdout': 'Done', 'stderr': ''}
    }

    reviewer_stdout = json.dumps({
        "outcome": "APPROVE",
        "summary": "Implementation is perfect.",
        "blocking_issues": [],
        "non_blocking_notes": []
    })

    with patch('fpl_intelligence.autonomy.controller.review.subprocess.run') as mock_run, \
         patch('fpl_intelligence.autonomy.controller.review.launch_task_session') as mock_launch:
        mock_run.return_value = MagicMock(stdout="No changes", returncode=0)
        mock_launch.return_value = {
            'success': True,
            'stdout': f"Here is the review:\n```json\n{reviewer_stdout}\n```",
            'stderr': ''
        }

        applicator = ReviewApplicator()
        outcome = applicator.apply_review_outcome(task, execution_context)

        assert outcome['approved'] is True
        assert outcome['outcome'] == 'APPROVE'

def test_review_applicator_changes_required():
    """Test reviewer returning CHANGES_REQUIRED."""
    task = {'id': 'M00-T01', 'objective': 'Test', 'instructions': 'Test', 'acceptance': []}
    execution_context = {
        'verification_result': {'success': True, 'stdout': '', 'stderr': ''},
        'implementer_result': {}
    }

    reviewer_stdout = json.dumps({
        "outcome": "CHANGES_REQUIRED",
        "summary": "Missing tests.",
        "blocking_issues": ["No unit tests found"],
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
        outcome = applicator.apply_review_outcome(task, execution_context)

        assert outcome['approved'] is False
        assert outcome['outcome'] == 'CHANGES_REQUIRED'
        assert "No unit tests found" in outcome['blocking_issues']

def test_review_applicator_malformed_json():
    """Test reviewer returning malformed JSON fails."""
    task = {'id': 'M00-T01', 'objective': 'Test', 'instructions': 'Test', 'acceptance': []}
    execution_context = {'verification_result': {'success': True}, 'implementer_result': {}}

    with patch('fpl_intelligence.autonomy.controller.review.subprocess.run') as mock_run, \
         patch('fpl_intelligence.autonomy.controller.review.launch_task_session') as mock_launch:
        mock_run.return_value = MagicMock(stdout="No changes", returncode=0)
        mock_launch.return_value = {
            'success': True,
            'stdout': "This is not JSON at all.",
            'stderr': ''
        }

        applicator = ReviewApplicator()
        outcome = applicator.apply_review_outcome(task, execution_context)

        assert outcome['approved'] is False
        assert outcome['outcome'] == 'CHANGES_REQUIRED'
        assert "Malformed Reviewer output" in outcome['summary']

def test_review_applicator_nonzero_exit_code():
    """Test reviewer process failure is not approval."""
    task = {'id': 'M00-T01', 'objective': 'Test', 'instructions': 'Test', 'acceptance': []}
    execution_context = {'verification_result': {'success': True}, 'implementer_result': {}}

    with patch('fpl_intelligence.autonomy.controller.review.subprocess.run') as mock_run, \
         patch('fpl_intelligence.autonomy.controller.review.launch_task_session') as mock_launch:
        mock_run.return_value = MagicMock(stdout="No changes", returncode=0)
        mock_launch.return_value = {
            'success': False,
            'stdout': '',
            'stderr': 'Error'
        }

        applicator = ReviewApplicator()
        outcome = applicator.apply_review_outcome(task, execution_context)

        assert outcome['approved'] is False
        assert "failed to run" in outcome['summary']

def test_reviewer_command_construction():
    """Test that reviewer command is constructed with correct flags."""
    task = {'id': 'M00-T01', 'objective': 'Test', 'instructions': 'Test', 'acceptance': [], 'likely_paths': []}
    execution_context = {'verification_result': {'success': True}, 'implementer_result': {}}

    with patch('fpl_intelligence.autonomy.controller.review.subprocess.run') as mock_run, \
         patch('fpl_intelligence.autonomy.controller.review.launch_task_session') as mock_launch:
        mock_run.return_value = MagicMock(stdout="No changes", returncode=0)
        mock_launch.return_value = {'success': False}

        applicator = ReviewApplicator()
        applicator.apply_review_outcome(task, execution_context)

        # Assert launch_task_session was called
        mock_launch.assert_called_once()
        called_args, called_kwargs = mock_launch.call_args
        assert called_kwargs.get('role') == 'AUTONOMOUS_REVIEWER' or called_args[1] == 'AUTONOMOUS_REVIEWER'
