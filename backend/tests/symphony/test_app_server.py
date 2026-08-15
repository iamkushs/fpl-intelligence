import asyncio
import sys
from pathlib import Path

import pytest

from tools.symphony_runner.app_server import CodexAppServer
from tools.symphony_runner.config import RunnerConfig
from tools.symphony_runner.models import AppServerError, AppServerTimeout
from tools.symphony_runner.models import RunRecord
from tools.symphony_runner.state import StateStore

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


def test_thread_and_turn_are_reported_before_later_turn_work_crashes(tmp_path):
    store=StateStore(tmp_path/"state.json"); record=RunRecord(1,str(tmp_path),"symphony/gh-1"); store.records[1]=record
    def thread(value): record.thread_id=value; store.save()
    def turn(value): record.turn_id=value; store.save()
    async def run():
        client=CodexAppServer(config(tmp_path,"crash_after_turn"),tmp_path,[],lambda *_:None)
        try:
            await client.run_turn("hello",on_thread=thread,on_turn=turn)
        finally: await client.close()
    with pytest.raises(AppServerError): asyncio.run(run())
    recovered=StateStore(tmp_path/"state.json"); loaded=recovered.load()[1]
    assert loaded.thread_id == "thread-fake" and loaded.turn_id == "turn-fake"


def test_resume_reports_existing_thread_immediately(tmp_path):
    seen=[]
    async def run():
        client=CodexAppServer(config(tmp_path),tmp_path,[],lambda *_:{})
        try: return await client.run_turn("resume","durable-thread",on_thread=seen.append)
        finally: await client.close()
    result=asyncio.run(run())
    assert result.thread_id == "durable-thread" and seen == ["durable-thread"]
