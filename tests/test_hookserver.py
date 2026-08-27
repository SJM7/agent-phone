import json
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

import pytest

from agent_phone.hookserver import (
    HookCallbacks,
    HookServer,
    parse_hook_payload,
    parse_phone_event_xml,
)

OFFHOOK_XML = b"""<PolycomIPPhone>
  <OffHookEvent>
    <PhoneIP>192.168.1.50</PhoneIP>
    <MACAddress>0004f2abcdef</MACAddress>
    <TimeStamp>2026-08-27T18:00:00</TimeStamp>
    <LineNumber>1</LineNumber>
  </OffHookEvent>
</PolycomIPPhone>"""

STOP_HOOK_JSON = {
    "session_id": "abc123",
    "transcript_path": "/Users/x/.claude/projects/p/abc123.jsonl",
    "cwd": "/Users/x/proj",
    "hook_event_name": "Stop",
}


class TestParsePhoneEventXml:
    def test_offhook_event(self):
        event = parse_phone_event_xml(OFFHOOK_XML)
        assert event == {
            "type": "OffHookEvent",
            "PhoneIP": "192.168.1.50",
            "MACAddress": "0004f2abcdef",
            "TimeStamp": "2026-08-27T18:00:00",
            "LineNumber": "1",
        }

    def test_unknown_event_type_is_accepted(self):
        xml = b"<PolycomIPPhone><SomeFutureEvent><Foo>bar</Foo></SomeFutureEvent></PolycomIPPhone>"
        event = parse_phone_event_xml(xml)
        assert event == {"type": "SomeFutureEvent", "Foo": "bar"}

    def test_nested_field_text_is_flattened(self):
        xml = (
            b"<PolycomIPPhone><CallStateChangeEvent>"
            b"<CallState>Connected</CallState>"
            b"<CallInfo><LineId>1</LineId></CallInfo>"
            b"</CallStateChangeEvent></PolycomIPPhone>"
        )
        event = parse_phone_event_xml(xml)
        assert event["type"] == "CallStateChangeEvent"
        assert event["CallState"] == "Connected"
        assert event["CallInfo"] == "1"

    def test_empty_field(self):
        xml = b"<PolycomIPPhone><OnHookEvent><LineNumber/></OnHookEvent></PolycomIPPhone>"
        assert parse_phone_event_xml(xml) == {"type": "OnHookEvent", "LineNumber": ""}

    def test_root_without_event_element(self):
        with pytest.raises(ValueError):
            parse_phone_event_xml(b"<PolycomIPPhone></PolycomIPPhone>")

    def test_malformed_xml(self):
        with pytest.raises(ET.ParseError):
            parse_phone_event_xml(b"<PolycomIPPhone><Off")


class TestParseHookPayload:
    def test_valid_payload(self):
        body = json.dumps(STOP_HOOK_JSON).encode()
        assert parse_hook_payload(body) == STOP_HOOK_JSON

    def test_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            parse_hook_payload(b"not json")

    def test_non_object_json(self):
        with pytest.raises(ValueError):
            parse_hook_payload(b"[1, 2, 3]")


class RecordingCallbacks(HookCallbacks):
    def __init__(self):
        self.turn_done = threading.Event()
        self.turn_start = threading.Event()
        self.phone_event = threading.Event()
        self.payloads = {}
        super().__init__(
            on_turn_done=self._record("turn_done", self.turn_done),
            on_turn_start=self._record("turn_start", self.turn_start),
            on_phone_event=self._record("phone_event", self.phone_event),
        )

    def _record(self, name, event):
        def cb(payload):
            self.payloads[name] = payload
            event.set()

        return cb


def post(url, body, content_type):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


@pytest.fixture
def server():
    callbacks = RecordingCallbacks()
    srv = HookServer(callbacks, port=0)
    srv.start()
    yield srv, callbacks
    srv.stop()


class TestHookServerEndToEnd:
    def test_full_round_trip(self, server):
        srv, callbacks = server
        base = f"http://127.0.0.1:{srv.port}"

        with urllib.request.urlopen(f"{base}/health", timeout=5) as resp:
            assert resp.status == 200
            assert json.load(resp) == {"ok": True}

        status = post(f"{base}/hook/stop", json.dumps(STOP_HOOK_JSON).encode(), "application/json")
        assert status == 204
        assert callbacks.turn_done.wait(5)
        assert callbacks.payloads["turn_done"]["session_id"] == "abc123"
        assert callbacks.payloads["turn_done"]["hook_event_name"] == "Stop"

        submit = dict(STOP_HOOK_JSON, hook_event_name="UserPromptSubmit", prompt="hi")
        status = post(
            f"{base}/hook/user-prompt-submit", json.dumps(submit).encode(), "application/json"
        )
        assert status == 204
        assert callbacks.turn_start.wait(5)
        assert callbacks.payloads["turn_start"]["prompt"] == "hi"

        status = post(f"{base}/phone/event", OFFHOOK_XML, "application/xml")
        assert status == 204
        assert callbacks.phone_event.wait(5)
        assert callbacks.payloads["phone_event"]["type"] == "OffHookEvent"
        assert callbacks.payloads["phone_event"]["LineNumber"] == "1"

    def test_error_statuses(self, server):
        srv, _ = server
        base = f"http://127.0.0.1:{srv.port}"

        with pytest.raises(urllib.error.HTTPError) as exc:
            post(f"{base}/phone/event", b"<broken", "application/xml")
        assert exc.value.code == 400

        with pytest.raises(urllib.error.HTTPError) as exc:
            post(f"{base}/hook/stop", b"not json", "application/json")
        assert exc.value.code == 400

        with pytest.raises(urllib.error.HTTPError) as exc:
            post(f"{base}/nope", b"{}", "application/json")
        assert exc.value.code == 404

        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{base}/nope", timeout=5)
        assert exc.value.code == 404

    def test_callback_exception_does_not_reach_client(self):
        fired = threading.Event()

        def boom(session):
            fired.set()
            raise RuntimeError("callback blew up")

        srv = HookServer(HookCallbacks(on_turn_done=boom), port=0)
        srv.start()
        try:
            base = f"http://127.0.0.1:{srv.port}"
            status = post(f"{base}/hook/stop", b"{}", "application/json")
            assert status == 204
            assert fired.wait(5)
            with urllib.request.urlopen(f"{base}/health", timeout=5) as resp:
                assert resp.status == 200
        finally:
            srv.stop()
