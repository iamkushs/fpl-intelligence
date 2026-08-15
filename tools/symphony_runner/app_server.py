from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .config import RunnerConfig
from .models import (AppServerError, AppServerMessageTooLarge, AppServerProtocolError,
    AppServerThreadUnavailable, AppServerTimeout, AppServerTurnFailed,
    AppServerTurnInterrupted, TurnResult)
from .routing import CatalogModel, ModelCatalog

ToolHandler = Callable[[str, dict[str, Any]], Any]
StateHandler = Callable[[str], None]


def decode_json_message(value: bytes | str) -> dict[str, Any]:
    """Decode exactly one JSONL boundary message and validate its envelope."""
    if not isinstance(value, (bytes, str)):
        raise AppServerProtocolError(
            f"App Server JSONL message must be bytes or string, got {type(value).__name__}"
        )
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppServerError(f"Malformed App Server message: {value[:200]!r}") from exc
    if not isinstance(decoded, dict):
        raise AppServerProtocolError(
            f"App Server JSON-RPC message must be an object, got {type(decoded).__name__}"
        )
    return decoded


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppServerProtocolError(f"App Server field {field} must be an object, got {type(value).__name__}")
    return value


def _agent_output(turn: dict[str, Any]) -> str | None:
    items = turn.get("items")
    if not isinstance(items, list):
        raise AppServerProtocolError("turn/completed field params.turn.items must be an array")
    for item in reversed(items):
        if isinstance(item, dict) and item.get("type") == "agentMessage":
            output = item.get("text")
            if output is None:
                return None
            if not isinstance(output, str):
                raise AppServerProtocolError("agentMessage.text must be a string")
            if output.strip():
                return output
    return None


def executable_command(command: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    parts = tuple(command)
    if os.name == "nt" and parts and parts[0].lower() == "codex":
        shim = shutil.which("codex.cmd")
        if shim:
            return (os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", subprocess.list2cmdline([shim, *parts[1:]]))
        executable = shutil.which("codex.exe")
        if executable:
            return (executable, *parts[1:])
    return parts


class CodexAppServer:
    def __init__(self, config: RunnerConfig, cwd: Path, tools: list[dict[str, Any]], handler: ToolHandler):
        self.config, self.cwd, self.tools, self.handler = config, cwd, tools, handler
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._initialized = False
        self._active_thread_id: str | None = None
        self._catalog: ModelCatalog | None = None

    @staticmethod
    def version() -> str:
        result = subprocess.run(executable_command(["codex", "--version"]), capture_output=True, text=True, check=True)
        return result.stdout.strip()

    @staticmethod
    def validate_schema() -> str:
        if not shutil.which("codex"):
            raise AppServerError("codex executable not found")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(executable_command(["codex", "app-server", "generate-json-schema", "--experimental", "--out", directory]), capture_output=True, text=True)
            required = [Path(directory) / "v2" / "ThreadStartParams.json", Path(directory) / "v2" / "TurnStartParams.json", Path(directory) / "v2" / "ModelListResponse.json", Path(directory) / "DynamicToolCallParams.json"]
            if result.returncode or not all(path.is_file() for path in required):
                raise AppServerError("installed Codex App Server schema is incompatible")
        return CodexAppServer.version()

    async def start(self) -> None:
        env = self.config.sanitized_child_environment()
        command = executable_command(self.config.codex_command)
        self.process = await asyncio.create_subprocess_exec(*command, cwd=self.cwd, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=self.config.app_server_max_message_bytes,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    async def initialize(self) -> None:
        if not self.process: await self.start()
        if not self._initialized:
            await self._request("initialize", {"clientInfo": {"name": "fpl_symphony_windows", "title": "FPL Symphony Windows Runner", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
            await self._send({"method": "initialized", "params": {}}); self._initialized = True

    async def model_catalog(self) -> ModelCatalog:
        await self.initialize()
        if self._catalog is not None: return self._catalog
        values: list[CatalogModel] = []; cursor: str | None = None
        while True:
            params: dict[str, Any] = {"includeHidden": True}
            if cursor: params["cursor"] = cursor
            page = await self._request("model/list", params)
            data = page.get("data")
            if not isinstance(data, list) or not all(isinstance(value, dict) for value in data):
                raise AppServerProtocolError("model/list field result.data must be an array of objects")
            values.extend(CatalogModel.from_api(value) for value in data)
            cursor = page.get("nextCursor")
            if cursor is not None and not isinstance(cursor, str):
                raise AppServerProtocolError("model/list field result.nextCursor must be a string or null")
            if not cursor: break
        self._catalog = ModelCatalog(values); return self._catalog

    async def _send(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin: raise AppServerError("App Server is not running")
        self.process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        await self.process.stdin.drain()

    async def _read(self, timeout: float) -> dict[str, Any]:
        if not self.process or not self.process.stdout: raise AppServerError("App Server is not running")
        try: line = await asyncio.wait_for(self.process.stdout.readuntil(b"\n"), timeout)
        except TimeoutError as exc: raise AppServerTimeout("Codex App Server stalled") from exc
        except asyncio.LimitOverrunError as exc:
            raise AppServerMessageTooLarge(self.config.app_server_max_message_bytes, exc.consumed) from exc
        except asyncio.IncompleteReadError as exc:
            line = exc.partial
        if not line:
            detail = ""
            if self.process.stderr: detail = (await self.process.stderr.read()).decode(errors="replace")[-500:]
            raise AppServerError(f"Codex App Server exited unexpectedly: {detail}")
        return decode_json_message(line)

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id; self._next_id += 1
        await self._send({"method": method, "id": request_id, "params": params})
        while True:
            message = await self._read(self.config.read_timeout_ms / 1000)
            if message.get("id") == request_id:
                if "error" in message:
                    error = _object(message["error"], "error")
                    if error.get("code") == -32001 or "overload" in str(error).lower(): raise AppServerError("Server overloaded; retry later")
                    raise AppServerError(str(error))
                result = message.get("result")
                if not isinstance(result, dict):
                    raise AppServerProtocolError(
                        f"App Server response for {method} field result must be an object, got {type(result).__name__}"
                    )
                return result
            await self._handle_server_request(message)

    async def _handle_server_request(self, message: dict[str, Any]) -> bool:
        if message.get("method") != "item/tool/call" or "id" not in message: return False
        params = _object(message.get("params"), "params")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            raise AppServerProtocolError(
                f"item/tool/call field params.arguments must be an object, got {type(arguments).__name__}"
            )
        try:
            value = self.handler(str(params.get("tool", "")), arguments)
            result = {"success": True, "contentItems": [{"type": "inputText", "text": json.dumps(value, default=str)}]}
        except Exception as exc:
            result = {"success": False, "contentItems": [{"type": "inputText", "text": str(exc)}]}
        await self._send({"id": message["id"], "result": result})
        return True

    async def run_turn(self, prompt: str, thread_id: str | None = None, *, model: str | None = None,
                       effort: str | None = None,
                       on_thread: StateHandler | None = None, on_turn: StateHandler | None = None,
                       output_schema: dict[str, Any] | None = None, read_only: bool = False) -> TurnResult:
        await self.initialize()
        sandbox = "read-only" if read_only else self.config.thread_sandbox
        common = {"cwd": str(self.cwd), "approvalPolicy": self.config.approval_policy, "sandbox": sandbox,
            "runtimeWorkspaceRoots": [str(self.cwd)]}
        if thread_id and thread_id == self._active_thread_id:
            response = {"thread": {"id": thread_id}}
        elif thread_id:
            try: response = await self._request("thread/resume", {"threadId": thread_id, **common})
            except AppServerError as exc:
                detail = str(exc).lower()
                if any(value in detail for value in ("not found", "invalid thread", "unknown thread", "thread invalid")):
                    raise AppServerThreadUnavailable(f"Codex thread {thread_id} cannot be resumed: {exc}") from exc
                raise
        if not thread_id:
            response = await self._request("thread/start", {**common, "dynamicTools": self.tools, "model": model,
                "allowProviderModelFallback": False})
        thread = _object(response.get("thread", response), "thread response.thread")
        thread_id = str(thread.get("id") or thread.get("threadId") or "")
        if not thread_id: raise AppServerError("thread response did not contain an id")
        self._active_thread_id = thread_id
        if on_thread:
            on_thread(thread_id)
        turn_params = {"threadId": thread_id, "input": [{"type": "text", "text": prompt}],
            "model": model, "effort": effort,
            "cwd": str(self.cwd), "approvalPolicy": self.config.approval_policy,
            "sandboxPolicy": ({"type": "readOnly"} if read_only else {**self.config.sandbox_policy, "writableRoots": [str(self.cwd)]}),
            "runtimeWorkspaceRoots": [str(self.cwd)]}
        if output_schema is not None: turn_params["outputSchema"] = output_schema
        turn = await self._request("turn/start", turn_params)
        turn_value = _object(turn.get("turn", turn), "turn response.turn")
        turn_id = str(turn_value.get("id") or turn_value.get("turnId") or "")
        if not turn_id: raise AppServerError("turn response did not contain an id")
        if on_turn:
            on_turn(turn_id)
        deadline = time.monotonic() + self.config.turn_timeout_ms / 1000; events = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise AppServerTimeout("Codex turn exceeded overall timeout")
            timeout = min(remaining, self.config.stall_timeout_ms / 1000) if self.config.stall_timeout_ms > 0 else remaining
            message = await self._read(timeout); events += 1
            if await self._handle_server_request(message): continue
            if message.get("method") == "turn/completed":
                params = _object(message.get("params"), "turn/completed.params")
                completed = _object(params.get("turn"), "turn/completed.params.turn")
                status_value = completed.get("status")
                if not isinstance(status_value, str):
                    raise AppServerProtocolError("turn/completed field params.turn.status must be a string")
                status = status_value.lower()
                error = completed.get("error")
                if status == "failed":
                    raise AppServerTurnFailed(f"Codex turn failed: {error or status}")
                if status == "interrupted":
                    raise AppServerTurnInterrupted(f"Codex turn interrupted: {error or status}")
                if status != "completed":
                    raise AppServerProtocolError(f"turn/completed reported unsupported status {status!r}")
                if error:
                    raise AppServerProtocolError("completed Codex turn unexpectedly contained an error")
                output = _agent_output(completed)
                if output is None:
                    raise AppServerProtocolError("completed Codex turn contained no non-null agent output")
                return TurnResult(thread_id, turn_id or str(completed.get("id", "")), status, events, None, output)

    async def close(self) -> None:
        if not self.process: return
        process = self.process
        if process.stdin:
            process.stdin.close()
            try: await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError): pass
        if process.returncode is None:
            process.terminate()
            try: await asyncio.wait_for(process.wait(), 5)
            except TimeoutError: process.kill(); await process.wait()
        if process.stdout: await process.stdout.read()
        if process.stderr: await process.stderr.read()
        self.process = None; self._initialized = False; self._active_thread_id = None; self._catalog = None

    async def __aenter__(self) -> CodexAppServer: await self.start(); return self
    async def __aexit__(self, *_: object) -> None: await self.close()
