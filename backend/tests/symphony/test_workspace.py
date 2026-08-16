import subprocess
from pathlib import Path

from tools.symphony_runner.models import Issue
import pytest

from tools.symphony_runner.models import ConfigurationError
from tools.symphony_runner.workspace import HostGitLifecycle, Workspace, WorkspaceManager


def git(cwd,*args): subprocess.run(["git",*args],cwd=cwd,check=True,capture_output=True,text=True)


def origin(tmp_path: Path) -> Path:
    source=tmp_path/"source"; source.mkdir(); git(source,"init","-b","main"); git(source,"config","user.email","test@example.com"); git(source,"config","user.name","Test")
    (source/"file.txt").write_text("base"); git(source,"add","."); git(source,"commit","-m","base")
    bare=tmp_path/"origin.git"; git(tmp_path,"clone","--bare",str(source),str(bare)); return bare


def test_prepare_reuses_isolated_workspace_and_rerere(tmp_path):
    manager=WorkspaceManager(tmp_path/"spaces","o/r"); manager.clone_url=str(origin(tmp_path))
    one=Issue(1,"A","",("symphony",),"open","u"); two=Issue(2,"B","",("symphony",),"open","u")
    first=manager.prepare(one); (first.path/"local.txt").write_text("keep")
    again=manager.prepare(one); other=manager.prepare(two)
    assert first.created and not again.created and (again.path/"local.txt").exists() and other.path != first.path
    assert manager.inspect(again)["branch"] == "symphony/gh-1"
    assert subprocess.run(["git","config","--get","rerere.enabled"],cwd=again.path,capture_output=True,text=True).stdout.strip()=="true"


def test_new_dependent_workspace_refreshes_current_origin_main(tmp_path):
    bare = origin(tmp_path); manager = WorkspaceManager(tmp_path / "spaces", "o/r"); manager.clone_url = str(bare)
    manager.prepare(Issue(10, "NR10", "", ("symphony",), "open", "u"))
    upstream = tmp_path / "merged"; git(tmp_path, "clone", str(bare), str(upstream))
    git(upstream, "config", "user.email", "test@example.com"); git(upstream, "config", "user.name", "Test")
    (upstream / "nr10.txt").write_text("merged", encoding="utf-8")
    git(upstream, "add", "nr10.txt"); git(upstream, "commit", "-m", "merge NR10"); git(upstream, "push", "origin", "main")
    dependent = manager.prepare(Issue(11, "NR11", "Depends-On: #10", ("symphony",), "open", "u"))
    assert (dependent.path / "nr10.txt").read_text(encoding="utf-8") == "merged"


def test_git_capture_uses_explicit_utf8_and_retains_human_diagnostics(tmp_path):
    calls = []
    def run(command, **kwargs):
        calls.append(kwargs); return subprocess.CompletedProcess(command, 0, "🙂\n", "")
    assert WorkspaceManager(tmp_path, "o/r", run=run)._git(None, "--version") == "🙂"
    assert calls[0]["encoding"] == "utf-8" and calls[0]["errors"] == "replace"


def test_cleanup_requires_eligibility_and_supports_dry_run(tmp_path):
    manager=WorkspaceManager(tmp_path/"spaces","o/r"); issue=Issue(3,"","",(),"closed","")
    path=manager.path_for(issue); path.mkdir(parents=True)
    assert not manager.cleanup(issue,dry_run=True,allowed=False)
    assert manager.cleanup(issue,dry_run=True,allowed=True) and path.exists()
    assert manager.cleanup(issue,dry_run=False,allowed=True) and not path.exists()


def test_host_git_sync_commit_push_preserves_retry_workspace_and_rerere(tmp_path):
    bare=origin(tmp_path); manager=WorkspaceManager(tmp_path/"spaces","o/r"); manager.clone_url=str(bare)
    item=Issue(4,"Repair","",("symphony",),"open","u"); workspace=manager.prepare(item)
    (workspace.path/"task.txt").write_text("useful retry state")
    upstream=tmp_path/"upstream"; git(tmp_path,"clone",str(bare),str(upstream)); git(upstream,"config","user.email","test@example.com"); git(upstream,"config","user.name","Test")
    (upstream/"main.txt").write_text("new main"); git(upstream,"add","main.txt"); git(upstream,"commit","-m","main update"); git(upstream,"push","origin","main")
    lifecycle=HostGitLifecycle(); assert lifecycle.synchronize(workspace)
    assert (workspace.path/"task.txt").read_text() == "useful retry state"
    lifecycle.verify=lambda _: None
    lifecycle.verify(workspace); commit=lifecycle.commit_and_push(workspace,"task commit")
    assert commit == subprocess.run(["git","rev-parse",f"refs/heads/{workspace.branch}"],cwd=bare,capture_output=True,text=True,check=True).stdout.strip()
    assert subprocess.run(["git","config","--get","rerere.enabled"],cwd=workspace.path,capture_output=True,text=True).stdout.strip()=="true"


def test_host_git_guardrails_reject_markdown_runtime_and_verification_failure(tmp_path):
    manager=WorkspaceManager(tmp_path/"spaces","o/r"); manager.clone_url=str(origin(tmp_path)); workspace=manager.prepare(Issue(5,"","",("symphony",),"open","u"))
    lifecycle=HostGitLifecycle()
    with pytest.raises(ConfigurationError): lifecycle.validate_changes(workspace,["notes.md"])
    with pytest.raises(ConfigurationError): lifecycle.validate_changes(workspace,[".env"])
    calls=[]
    def fail_verify(command,**kwargs):
        calls.append(command); return subprocess.CompletedProcess(command,1,"","failed")
    with pytest.raises(ConfigurationError): HostGitLifecycle(run=fail_verify).verify(workspace)
    assert not any("push" in call or "commit" in call for call in calls)


def local_workspace(tmp_path: Path) -> Workspace:
    repo=tmp_path/"repo"; repo.mkdir(); git(repo,"init","-b","main"); git(repo,"config","user.email","test@example.com"); git(repo,"config","user.name","Test")
    return Workspace(repo,"main",True)


def test_status_z_regression_preserves_smoke_path_first_character():
    files=HostGitLifecycle._parse_status_z(" M tooling/symphony-smoke.env\0")
    assert files == ["tooling/symphony-smoke.env"]
    assert "ooling/symphony-smoke.env" not in files


def test_actual_discovery_and_staging_preserve_exact_smoke_path(tmp_path):
    workspace=local_workspace(tmp_path); path=workspace.path/"tooling"/"symphony-smoke.env"; path.parent.mkdir(); path.write_text("SMOKE=1")
    git(workspace.path,"add","tooling/symphony-smoke.env"); git(workspace.path,"commit","-m","base")
    path.write_text("SMOKE=2")
    lifecycle=HostGitLifecycle()
    assert lifecycle.changed_files(workspace) == ["tooling/symphony-smoke.env"]
    assert lifecycle.stage_changes(workspace) == ["tooling/symphony-smoke.env"]
    staged=subprocess.run(["git","diff","--cached","--name-only","-z"],cwd=workspace.path,capture_output=True,text=True,check=True).stdout.split("\0")
    assert [value for value in staged if value] == ["tooling/symphony-smoke.env"]


def test_status_z_paths_modified_untracked_deleted_renamed_spaces_and_unicode(tmp_path):
    workspace=local_workspace(tmp_path)
    tracked = ["tooling/symphony-smoke.env", "backend/fpl_intelligence/models.py", "frontend/app/watchlist/page.tsx", "deleted.txt", "rename-old.txt"]
    for name in tracked:
        path=workspace.path/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(name,encoding="utf-8")
    git(workspace.path,"add","--",*tracked); git(workspace.path,"commit","-m","base")
    for name in tracked[:3]: (workspace.path/name).write_text("modified",encoding="utf-8")
    (workspace.path/"deleted.txt").unlink(); (workspace.path/"nested").mkdir(); git(workspace.path,"mv","rename-old.txt","nested/rename-new.txt")
    additions=["nested/path with spaces/file.txt","nested/unicode-Δοκιμή.txt"]
    for name in additions:
        path=workspace.path/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text("new",encoding="utf-8")
    expected=sorted(set(tracked[:3] + ["deleted.txt","nested/rename-new.txt",*additions]))
    lifecycle=HostGitLifecycle(); assert lifecycle.changed_files(workspace) == expected
    assert lifecycle.stage_changes(workspace) == expected
    staged=lifecycle._git_output(workspace.path,"diff","--cached","--name-only","-z")
    assert sorted(value for value in staged.split("\0") if value) == expected
