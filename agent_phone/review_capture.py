"""Bounded local review assets and their validated handoff manifest."""
from __future__ import annotations

import base64
import hashlib
import math
import pathlib
import re
import urllib.parse


CHUNK_BYTES = 512 * 1024
VIDEO_BYTES = 128 * 1024 * 1024
TOTAL_BYTES = 192 * 1024 * 1024
PNG_BYTES = 2 * 1024 * 1024
FRAME_NAME = re.compile(r"review-([1-9]|[1-4][0-9]|50)-frame-([1-5])\.png\Z")
EVENT_TYPES = {"click", "pointerenter", "pointerleave", "scroll", "keydown", "dragstart", "drop"}
NAV_KEYS = {"Tab", "Enter", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
            "Home", "End", "PageUp", "PageDown", "Space"}


def _number(value, minimum=0, maximum=1e15):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("Invalid review number")
    return value


def _text(value, limit):
    if not isinstance(value, str):
        raise ValueError("Invalid review text")
    return re.sub(r"[\x00-\x1f\x7f]", " ", value)[:limit]


def _url(value):
    value = _text(value, 4000)
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Invalid review URL")
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        raise ValueError("Invalid review URL") from None


def _event(event, duration):
    if not isinstance(event, dict) or event.get("type") not in EVENT_TYPES:
        raise ValueError("Invalid review interaction")
    clean = {"type": event["type"], "atMs": _number(event.get("atMs"), 0, duration + 2000)}
    if "url" in event:
        clean["url"] = _url(event["url"])
    if clean["type"] == "keydown":
        if event.get("key") not in NAV_KEYS:
            raise ValueError("Only navigation keys may be recorded")
        clean["key"] = event["key"]
    for name, fields, minimum, maximum in (("viewport", ("width", "height"), 0, 20000),
                                          ("scroll", ("x", "y"), -100000, 100000)):
        if name in event:
            if not isinstance(event[name], dict):
                raise ValueError("Invalid review geometry")
            clean[name] = {k: _number(event[name][k], minimum, maximum) for k in fields if k in event[name]}
    target = event.get("target")
    if target is not None:
        if not isinstance(target, dict):
            raise ValueError("Invalid review target")
        item = {k: _text(target[k], limit) for k, limit in
                (("tag", 32), ("role", 32), ("name", 80), ("testId", 80), ("selector", 200)) if k in target}
        if (not item.get("tag") and not item.get("role")) or item.get("tag", "").upper() in ("INPUT", "TEXTAREA") or item.get("role", "").lower() in ("textbox", "searchbox"):
            for key in ("name", "testId", "selector"):
                item.pop(key, None)
        for key in ("expanded", "disabled"):
            if key in target:
                if type(target[key]) is not bool:
                    raise ValueError("Invalid review element state")
                item[key] = target[key]
        if "bounds" in target:
            if not isinstance(target["bounds"], dict):
                raise ValueError("Invalid review bounds")
            item["bounds"] = {k: _number(target["bounds"][k], 0 if k in ("width", "height") else -100000, 100000)
                              for k in ("x", "y", "width", "height") if k in target["bounds"]}
        clean["target"] = item
    return clean


class ReviewCapture:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.files = {}
        self.total_bytes = 0

    def asset(self, payload):
        name = payload.get("name")
        if not isinstance(name, str) or (name != "review.webm" and not FRAME_NAME.fullmatch(name)):
            raise ValueError("Invalid review asset name")
        index, last, encoded = payload.get("index"), payload.get("last"), payload.get("data")
        if type(index) is not int or not 0 <= index < 1024 or type(last) is not bool:
            raise ValueError("Invalid review chunk")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((CHUNK_BYTES + 2) // 3):
            raise ValueError("Review chunk exceeds 512 KiB")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid review chunk encoding") from None
        if len(data) > CHUNK_BYTES or not data:
            raise ValueError("Empty or oversized review chunk")
        fingerprint = (hashlib.sha256(data).digest(), last)
        record = self.files.get(name)
        if record and index < len(record["chunks"]):
            if record["chunks"][index] != fingerprint:
                raise ValueError("Review chunk retry does not match")
            return {"ok": True, "nextIndex": len(record["chunks"]), "complete": record["complete"]}
        expected = len(record["chunks"]) if record else 0
        if index != expected or (record and record["complete"]):
            raise ValueError("Review chunks must arrive in order")
        limit = VIDEO_BYTES if name == "review.webm" else PNG_BYTES
        old_size = record["bytes"] if record else 0
        if old_size + len(data) > limit or self.total_bytes + len(data) > TOTAL_BYTES:
            raise ValueError("Review recording storage limit reached")
        if not record:
            signature = b"\x1aE\xdf\xa3" if name == "review.webm" else b"\x89PNG\r\n\x1a\n"
            if not data.startswith(signature):
                raise ValueError("Review asset has the wrong format")
        part = self.path / (name + ".part")
        with part.open("ab" if record else "xb") as output:
            output.write(data)
        if not record:
            record = {"chunks": [], "bytes": 0, "complete": False}
            self.files[name] = record
        record["chunks"].append(fingerprint)
        record["bytes"] += len(data)
        self.total_bytes += len(data)
        if last:
            part.replace(self.path / name)
            record["complete"] = True
        return {"ok": True, "nextIndex": len(record["chunks"]), "complete": record["complete"]}

    def _complete(self, name):
        record = self.files.get(name)
        if not record or not record["complete"]:
            raise ValueError("Review asset has not finished uploading")
        return record

    def manifest(self, review, phone_started_at, phone_finished_at=None):
        if not isinstance(review, dict) or type(review.get("version")) is not int or review["version"] != 1:
            raise ValueError("Unsupported review manifest")
        start_limit = phone_finished_at + 2000 if phone_finished_at is not None else phone_started_at + 610000
        start = _number(review.get("startedAt"), max(0, phone_started_at - 2000), start_limit)
        end_limit = min(start + 610000, phone_finished_at + 120000) if phone_finished_at is not None else start + 610000
        end = _number(review.get("endedAt"), start, end_limit)
        duration = end - start
        status = review.get("status")
        if status not in ("complete", "interrupted", "limit"):
            raise ValueError("Invalid review status")
        clean = {"version": 1, "startedAt": start, "endedAt": end, "status": status,
                 "reason": _text(review.get("reason", ""), 500),
                 "recordingOffsetMs": start - phone_started_at, "video": None}
        video = review.get("video")
        if video is not None:
            if not isinstance(video, dict) or video.get("file") != "review.webm" or video.get("mimeType") != "video/webm":
                raise ValueError("Invalid review video")
            actual = self._complete("review.webm")["bytes"]
            if type(video.get("bytes")) is not int or video["bytes"] != actual:
                raise ValueError("Review video byte count does not match")
            clean["video"] = {"file": "review.webm", "bytes": actual, "mimeType": "video/webm"}
        elif status == "complete":
            raise ValueError("A complete review requires its video")
        events, bookmarks = review.get("events", []), review.get("bookmarks", [])
        if not isinstance(events, list) or len(events) > 1000 or not isinstance(bookmarks, list) or len(bookmarks) > 50:
            raise ValueError("Review event or bookmark limit exceeded")
        clean["events"] = sorted((_event(e, duration) for e in events), key=lambda e: e["atMs"])
        clean["bookmarks"] = []
        numbers = set()
        for bookmark in bookmarks:
            if not isinstance(bookmark, dict):
                raise ValueError("Invalid review bookmark")
            n = bookmark.get("number")
            if type(n) is not int or not 1 <= n <= 50 or n in numbers:
                raise ValueError("Invalid review bookmark number")
            numbers.add(n)
            frames = bookmark.get("frames", [])
            if not isinstance(frames, list) or len(frames) > 5:
                raise ValueError("Too many review frames")
            clean_frames, filenames = [], set()
            for frame in frames:
                if not isinstance(frame, dict):
                    raise ValueError("Invalid review frame")
                name = frame.get("file")
                match = FRAME_NAME.fullmatch(name) if isinstance(name, str) else None
                if not match or int(match[1]) != n or name in filenames:
                    raise ValueError("Invalid review frame reference")
                filenames.add(name)
                self._complete(name)
                clean_frames.append({"file": name, "atMs": _number(frame.get("atMs"), 0, duration + 2000)})
            clean["bookmarks"].append({"number": n, "atMs": _number(bookmark.get("atMs"), 0, duration + 2000),
                                       "label": _text(bookmark.get("label", ""), 120),
                                       "frames": sorted(clean_frames, key=lambda f: f["atMs"])})
        clean["bookmarks"].sort(key=lambda b: b["atMs"])
        return clean
