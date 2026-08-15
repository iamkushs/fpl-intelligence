from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


class StatusServer:
    def __init__(self, host: str, port: int, state: Callable[[], dict[str, Any]]):
        self.host, self.port, self.state = host, port, state
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/health": payload: Any = {"status": "ok"}
                elif self.path == "/state": payload = outer.state()
                elif self.path == "/":
                    body = "<html><body><h1>FPL Symphony</h1><pre>" + json.dumps(outer.state(), indent=2) + "</pre></body></html>"
                    self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode()); return
                else: self.send_error(404); return
                body = json.dumps(payload, default=str).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
            def log_message(self, *_: object) -> None: return
        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> None: self.thread.start()
    def stop(self) -> None: self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(timeout=3)
