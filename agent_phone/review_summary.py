import re


def _escape(value):
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))
    return re.sub(r"([\\`*_{}\[\]()#+!|<>~])", r"\\\1", text)


def _seconds(value):
    return f"{float(value) / 1000:.1f}s"


def _link_text(value):
    return _escape(value).replace("\n", " ")


def _event_line(event):
    line = f"{_escape(event.get('type', 'event'))} at {_seconds(event.get('atMs', 0))}"
    if event.get("type") == "keydown" and event.get("key"):
        line = f"keydown {_escape(event['key'])} at {_seconds(event.get('atMs', 0))}"
    target = event.get("target") or {}
    details = []
    for key in ("role", "tag", "name"):
        if target.get(key): details.append(_escape(target[key]))
    if details: line += " (" + ", ".join(details) + ")"
    if event.get("url"): line += f" — {_escape(event['url'])}"
    scroll = event.get("scroll") or {}
    if scroll: line += f" — scroll ({_escape(scroll.get('x', 0))}, {_escape(scroll.get('y', 0))})"
    return f"- {line}"


def render_review_summary(review) -> str:
    duration_ms = max(0, review["endedAt"] - review["startedAt"])
    lines = ["# Agent Phone review", f"Status: {_escape(review['status'])}", f"Reason: {_escape(review['reason'])}", f"Duration: {_seconds(duration_ms)}"]
    video = review.get("video")
    lines.append(f"Video: [{_link_text(video['file'])}]({_link_text(video['file'])})" if video else "Video: unavailable")
    bookmarks = review.get("bookmarks", [])
    for index, bookmark in enumerate(bookmarks):
        at = bookmark["atMs"]
        lines += ["", f"## Bookmark {bookmark['number']} — {_escape(bookmark['label'])} ({_seconds(at)})"]
        end = bookmarks[index + 1]["atMs"] if index + 1 < len(bookmarks) else duration_ms
        if video:
            lines.append(f"Suggested video clip: {_seconds(max(0, at - 20000))}–{_seconds(min(duration_ms, end))}")
        for frame in bookmark.get("frames", []):
            file = _link_text(frame["file"])
            lines.append(f"![Frame at {_seconds(frame['atMs'])}]({file})")
        nearby = [event for event in review.get("events", []) if abs(event.get("atMs", 0) - at) <= 20000][:20]
        lines.extend(_event_line(event) for event in nearby)
    lines += ["", "Timing caveat: state capture can be delayed relative to handset narration and does not establish exact word alignment."]
    return "\n".join(lines) + "\n"
