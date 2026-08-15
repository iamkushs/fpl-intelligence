import asyncio
import sys
from pathlib import Path

import pytest

from tools.symphony_runner.app_server import CodexAppServer, decode_json_message
from tools.symphony_runner.config import RunnerConfig
from tools.symphony_runner.models import (AppServerError, AppServerProtocolError,
    AppServerThreadUnavailable, AppServerTimeout, AppServerTurnFailed,
    AppServerTurnInterrupted)
from tools.symphony_runner.models import RunRecord
from tools.symphony_runner.state import StateStore

FAKE=Path(__file__).with_name("fake_app_server.py")


def config(tmp_path, mode="normal", *, size=0, max_message_bytes=16 * 1024 * 1024):
    value=RunnerConfig("o/r","",codex_command=(sys.executable,str(FAKE),mode,str(size)),turn_timeout_ms=10000,read_timeout_ms=5000,stall_timeout_ms=2000,app_server_max_message_bytes=max_message_bytes)
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
    assert calls[0][1] == {"issue_number": 1, "filters": ["open"]}


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


def test_model_and_effort_use_schema_backed_turn_fields(tmp_path):
    async def run():
        client=CodexAppServer(config(tmp_path,"validate_model"),tmp_path,[],lambda *_:{})
        try: return await client.run_turn("routed",model="gpt-5.5",effort="medium")
        finally: await client.close()
    assert asyncio.run(run()).status == "completed"


@pytest.mark.parametrize("size", [128 * 1024, 1024 * 1024, 5 * 1024 * 1024])
def test_large_chunked_jsonl_messages_cross_subprocess_pipe(tmp_path, size):
    async def run():
        client = CodexAppServer(config(tmp_path, "large", size=size), tmp_path, [], lambda *_: {})
        try:
            await client.initialize()
            return client.process.returncode
        finally:
            await client.close()
    assert asyncio.run(run()) is None


def test_large_multibyte_utf8_message_is_decoded_after_assembly(tmp_path):
    async def run():
        client = CodexAppServer(config(tmp_path, "large_utf8", size=100_000), tmp_path, [], lambda *_: {})
        try: await client.initialize()
        finally: await client.close()
    asyncio.run(run())


def test_oversized_message_is_clear_retryable_and_child_is_cleaned_up(tmp_path):
    client = None
    record = RunRecord(5, str(tmp_path), "symphony/gh-5", thread_id="existing-thread",
                       requested_model_route="5.5", resolved_model_id="gpt-5.5",
                       productive_failure_count=0)
    async def run():
        nonlocal client
        client = CodexAppServer(config(tmp_path, "large", size=16_000, max_message_bytes=4096), tmp_path, [], lambda *_: {})
        try: await client.initialize()
        finally: await client.close()
    with pytest.raises(AppServerError, match="Codex App Server message exceeded configured 4096 bytes limit") as raised:
        asyncio.run(run())
    assert raised.value.retryable is True
    assert client is not None and client.process is None
    assert record.thread_id == "existing-thread"
    assert record.requested_model_route == "5.5" and record.resolved_model_id == "gpt-5.5"
    assert record.productive_failure_count == 0 and record.escalation_level == 0


@pytest.mark.parametrize("payload", [{}, [], None])
def test_json_boundary_never_decodes_already_decoded_or_null_values(payload):
    with pytest.raises(AppServerProtocolError, match="must be bytes or string"):
        decode_json_message(payload)


def test_json_boundary_decodes_bytes_and_string_once():
    assert decode_json_message(b'{"result":{"nested":[1]}}')["result"] == {"nested": [1]}
    assert decode_json_message('{"result":null}')["result"] is None


@pytest.mark.parametrize("mode,error,pattern", [
    ("failed", AppServerTurnFailed, "turn failed"),
    ("interrupted", AppServerTurnInterrupted, "turn interrupted"),
    ("null_agent", AppServerProtocolError, "no non-null agent output"),
    ("missing_arguments", AppServerProtocolError, "params.arguments must be an object"),
])
def test_terminal_and_tool_schema_classifications(tmp_path, mode, error, pattern):
    async def run():
        client = CodexAppServer(config(tmp_path, mode), tmp_path, [], lambda *_: {})
        try: await client.run_turn("hello")
        finally: await client.close()
    with pytest.raises(error, match=pattern):
        asyncio.run(run())


def test_invalid_persisted_thread_is_rotation_eligible(tmp_path):
    async def run():
        client = CodexAppServer(config(tmp_path, "resume_not_found"), tmp_path, [], lambda *_: {})
        try: await client.run_turn("hello", "old-thread")
        finally: await client.close()
    with pytest.raises(AppServerThreadUnavailable, match="cannot be resumed"):
        asyncio.run(run())
