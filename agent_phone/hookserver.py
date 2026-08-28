"""Embedded HTTP server that receives Claude Code hook calls and Polycom phone events.

Claude Code hook scripts POST the hook's stdin JSON to /hook/stop and
/hook/user-prompt-submit; the phone POSTs apps.telNotification XML to
/phone/event. Callbacks are dispatched on worker threads so responses
return immediately and callback errors never reach the client.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8489


def _noop(_payload: dict) -> None:
    pass


@dataclass
class HookCallbacks:
    on_turn_done: Callable[[dict], None] = _noop
    on_turn_start: Callable[[dict], None] = _noop
    on_phone_event: Callable[[dict], None] = _noop


def parse_hook_payload(body: bytes) -> dict:
    """Parse the JSON Claude Code passes to hooks on stdin (forwarded verbatim)."""
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"hook payload must be a JSON object, got {type(payload).__name__}")
    return payload


def parse_phone_event_xml(body: bytes | str) -> dict:
    """Parse a Polycom apps.telNotification XML body into a flat event dict.

    The first child of the root is the event element (OffHookEvent,
    CallStateChangeEvent, ...); its tag becomes ``type`` and its child
    elements are flattened to str -> str entries.
    """
    root = ET.fromstring(body)
    event_el = next(iter(root), None)
    if event_el is None:
        raise ValueError(f"no event element under <{root.tag}>")
    event = {"type": event_el.tag}
    for child in event_el:
        event[child.tag] = "".join(child.itertext()).strip()
    return event


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def callbacks(self):
        return self.server.hook_callbacks

    def log_message(self, format, *args):
        log.debug("%s - %s", self.address_string(), format % args)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _respond(self, status: int, body: bytes = b"", content_type: str = "text/plain") -> None:
        self.send_response(status)
        if body:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _dispatch(self, callback_name: str, payload: dict) -> None:
        callback = getattr(self.callbacks, callback_name)

        def run():
            try:
                callback(payload)
            except Exception:
                log.exception("%s callback failed for payload %r", callback_name, payload)

        threading.Thread(target=run, name=f"hookserver-{callback_name}", daemon=True).start()

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, b'{"ok": true}', "application/json")
        else:
            self._respond(404, b"not found\n")

    def do_POST(self):
        body = self._read_body()
        path, _, query = self.path.partition("?")
        if path == "/hook/stop":
            self._handle_hook(body, "on_turn_done", query)
        elif path == "/hook/user-prompt-submit":
            self._handle_hook(body, "on_turn_start", query)
        elif path == "/phone/event":
            self._handle_phone_event(body)
        else:
            self._respond(404, b"not found\n")

    def _handle_hook(self, body: bytes, callback_name: str,
                     query: str = "") -> None:
        try:
            session = parse_hook_payload(body)
        except (ValueError, UnicodeDecodeError) as exc:
            log.warning("bad hook payload on %s: %s", self.path, exc)
            self._respond(400, b"bad hook payload\n")
            return
        # ?agent=codex tags which harness sent the hook (default claude)
        params = dict(urllib.parse.parse_qsl(query))
        session["_agent"] = params.get("agent", "claude")
        self._respond(204)
        self._dispatch(callback_name, session)

    def _handle_phone_event(self, body: bytes) -> None:
        try:
            event = parse_phone_event_xml(body)
        except (ET.ParseError, ValueError) as exc:
            log.warning("bad phone event XML: %s", exc)
            self._respond(400, b"bad phone event\n")
            return
        self._respond(204)
        self._dispatch("on_phone_event", event)


class HookServer:
    """Threaded HTTP server the agent-phone daemon embeds.

    Pass ``port=0`` to bind an ephemeral port; read the bound port back
    from ``server.port``.
    """

    def __init__(self, callbacks: HookCallbacks, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.callbacks = callbacks
        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.hook_callbacks = callbacks
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return self._httpd.server_address[0]

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("HookServer already started")
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="agent-phone-hookserver", daemon=True
        )
        self._thread.start()
        log.info("hook server listening on %s:%d", self.host, self.port)

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
