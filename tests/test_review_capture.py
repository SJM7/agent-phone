import base64
import json

import pytest

from agent_phone.review_capture import CHUNK_BYTES, ReviewCapture
from agent_phone.whiteboard import WhiteboardBridge


WEBM = b"\x1aE\xdf\xa3review-fixture"
PNG = b"\x89PNG\r\n\x1a\nframe-fixture"


def chunk(name, data, index=0, last=True):
    return {"name": name, "index": index, "last": last, "data": base64.b64encode(data).decode()}


def review():
    return {"version": 1, "startedAt": 1000, "endedAt": 6000, "status": "complete", "reason": "",
            "video": {"file": "review.webm", "bytes": len(WEBM), "mimeType": "video/webm"},
            "events": [{"type": "click", "atMs": 1000, "url": "https://user:pass@example.com/page?token=private#private",
                        "target": {"tag": "INPUT", "role": "textbox", "name": "private", "value": "private",
                                   "selector": "input[value=private]", "disabled": False}, "password": "private"}],
            "bookmarks": [{"number": 1, "atMs": 2000, "label": "Menu closes",
                           "frames": [{"file": "review-1-frame-1.png", "atMs": 1900}]}]}


def test_chunk_retry_order_and_partial_asset(tmp_path):
    capture = ReviewCapture(tmp_path)
    first = chunk("review.webm", WEBM[:4], last=False)
    assert capture.asset(first) == {"ok": True, "nextIndex": 1, "complete": False}
    assert capture.asset(first)["nextIndex"] == 1
    assert not (tmp_path / "review.webm").exists()
    for bad in (chunk("review.webm", b"changed", 0, False), chunk("review.webm", b"later", 2),
                chunk("../escape.png", PNG), chunk("review-51-frame-1.png", PNG),
                chunk("review.webm", WEBM[:4], 0, True), chunk("review-1-frame-1.png", b"wrong")):
        with pytest.raises(ValueError):
            capture.asset(bad)
    assert capture.asset(chunk("review.webm", WEBM[4:], 1))["complete"]
    assert (tmp_path / "review.webm").read_bytes() == WEBM
    assert capture.asset(first)["complete"]
    assert capture.total_bytes == len(WEBM)
    with pytest.raises(ValueError):
        capture.asset(chunk("review.webm", b"extra", 2))


@pytest.mark.parametrize("failed", [False, True])
def test_assets_and_metadata_fail_closed_then_retry(tmp_path, failed):
    b = WhiteboardBridge(tmp_path)
    b.request({"op": "heartbeat", "active": True, "sheetId": "sheet"})
    session = b.begin(None)
    begin = {"op": "review_begin", "id": session["id"], "sheetId": "sheet"}
    for invalid in ({**begin, "id": "old"}, {**begin, "sheetId": "other"}):
        with pytest.raises(ValueError):
            b.request(invalid)
    assert b.request(begin)["ok"]
    assert b.request(begin)["ok"]
    b.request({"op": "review_asset", "id": session["id"], **chunk("review.webm", WEBM)})
    b.finish(session)
    if failed:
        b.set_status(session, "failed", "Transcription failed")
    metadata = review()
    metadata["startedAt"] = session["startedAt"]
    metadata["endedAt"] = session["startedAt"] + 5000
    freeze = {"op": "freeze", "id": session["id"], "sheet": {"id": "sheet", "marks": []},
              "brief": "No drawn marks", "review": metadata}
    with pytest.raises(ValueError, match="not finished"):
        b.request(freeze)
    assert not session["visuals"].is_set()
    b.request({"op": "review_asset", "id": session["id"], **chunk("review-1-frame-1.png", PNG)})
    assert b.request(freeze)["ok"]
    assert b.request(freeze)["ok"]
    if failed:
        assert session["phase"] == "failed"
        assert (session["path"] / "review.md").exists()
        assert not (session["path"] / "brief.md").exists()
        return
    prompt = b.bundle(session, "The menu should remain open", timeout=0)
    assert "brief.md" in prompt
    assert "review.md" in (session["path"] / "brief.md").read_text()
    clean = json.loads((session["path"] / "review.json").read_text())
    assert "private" not in json.dumps(clean)
    assert clean["events"][0]["url"] == "https://example.com/page"
    assert clean["recordingOffsetMs"] == 0
    assert (session["path"] / "review.md").exists()
    with pytest.raises(ValueError):
        b.request({"op": "review_asset", "id": session["id"], **chunk("review-2-frame-1.png", PNG)})


def test_manifest_rejects_invalid_references_and_sensitive_key_logging(tmp_path):
    capture = ReviewCapture(tmp_path)
    capture.asset(chunk("review.webm", WEBM))
    capture.asset(chunk("review-1-frame-1.png", PNG))
    for field, value in (("video", {"file": "../video", "bytes": len(WEBM), "mimeType": "video/webm"}),
                         ("endedAt", float("nan")), ("endedAt", 900000),
                         ("events", [{"type": "keydown", "atMs": 100, "key": "a"}]),
                         ("bookmarks", [{"number": 1, "atMs": 1, "frames": [{"file": "../../file", "atMs": 1}]}]),
                         ("bookmarks", [{"number": 1, "atMs": 1}, {"number": 1, "atMs": 2}])):
        with pytest.raises(ValueError):
            capture.manifest({**review(), field: value}, 1000)
    interrupted = {**review(), "status": "interrupted", "video": None, "reason": "Tab closed"}
    assert capture.manifest(interrupted, 1000)["status"] == "interrupted"
    with pytest.raises(ValueError):
        capture.manifest(review(), 1_700_000_000_000)
    unknown = {**review(), "events": [{"type": "click", "atMs": 1,
               "target": {"name": "private", "selector": "input[value=private]"}}]}
    assert capture.manifest(unknown, 1000)["events"][0]["target"] == {}


def test_capture_storage_limits_do_not_append_rejected_bytes(tmp_path, monkeypatch):
    import agent_phone.review_capture as module
    capture = ReviewCapture(tmp_path)
    monkeypatch.setattr(module, "VIDEO_BYTES", len(WEBM))
    capture.asset(chunk("review.webm", WEBM, last=False))
    with pytest.raises(ValueError, match="limit"):
        capture.asset(chunk("review.webm", b"overflow", 1))
    assert (tmp_path / "review.webm.part").read_bytes() == WEBM
    assert capture.total_bytes == len(WEBM)
    with pytest.raises(ValueError):
        capture.asset(chunk("review-1-frame-1.png", PNG + b"x" * CHUNK_BYTES))
