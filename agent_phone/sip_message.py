"""Minimal SIP message parsing/serialization (RFC 3261 subset).

Just enough SIP to act as the phone's registrar: parse requests/responses,
expand compact header forms, and split header values and auth challenges.
"""
from __future__ import annotations

from dataclasses import dataclass

COMPACT = {
    "v": "Via", "f": "From", "t": "To", "i": "Call-ID", "m": "Contact",
    "l": "Content-Length", "c": "Content-Type", "k": "Supported",
    "s": "Subject", "e": "Content-Encoding",
}


def _canon(name: str) -> str:
    """Canonical lowercase key for case-insensitive, compact-aware matching."""
    lowered = name.lower()
    return COMPACT.get(lowered, lowered).lower()


@dataclass
class SipMessage:
    is_request: bool
    method: str | None
    uri: str | None
    status_code: int | None
    reason: str | None
    version: str
    headers: list[tuple[str, str]]
    body: bytes

    def get(self, name: str) -> str | None:
        key = _canon(name)
        for hname, value in self.headers:
            if _canon(hname) == key:
                return value
        return None

    def get_all(self, name: str) -> list[str]:
        key = _canon(name)
        return [v for hname, v in self.headers if _canon(hname) == key]

    def set(self, name: str, value: str) -> None:
        key = _canon(name)
        for i, (hname, _) in enumerate(self.headers):
            if _canon(hname) == key:
                self.headers[i] = (hname, value)
                return
        self.headers.append((name, value))

    def add(self, name: str, value: str) -> None:
        self.headers.append((name, value))


def parse_message(data: bytes) -> SipMessage:
    # Locate the first blank line, tolerating CRLF and bare LF.
    sep_at = body_at = None
    i = 0
    while i < len(data):
        if data[i : i + 4] == b"\r\n\r\n":
            sep_at, body_at = i, i + 4
            break
        if data[i : i + 2] == b"\n\n":
            sep_at, body_at = i, i + 2
            break
        if data[i : i + 3] in (b"\n\r\n", b"\r\n\n"):
            sep_at, body_at = i, i + 3
            break
        i += 1
    if sep_at is None:
        raise ValueError("no blank line terminating headers")

    head = data[:sep_at].decode("latin-1")
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in head.split("\n")]

    start = lines[0]
    if start.startswith("SIP/"):
        parts = start.split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise ValueError(f"bad response start line: {start!r}")
        version, code = parts[0], int(parts[1])
        reason = parts[2] if len(parts) == 3 else ""
        msg = SipMessage(False, None, None, code, reason, version, [], b"")
    else:
        parts = start.split(" ")
        if len(parts) != 3 or not parts[2].startswith("SIP/"):
            raise ValueError(f"bad request start line: {start!r}")
        msg = SipMessage(True, parts[0], parts[1], None, None, parts[2], [], b"")

    for line in lines[1:]:
        if not line:
            continue
        if line[0] in " \t":
            if not msg.headers:
                raise ValueError("continuation line before any header")
            hname, value = msg.headers[-1]
            msg.headers[-1] = (hname, value + " " + line.strip())
            continue
        name, colon, value = line.partition(":")
        if not colon:
            raise ValueError(f"header line without colon: {line!r}")
        name = name.strip()
        stored = COMPACT.get(name.lower(), name)
        msg.headers.append((stored, value.strip()))

    rest = data[body_at:]
    length_hdr = msg.get("Content-Length")
    if length_hdr is not None:
        if not length_hdr.isdigit():
            raise ValueError(f"bad Content-Length: {length_hdr!r}")
        length = int(length_hdr)
        if len(rest) < length:
            raise ValueError("body shorter than Content-Length")
        msg.body = rest[:length]
    else:
        msg.body = rest
    return msg


def serialize(msg: SipMessage) -> bytes:
    msg.set("Content-Length", str(len(msg.body)))
    if msg.is_request:
        start = f"{msg.method} {msg.uri} {msg.version}"
    else:
        start = f"{msg.version} {msg.status_code} {msg.reason}"
    lines = [start] + [f"{n}: {v}" for n, v in msg.headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + msg.body


def _split_top_level(text: str, sep: str, brackets: bool) -> list[str]:
    """Split on `sep` outside double quotes (and optionally <...> brackets)."""
    parts: list[str] = []
    buf: list[str] = []
    in_quotes = False
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if in_quotes:
            if ch == "\\" and i + 1 < len(text):
                buf.append(ch)
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_quotes = False
            buf.append(ch)
        elif ch == '"':
            in_quotes = True
            buf.append(ch)
        elif brackets and ch == "<":
            depth += 1
            buf.append(ch)
        elif brackets and ch == ">" and depth:
            depth -= 1
            buf.append(ch)
        elif ch == sep and not depth:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return value


def parse_params(value: str) -> tuple[str, dict[str, str | None]]:
    segments = _split_top_level(value, ";", brackets=True)
    params: dict[str, str | None] = {}
    for seg in segments[1:]:
        key, eq, val = seg.partition("=")
        if eq:
            params[key.strip().lower()] = _unquote(val.strip())
        else:
            params[key.strip().lower()] = None
    return segments[0].strip(), params


def parse_auth(value: str) -> tuple[str, dict[str, str]]:
    scheme, _, rest = value.strip().partition(" ")
    params: dict[str, str] = {}
    for seg in _split_top_level(rest, ",", brackets=False):
        key, eq, val = seg.partition("=")
        if not eq:
            raise ValueError(f"auth param without '=': {seg.strip()!r}")
        params[key.strip().lower()] = _unquote(val.strip())
    return scheme, params
