import json
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
thread = "thread-fake"
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if mode == "malformed": print("not-json", flush=True); mode = "normal"; continue
    if mode == "exit": sys.exit(3)
    if method == "initialize": print(json.dumps({"id": message["id"], "result": {"platformOs": "windows"}}), flush=True)
    elif method == "initialized": pass
    elif method == "thread/start": print(json.dumps({"id": message["id"], "result": {"thread": {"id": thread}}}), flush=True)
    elif method == "thread/resume":
        thread = message["params"]["threadId"]
        print(json.dumps({"id": message["id"], "result": {"thread": {"id": thread}}}), flush=True)
    elif method == "turn/start":
        print(json.dumps({"id": message["id"], "result": {"turn": {"id": "turn-fake"}}}), flush=True)
        if mode == "stall": time.sleep(10); continue
        print(json.dumps({"id": 900, "method": "item/tool/call", "params": {"tool": "read_issue", "arguments": {"issue_number": 1}, "callId": "c", "threadId": thread, "turnId": "turn-fake"}}), flush=True)
    elif message.get("id") == 900:
        print(json.dumps({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": "turn-fake", "status": "completed"}}}), flush=True)
