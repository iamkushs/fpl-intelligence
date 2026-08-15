import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path(__file__).parents[3] / "scripts" / "symphony-runner-process.ps1"


class Handler(BaseHTTPRequestHandler):
    repository = "o/r"
    def do_GET(self):
        payload = {"status": "ok"} if self.path == "/health" else {"status": "running", "repository": self.repository}
        body = json.dumps(payload).encode(); self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *_): pass


def powershell(command):
    return subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",command],capture_output=True,text=True,check=True).stdout.strip()


def test_existing_runner_requires_health_compatibility_and_exact_lock_owner(tmp_path):
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    lock=tmp_path/"runner.lock"; lock.write_text(str(os.getpid()))
    try:
        base=f"http://127.0.0.1:{server.server_port}"
        yes=powershell(f". '{SCRIPT}'; Test-SymphonyCompatibleRunner -Repository 'o/r' -LockPath '{lock}' -StatusBase '{base}'")
        no=powershell(f". '{SCRIPT}'; Test-SymphonyCompatibleRunner -Repository 'wrong/repo' -LockPath '{lock}' -StatusBase '{base}'")
        assert yes == "True" and no == "False"
    finally: server.shutdown(); server.server_close(); thread.join()


def test_cleanup_stops_only_owned_temporary_process():
    child=subprocess.Popen(["powershell.exe","-NoProfile","-Command","Start-Sleep -Seconds 30"])
    try:
        powershell(f". '{SCRIPT}'; $p=Get-Process -Id {child.pid}; Stop-SymphonyOwnedProcess -Process $p -Owned $false")
        assert child.poll() is None
        powershell(f". '{SCRIPT}'; $p=Get-Process -Id {child.pid}; Stop-SymphonyOwnedProcess -Process $p -Owned $true")
        child.wait(timeout=5)
    finally:
        if child.poll() is None: child.kill()
