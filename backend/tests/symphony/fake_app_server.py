import json
import os
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
size = int(sys.argv[2]) if len(sys.argv) > 2 else 0
thread = "thread-fake"
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if mode == "malformed": print("not-json", flush=True); mode = "normal"; continue
    if mode == "exit": sys.exit(3)
    if method == "initialize":
        response = json.dumps({"id": message["id"], "result": {"platformOs": "windows", "payload": ("🙂" if mode == "large_utf8" else "x") * size}}, ensure_ascii=False).encode() + b"\n"
        if mode in {"large", "large_utf8"}:
            for offset in range(0, len(response), 7919):
                os.write(sys.stdout.fileno(), response[offset:offset + 7919])
        else:
            os.write(sys.stdout.fileno(), response)
    elif method == "initialized": pass
    elif method == "model/list":
        def item(name, efforts): return {"id": name, "model": name, "displayName": name, "description": "", "hidden": False, "isDefault": name == "gpt-5.5", "defaultReasoningEffort": efforts[0], "supportedReasoningEfforts": [{"reasoningEffort": effort, "description": ""} for effort in efforts]}
        data = [item("gpt-5.5", ["medium", "high"]), item("gpt-5.6-luna", ["medium"]), item("gpt-5.6-terra", ["medium", "high"]), item("gpt-5.6-sol", ["high"])]
        if mode == "paginate":
            cursor = message.get("params", {}).get("cursor")
            data, next_cursor = (data[:2], "page-2") if not cursor else (data[2:], None)
        else: next_cursor = None
        print(json.dumps({"id": message["id"], "result": {"data": data, "nextCursor": next_cursor}}), flush=True)
    elif method == "thread/start": print(json.dumps({"id": message["id"], "result": {"thread": {"id": thread}}}), flush=True)
    elif method == "thread/resume":
        if mode == "resume_not_found":
            print(json.dumps({"id": message["id"], "error": {"code": -32602, "message": "thread not found"}}), flush=True); continue
        thread = message["params"]["threadId"]
        print(json.dumps({"id": message["id"], "result": {"thread": {"id": thread}}}), flush=True)
    elif method == "turn/start":
        if mode == "validate_model" and (message["params"].get("model") != "gpt-5.5" or message["params"].get("effort") != "medium"):
            print(json.dumps({"id": message["id"], "error": {"code": -32602, "message": "missing model/effort"}}), flush=True); continue
        print(json.dumps({"id": message["id"], "result": {"turn": {"id": "turn-fake"}}}), flush=True)
        if mode == "crash_after_turn": sys.exit(4)
        if mode == "stall": time.sleep(10); continue
        arguments = None if mode == "missing_arguments" else {"issue_number": 1, "filters": ["open"]}
        print(json.dumps({"id": 900, "method": "item/tool/call", "params": {"tool": "read_issue", "arguments": arguments, "callId": "c", "threadId": thread, "turnId": "turn-fake"}}), flush=True)
    elif message.get("id") == 900:
        status = "failed" if mode == "failed" else "interrupted" if mode == "interrupted" else "completed"
        text = None if mode == "null_agent" else "HOST HANDOFF READY"
        turn = {"id": "turn-fake", "status": status, "items": [{"type": "agentMessage", "id": "agent-1", "text": text}], "error": {"message": "boom"} if status == "failed" else None}
        print(json.dumps({"method": "turn/completed", "params": {"threadId": thread, "turn": turn}}), flush=True)
