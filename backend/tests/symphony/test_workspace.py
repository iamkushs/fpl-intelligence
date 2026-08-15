import subprocess
from pathlib import Path

from tools.symphony_runner.models import Issue
from tools.symphony_runner.workspace import WorkspaceManager


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


def test_cleanup_requires_eligibility_and_supports_dry_run(tmp_path):
    manager=WorkspaceManager(tmp_path/"spaces","o/r"); issue=Issue(3,"","",(),"closed","")
    path=manager.path_for(issue); path.mkdir(parents=True)
    assert not manager.cleanup(issue,dry_run=True,allowed=False)
    assert manager.cleanup(issue,dry_run=True,allowed=True) and path.exists()
    assert manager.cleanup(issue,dry_run=False,allowed=True) and not path.exists()
