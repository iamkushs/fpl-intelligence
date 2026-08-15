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
from .models import AppServerError, AppServerMessageTooLarge, AppServerTimeout, TurnResult
from .routing import CatalogModel, ModelCatalog

ToolHandler = Callable[[str, dict[str, Any]], Any]
StateHandler = Callable[[str], None]


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
            values.extend(CatalogModel.from_api(value) for value in page.get("data", []))
            cursor = page.get("nextCursor")
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
        try: return json.loads(line)
        except json.JSONDecodeError as exc: raise AppServerError(f"Malformed App Server message: {line[:200]!r}") from exc

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id; self._next_id += 1
        await self._send({"method": method, "id": request_id, "params": params})
        while True:
            message = await self._read(self.config.read_timeout_ms / 1000)
            if message.get("id") == request_id:
                if "error" in message:
                    error = message["error"]
                    if error.get("code") == -32001 or "overload" in str(error).lower(): raise AppServerError("Server overloaded; retry later")
                    raise AppServerError(str(error))
                return message.get("result", {})
            await self._handle_server_request(message)

    async def _handle_server_request(self, message: dict[str, Any]) -> bool:
        if message.get("method") != "item/tool/call" or "id" not in message: return False
        params = message.get("params", {})
        try:
            value = self.handler(str(params.get("tool", "")), dict(params.get("arguments") or {}))
            result = {"success": True, "contentItems": [{"type": "inputText", "text": json.dumps(value, default=str)}]}
        except Exception as exc:
            result = {"success": False, "contentItems": [{"type": "inputText", "text": str(exc)}]}
        await self._send({"id": message["id"], "result": result})
        return True

    async def run_turn(self, prompt: str, thread_id: str | None = None, *, model: str | None = None,
                       effort: str | None = None,
                       on_thread: StateHandler | None = None, on_turn: StateHandler | None = None) -> TurnResult:
        await self.initialize()
        common = {"cwd": str(self.cwd), "approvalPolicy": self.config.approval_policy, "sandbox": self.config.thread_sandbox,
            "runtimeWorkspaceRoots": [str(self.cwd)]}
        if thread_id and thread_id == self._active_thread_id:
            response = {"thread": {"id": thread_id}}
        elif thread_id:
            try: response = await self._request("thread/resume", {"threadId": thread_id, **common})
            except AppServerError: thread_id = None
        if not thread_id:
            response = await self._request("thread/start", {**common, "dynamicTools": self.tools, "model": model,
                "allowProviderModelFallback": False})
        thread = response.get("thread", response)
        thread_id = str(thread.get("id") or thread.get("threadId") or "")
        if not thread_id: raise AppServerError("thread response did not contain an id")
        self._active_thread_id = thread_id
        if on_thread:
            on_thread(thread_id)
        turn = await self._request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": prompt}],
            "model": model, "effort": effort,
            "cwd": str(self.cwd), "approvalPolicy": self.config.approval_policy, "sandboxPolicy": {**self.config.sandbox_policy, "writableRoots": [str(self.cwd)]},
            "runtimeWorkspaceRoots": [str(self.cwd)]})
        turn_value = turn.get("turn", turn); turn_id = str(turn_value.get("id") or turn_value.get("turnId") or "")
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
                completed = message.get("params", {}).get("turn", {})
                status = str(completed.get("status", "completed"))
                error = completed.get("error")
                result = TurnResult(thread_id, turn_id or str(completed.get("id", "")), status, events, str(error) if error else None)
                if status.lower() not in {"completed", "complete"} or error:
                    raise AppServerError(f"Codex turn failed: {error or status}")
                return result

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
