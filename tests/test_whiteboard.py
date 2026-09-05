import base64
import json
import urllib.request
import urllib.error

import pytest

from agent_phone.whiteboard import WhiteboardBridge
from agent_phone.hookserver import HookServer, HookCallbacks


def frozen(s, number=1):
    return {"op": "freeze", "id": s["id"], "brief": "Reference 1",
            "sheet": {"id": "sheet", "marks": [{"number": number,
            "image": "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()}]}}


def test_bundle_identity_and_retries(tmp_path):
    b = WhiteboardBridge(tmp_path)
    assert b.begin(None) is None
    b.request({"op": "heartbeat", "active": True, "sheetId": "sheet"})
    s = b.begin(None)
    with pytest.raises(ValueError):
        b.begin(None)
    b.finish(s)
    assert b.lease is None
    b.request({"op": "heartbeat", "active": True, "sheetId": "sheet"})
    assert b.lease is None
    with pytest.raises(ValueError):
        b.request({**frozen(s), "id": "wrong"})
    with pytest.raises(ValueError):
        b.request(frozen(s, "../../escape"))
    assert b.request(frozen(s))["ok"]
    assert b.request(frozen(s))["ok"]
    prompt = b.bundle(s, "Move 1 beside 2")
    assert str(s["path"] / "brief.md") in prompt
    assert (s["path"] / "mark-1.png").exists()
    assert "image" not in json.loads((s["path"] / "context.json").read_text())["marks"][0]
    assert WhiteboardBridge(tmp_path).token == b.token


def test_visual_timeout_keeps_narration(tmp_path):
    b = WhiteboardBridge(tmp_path)
    b.request({"op": "heartbeat", "active": True, "sheetId": "sheet"})
    s = b.begin(None)
    b.finish(s)
    assert b.bundle(s, "My critique", timeout=0) is None
    assert s["phase"] == "failed"
    assert (s["path"] / "narration.txt").read_text() == "My critique"


def test_http_auth_and_web_origin(tmp_path):
    b = WhiteboardBridge(tmp_path)
    server = HookServer(HookCallbacks(), port=0, whiteboard=b)
    server.start()
    def post(token, origin):
        req = urllib.request.Request(f"http://127.0.0.1:{server.port}/whiteboard",
            data=b'{"op":"heartbeat","active":false}',
            headers={"Authorization": "Bearer " + token, "Origin": origin})
        return urllib.request.urlopen(req)
    try:
        for token, origin in [("bad", "chrome-extension://test"), (b.token, "https://example.com")]:
            with pytest.raises(urllib.error.HTTPError) as exc:
                post(token, origin)
            assert exc.value.code == 403
        with post(b.token, "chrome-extension://test") as response:
            assert json.load(response)["phase"] == "ready"
    finally:
        server.stop()
