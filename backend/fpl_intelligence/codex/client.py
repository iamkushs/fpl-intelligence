"""Minimal JSON-RPC stdio client for Codex App Server."""

from __future__ import annotations

import json
import logging
import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fpl_intelligence.config import Settings, get_settings

logger = logging.getLogger(__name__)


class CodexAppServerError(RuntimeError):
    """Raised when Codex App Server cannot complete an execution."""


@dataclass(frozen=True)
class CodexExecutionConfig:
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class CodexExecutionResult:
    thread_id: str
    turn_id: str | None
    final_text: str
    model: str | None
    reasoning_effort: str | None
    usage: dict[str, Any] | None
    started_at: datetime
    completed_at: datetime
    status: str = "completed"

    @property
    def execution_metadata(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "status": self.status,
        }


class _JsonRpcProcess:
    """Line-delimited JSON-RPC process wrapper with a timeout-aware reader."""

    def __init__(self, command: Sequence[str], cwd: str | None):
        self.process = subprocess.Popen(
            list(command),
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.messages: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self.stderr_lines: list[str] = []
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._stderr_reader.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                if line.strip():
                    self.messages.put(json.loads(line))
        except BaseException as exc:  # surfaced to the caller through the queue
            self.messages.put(exc)
        finally:
            self.messages.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip())

    def send(self, message: dict[str, Any]) -> None:
        if self.process.stdin is None or self.process.poll() is not None:
            raise CodexAppServerError(self._failure_message("process is not running"))
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def receive(self, timeout: float) -> dict[str, Any]:
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise CodexAppServerError(self._failure_message("timed out waiting for App Server")) from exc
        if message is None:
            raise CodexAppServerError(self._failure_message("App Server closed its output"))
        if isinstance(message, BaseException):
            raise CodexAppServerError(self._failure_message(str(message))) from message
        return message

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def _failure_message(self, message: str) -> str:
        stderr = "\n".join(self.stderr_lines[-10:])
        return f"{message}{': ' + stderr if stderr else ''}"


class CodexAppServerClient:
    """Starts a bounded App Server session and executes one thread/turn."""

    def __init__(
        self,
        settings: Settings | None = None,
        process_factory: Callable[[Sequence[str], str | None], _JsonRpcProcess] | None = None,
    ):
        self.settings = settings or get_settings()
        self.process_factory = process_factory or _JsonRpcProcess

    def execute(self, prompt: str, config: CodexExecutionConfig) -> CodexExecutionResult:
        if not prompt.strip():
            raise ValueError("Codex prompt must not be empty")

        started_at = datetime.now(timezone.utc)
        command = shlex.split(self.settings.codex_app_server_command, posix=False)
        if not command:
            raise CodexAppServerError("CODEX_APP_SERVER_COMMAND is empty")
        if os.name == "nt":
            # PowerShell can resolve `codex` through a .ps1 shim, but
            # CreateProcess cannot.  Resolve the executable wrapper (.CMD)
            # before starting the stdio server.
            command[0] = shutil.which(command[0]) or command[0]

        rpc = self.process_factory(command, self.settings.codex_working_directory)
        try:
            initialize_id = self._next_id()
            rpc.send(
                {
                    "id": initialize_id,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "fpl-intelligence",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": False},
                    },
                }
            )
            self._await_response(rpc, initialize_id)
            rpc.send({"method": "initialized", "params": {}})

            thread_id = self._request_thread(rpc, config)
            turn_id, final_text, usage, status = self._request_turn(
                rpc, thread_id, prompt, config
            )
            completed_at = datetime.now(timezone.utc)
            if status != "completed":
                raise CodexAppServerError(f"Codex turn finished with status {status}")
            if not final_text.strip():
                raise CodexAppServerError("Codex completed without a final text response")
            return CodexExecutionResult(
                thread_id=thread_id,
                turn_id=turn_id,
                final_text=final_text,
                model=config.model,
                reasoning_effort=config.reasoning_effort,
                usage=usage,
                started_at=started_at,
                completed_at=completed_at,
            )
        finally:
            rpc.close()

    def _request_thread(self, rpc: _JsonRpcProcess, config: CodexExecutionConfig) -> str:
        params: dict[str, Any] = {
            "approvalPolicy": "never",
            "sandbox": "read-only",
        }
        if config.model:
            params["model"] = config.model
        if self.settings.codex_working_directory:
            params["cwd"] = self.settings.codex_working_directory
        request_id = self._next_id()
        rpc.send({"id": request_id, "method": "thread/start", "params": params})
        response = self._await_response(rpc, request_id)
        result = response.get("result") or {}
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = self._find_string(thread, "id") or self._find_string(result, "threadId")
        if not thread_id:
            raise CodexAppServerError("Codex thread/start response did not contain a thread id")
        return thread_id

    def _request_turn(
        self,
        rpc: _JsonRpcProcess,
        thread_id: str,
        prompt: str,
        config: CodexExecutionConfig,
    ) -> tuple[str | None, str, dict[str, Any] | None, str]:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "approvalPolicy": "never",
        }
        if config.model:
            params["model"] = config.model
        if config.reasoning_effort:
            params["effort"] = config.reasoning_effort
        request_id = self._next_id()
        rpc.send({"id": request_id, "method": "turn/start", "params": params})

        response = self._await_response(rpc, request_id)
        result = response.get("result") or {}
        turn_id = self._find_string(result.get("turn"), "id") if isinstance(result, dict) else None
        text_parts: list[str] = []
        completed_items: list[str] = []
        usage: dict[str, Any] | None = self._find_mapping(result, "usage")
        status = self._find_string(result.get("turn"), "status") if isinstance(result, dict) else None
        if status == "completed":
            completed_items.extend(self._texts_from_items(result.get("turn", {}).get("items", [])))
            return turn_id, "\n\n".join(completed_items), usage, status

        deadline = time.monotonic() + self.settings.codex_timeout_seconds
        while time.monotonic() < deadline:
            message = rpc.receive(max(0.1, deadline - time.monotonic()))
            method = message.get("method")
            params = message.get("params") or {}
            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
            elif method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage") or params.get("usage") or usage
            elif method == "error":
                raise CodexAppServerError(str(params.get("error") or params))
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                turn_id = self._find_string(turn, "id") or turn_id
                status = self._find_string(turn, "status") or "completed"
                usage = self._find_mapping(turn, "usage") or usage
                completed_items.extend(self._texts_from_items(turn.get("items", [])))
                break

        if status != "completed":
            raise CodexAppServerError("Codex turn did not complete before the configured timeout")
        final_text = "\n\n".join(completed_items) if completed_items else "".join(text_parts)
        return turn_id, final_text, usage, status

    def _await_response(self, rpc: _JsonRpcProcess, request_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.settings.codex_timeout_seconds
        while time.monotonic() < deadline:
            message = rpc.receive(max(0.1, deadline - time.monotonic()))
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise CodexAppServerError(str(message["error"]))
            return message
        raise CodexAppServerError("Timed out waiting for Codex App Server response")

    @staticmethod
    def _next_id() -> str:
        return str(uuid4())

    @staticmethod
    def _find_string(value: Any, key: str) -> str | None:
        return value.get(key) if isinstance(value, dict) and isinstance(value.get(key), str) else None

    @staticmethod
    def _find_mapping(value: Any, key: str) -> dict[str, Any] | None:
        candidate = value.get(key) if isinstance(value, dict) else None
        return candidate if isinstance(candidate, dict) else None

    @staticmethod
    def _texts_from_items(items: Any) -> list[str]:
        if not isinstance(items, list):
            return []
        return [item["text"] for item in items if isinstance(item, dict) and item.get("type") == "agentMessage" and isinstance(item.get("text"), str)]
