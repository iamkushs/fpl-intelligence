from queue import Queue

import pytest

import fpl_intelligence.codex.client as client_module
from fpl_intelligence.codex.client import (
    CodexAppServerClient,
    CodexAppServerError,
    CodexExecutionConfig,
)
from fpl_intelligence.config import Settings


class FakeRpc:
    instances = []

    def __init__(self, command, cwd):
        self.command = command
        self.messages = Queue()
        self.sent = []
        self.closed = False
        self.__class__.instances.append(self)

    def send(self, message):
        self.sent.append(message)
        if message.get("method") == "initialize":
            self.messages.put({"id": message["id"], "result": {}})
        elif message.get("method") == "thread/start":
            self.messages.put({"id": message["id"], "result": {"thread": {"id": "thread-live-fake"}}})
        elif message.get("method") == "turn/start":
            self.messages.put({"id": message["id"], "result": {"turn": {"id": "turn-live-fake", "status": "inProgress"}}})
            for delta in ("hello from ", "Codex"):
                self.messages.put({
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": "thread-live-fake", "turnId": "turn-live-fake", "itemId": "item-1", "delta": delta},
                })
            self.messages.put({
                "method": "turn/completed",
                "params": {"threadId": "thread-live-fake", "turn": {"id": "turn-live-fake", "status": "completed", "items": []}},
            })

    def receive(self, timeout):
        return self.messages.get(timeout=timeout)

    def close(self):
        self.closed = True


def test_app_server_client_executes_thread_and_turn():
    FakeRpc.instances.clear()
    settings = Settings(database_url="sqlite://", codex_timeout_seconds=2)
    client = CodexAppServerClient(settings=settings, process_factory=FakeRpc)

    result = client.execute("bounded question", CodexExecutionConfig(model="codex-test", reasoning_effort="medium"))

    assert result.thread_id == "thread-live-fake"
    assert result.turn_id == "turn-live-fake"
    assert result.final_text == "hello from Codex"
    assert [message["method"] for message in FakeRpc.instances[0].sent] == [
        "initialize", "initialized", "thread/start", "turn/start"
    ]
    assert FakeRpc.instances[0].sent[-1]["params"]["effort"] == "medium"
    assert FakeRpc.instances[0].closed


def test_app_server_client_resolves_windows_command_wrapper(monkeypatch):
    FakeRpc.instances.clear()
    monkeypatch.setattr(client_module.os, "name", "nt")
    monkeypatch.setattr(client_module.shutil, "which", lambda command: r"C:\\tools\\codex.CMD")
    client = CodexAppServerClient(
        settings=Settings(database_url="sqlite://", codex_timeout_seconds=2), process_factory=FakeRpc
    )

    client.execute("bounded question", CodexExecutionConfig())

    assert FakeRpc.instances[0].command[0] == r"C:\\tools\\codex.CMD"


class ErrorRpc(FakeRpc):
    def send(self, message):
        self.sent.append(message)
        if message.get("method") == "initialize":
            self.messages.put({"id": message["id"], "result": {}})
        elif message.get("method") == "thread/start":
            self.messages.put({
                "id": message["id"],
                "error": {"code": -32000, "message": "thread start failed"},
            })


def test_app_server_client_surfaces_json_rpc_failure():
    settings = Settings(database_url="sqlite://", codex_timeout_seconds=2)
    client = CodexAppServerClient(settings=settings, process_factory=ErrorRpc)

    with pytest.raises(CodexAppServerError, match="thread start failed"):
        client.execute("bounded question", CodexExecutionConfig())


def test_app_server_client_rejects_empty_completed_response():
    class EmptyRpc(FakeRpc):
        def send(self, message):
            super().send(message)
            if message.get("method") == "turn/start":
                self.messages.queue.clear()
                self.messages.put({
                    "id": message["id"],
                    "result": {"turn": {"id": "turn-empty", "status": "completed", "items": []}},
                })

    settings = Settings(database_url="sqlite://", codex_timeout_seconds=2)
    client = CodexAppServerClient(settings=settings, process_factory=EmptyRpc)

    with pytest.raises(CodexAppServerError, match="without a final text"):
        client.execute("bounded question", CodexExecutionConfig())
