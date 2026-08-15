import asyncio
import sys
from pathlib import Path

import pytest

from tools.symphony_runner.app_server import CodexAppServer
from tools.symphony_runner.config import RunnerConfig
from tools.symphony_runner.models import AppServerError, AppServerTimeout

FAKE=Path(__file__).with_name("fake_app_server.py")


def config(tmp_path, mode="normal"):
    value=RunnerConfig("o/r","",codex_command=(sys.executable,str(FAKE),mode),turn_timeout_ms=2000,read_timeout_ms=1000,stall_timeout_ms=200)
    return value


def test_handshake_dynamic_tool_and_resume(tmp_path):
    calls=[]
    async def run():
        client=CodexAppServer(config(tmp_path),tmp_path,[],lambda name,args: calls.append((name,args)) or {"ok":True})
        try:
            first=await client.run_turn("hello"); second=await client.run_turn("again",first.thread_id)
            return first,second
        finally: await client.close()
    first,second=asyncio.run(run())
    assert first.status=="completed" and second.thread_id==first.thread_id and len(calls)==2


@pytest.mark.parametrize("mode,error",[("malformed",AppServerError),("exit",AppServerError),("stall",AppServerTimeout)])
def test_protocol_failures(tmp_path,mode,error):
    async def run():
        client=CodexAppServer(config(tmp_path,mode),tmp_path,[],lambda *_:None)
        try: await client.run_turn("hello")
        finally: await client.close()
    with pytest.raises(error): asyncio.run(run())


def test_child_environment_scrubs_tracker_secrets(monkeypatch,tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN","secret"); monkeypatch.setenv("GH_TOKEN","secret2"); monkeypatch.setenv("CODEX_HOME","safe")
    env=config(tmp_path).sanitized_child_environment()
    assert "GITHUB_TOKEN" not in env and "GH_TOKEN" not in env and env["CODEX_HOME"]=="safe"
