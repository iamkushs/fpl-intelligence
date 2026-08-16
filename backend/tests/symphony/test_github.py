import json
import subprocess

import pytest

from tools.symphony_runner.github import GitHubClient
from tools.symphony_runner.models import GitHubOutputError, GitHubServiceError, RetryableError


class FakeRun:
    def __init__(self): self.calls=[]; self.comments=[]; self.labels={1:[{"name":"symphony"}]}
    def __call__(self, command, **kwargs):
        self.calls.append(command); output=""
        if command[1:3] == ["issue","list"]:
            output=json.dumps([{"number":1,"title":"A","body":"B","labels":self.labels[1],"state":"OPEN","url":"u"}])
        elif command[1:3] == ["issue","view"]:
            output=json.dumps({"number":1,"title":"A","body":"B","labels":self.labels[1],"state":"OPEN","url":"u"})
        elif any(value.endswith("/comments") for value in command) and "-f" not in command: output=json.dumps(self.comments)
        elif any(value.endswith("/comments") for value in command) and "PATCH" not in command:
            body=next(v[5:] for v in command if v.startswith("body=")); item={"id":7,"body":body}; self.comments=[item]; output=json.dumps(item)
        elif any(value.endswith("comments/7") for value in command) and "PATCH" in command:
            body=next(v[5:] for v in command if v.startswith("body=")); item={"id":7,"body":body}; self.comments=[item]; output=json.dumps(item)
        elif command[1:3] == ["pr","list"]: output="[]"
        return subprocess.CompletedProcess(command,0,output,"")


def test_candidates_workpad_reuse_update_and_labels():
    run=FakeRun(); client=GitHubClient("o/r",run=run)
    assert [i.number for i in client.list_candidates(("symphony",))] == [1]
    first=client.ensure_workpad(client.get_issue(1)); second=client.ensure_workpad(client.get_issue(1))
    assert first.comment_id == second.comment_id == 7 and len(run.comments)==1
    assert client.update_workpad(7,"## Codex Workpad\nupdated").body.endswith("updated")
    client.add_label(1,"symphony-review"); client.remove_label(1,"symphony")


def test_tools_reject_unrestricted_label():
    client=GitHubClient("o/r",run=FakeRun())
    with pytest.raises(ValueError): client.invoke_tool("add_label",{"issue_number":1,"label":"admin"})


def test_retryable_github_error():
    def fail(command,**kwargs): return subprocess.CompletedProcess(command,1,"","temporary")
    with pytest.raises(RetryableError): GitHubClient("o/r",run=fail).list_candidates(("symphony",))


def test_gh_uses_strict_utf8_and_accepts_windows_regression_character():
    seen = {}
    def run(command, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, '[{"number":1,"title":"🙂","state":"open"}]', "")
    assert GitHubClient("o/r", run=run).list_candidates(())[0].title == "🙂"
    assert seen["encoding"] == "utf-8" and seen["errors"] == "strict"


@pytest.mark.parametrize("stdout, message", [(None, "no stdout"), ("", "empty required"), ("{", "malformed JSON")])
def test_required_gh_json_output_errors_are_typed(stdout, message):
    def run(command, **kwargs): return subprocess.CompletedProcess(command, 0, stdout, "")
    with pytest.raises(GitHubOutputError, match=message):
        GitHubClient("o/r", run=run).list_candidates(())


def test_malformed_utf8_is_local_output_error_not_service_error():
    def run(command, **kwargs): raise UnicodeDecodeError("utf-8", b"\x9d", 0, 1, "invalid start byte")
    with pytest.raises(GitHubOutputError, match="valid UTF-8") as caught:
        GitHubClient("o/r", run=run).list_candidates(())
    assert not isinstance(caught.value, GitHubServiceError)


def test_nonzero_gh_exit_is_typed_service_error():
    def run(command, **kwargs): return subprocess.CompletedProcess(command, 4, "", "authentication failed")
    with pytest.raises(GitHubServiceError, match="authentication failed"):
        GitHubClient("o/r", run=run).list_candidates(())
