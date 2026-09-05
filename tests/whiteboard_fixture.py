"""Disposable bridge process controlled by the browser integration test's stdin."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agent_phone.whiteboard import WhiteboardBridge
from agent_phone.hookserver import HookServer, HookCallbacks

b = WhiteboardBridge(pathlib.Path(sys.argv[1]))
server = HookServer(HookCallbacks(), port=0, whiteboard=b)
server.start()
print(json.dumps({"port": server.port, "token": b.token}), flush=True)
try:
    for line in sys.stdin:
        op = json.loads(line)["op"]
        if op == "begin":
            s = b.begin(None)
            result = {"id": s["id"]} if s else {"error": "No active sheet"}
        elif op == "finish":
            b.finish(s)
            result = {"ok": True}
        elif op == "bundle":
            prompt = b.bundle(s, "Move reference 1 to the tip of reference 2.")
            b.set_status(s, "delivered", "Test handoff saved (no terminal input)")
            result = {"prompt": prompt, "path": str(s["path"])}
        print(json.dumps(result), flush=True)
finally:
    server.stop()
