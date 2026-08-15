import subprocess

import pytest

from tools.symphony_runner.github import GitHubClient
from tools.symphony_runner.models import RetryableError


def test_issue_view_none_stdout_is_descriptive_retryable_infrastructure_error():
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=None, stderr="")

    client = GitHubClient("o/r", run=run)
    with pytest.raises(RetryableError, match="GitHub CLI returned no stdout value"):
        client.get_issue(5)


def test_github_json_decoder_never_passes_none_to_json_loads():
    with pytest.raises(RetryableError, match="returned no JSON text"):
        GitHubClient._decode(None, "issue view")
