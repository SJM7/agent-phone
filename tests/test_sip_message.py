"""Independent acceptance suite for sip_message.py (written by the reviewer,
never shown to the producer)."""
import pytest

from agent_phone.sip_message import SipMessage, parse_message, serialize, parse_params, parse_auth


REGISTER = (
    b"REGISTER sip:10.0.0.5 SIP/2.0\r\n"
    b"Via: SIP/2.0/UDP 10.0.0.20:5060;branch=z9hG4bK776asdhds\r\n"
    b"Max-Forwards: 70\r\n"
    b"To: <sip:agentphone@10.0.0.5>\r\n"
    b"f: \"VVX 300\" <sip:agentphone@10.0.0.5>;tag=1928301774\r\n"
    b"i: a84b4c76e66710@10.0.0.20\r\n"
    b"CSeq: 314159 REGISTER\r\n"
    b"Contact: <sip:agentphone@10.0.0.20:5060>\r\n"
    b"Content-Length: 0\r\n"
    b"\r\n"
)


def test_parse_request_basics():
    m = parse_message(REGISTER)
    assert m.is_request is True
    assert m.method == "REGISTER"
    assert m.uri == "sip:10.0.0.5"
    assert m.version == "SIP/2.0"
    assert m.status_code is None and m.reason is None
    assert m.body == b""


def test_compact_forms_expanded_and_case_insensitive_get():
    m = parse_message(REGISTER)
    # stored under canonical names
    names = [n for n, _ in m.headers]
    assert "From" in names and "Call-ID" in names
    assert "f" not in names and "i" not in names
    # get() matches canonical, lowercase, and compact spellings
    assert m.get("from") == '"VVX 300" <sip:agentphone@10.0.0.5>;tag=1928301774'
    assert m.get("F") == m.get("From")
    assert m.get("call-id") == "a84b4c76e66710@10.0.0.20"
    assert m.get("I") == m.get("Call-ID")
    assert m.get("absent-header") is None


def test_parse_response_reason_with_spaces_and_empty():
    r = parse_message(b"SIP/2.0 401 Unauthorized Here\r\nCSeq: 1 REGISTER\r\n\r\n")
    assert r.is_request is False
    assert r.status_code == 401
    assert r.reason == "Unauthorized Here"
    assert r.method is None and r.uri is None
    r2 = parse_message(b"SIP/2.0 200 \r\n\r\n")
    assert r2.status_code == 200
    assert r2.reason == ""


def test_continuation_lines():
    raw = (b"INVITE sip:a@b SIP/2.0\r\n"
           b"Subject: first part\r\n"
           b"  second part\r\n"
           b"\tthird\r\n"
           b"\r\n")
    m = parse_message(raw)
    assert m.get("Subject") == "first part second part third"


def test_bare_lf_tolerated():
    m = parse_message(b"OPTIONS sip:a@b SIP/2.0\nVia: SIP/2.0/UDP h\n\n")
    assert m.method == "OPTIONS"
    assert m.get("via") == "SIP/2.0/UDP h"


def test_content_length_governs_body():
    raw = (b"MESSAGE sip:a@b SIP/2.0\r\n"
           b"Content-Length: 5\r\n"
           b"\r\n"
           b"hellosurplus")
    m = parse_message(raw)
    assert m.body == b"hello"


def test_body_without_content_length_takes_rest():
    raw = b"MESSAGE sip:a@b SIP/2.0\r\nX-A: 1\r\n\r\nrest of it"
    assert parse_message(raw).body == b"rest of it"


def test_content_length_underrun_raises():
    raw = b"MESSAGE sip:a@b SIP/2.0\r\nContent-Length: 50\r\n\r\nshort"
    with pytest.raises(ValueError):
        parse_message(raw)


def test_malformed_raises():
    with pytest.raises(ValueError):
        parse_message(b"REGISTER sip:a@b SIP/2.0\r\nNo-Blank-Line: yes\r\n")
    with pytest.raises(ValueError):
        parse_message(b"SIP/2.0 abc Bad\r\n\r\n")
    with pytest.raises(ValueError):
        parse_message(b"INVITE sip:a@b SIP/2.0\r\nBrokenHeaderNoColon\r\n\r\n")
    with pytest.raises(ValueError):
        parse_message(b"MESSAGE sip:a@b SIP/2.0\r\nContent-Length: nope\r\n\r\nx")


def test_get_all_and_set_and_add():
    raw = (b"INVITE sip:a@b SIP/2.0\r\n"
           b"Via: SIP/2.0/UDP one\r\n"
           b"Via: SIP/2.0/UDP two\r\n"
           b"\r\n")
    m = parse_message(raw)
    assert m.get_all("via") == ["SIP/2.0/UDP one", "SIP/2.0/UDP two"]
    m.set("Via", "replaced")
    assert m.get_all("via") == ["replaced", "SIP/2.0/UDP two"]
    assert m.headers[0] == ("Via", "replaced")  # position and name kept
    m.set("X-New", "v")
    assert m.headers[-1] == ("X-New", "v")
    m.add("Via", "SIP/2.0/UDP three")
    assert m.get_all("v")[-1] == "SIP/2.0/UDP three"


def test_serialize_roundtrip_and_auto_content_length():
    m = SipMessage(True, "NOTIFY", "sip:phone@10.0.0.20", None, None, "SIP/2.0",
                   [("Via", "SIP/2.0/UDP 10.0.0.5"), ("Event", "message-summary")],
                   b"Messages-Waiting: yes\r\n")
    wire = serialize(m)
    assert wire.startswith(b"NOTIFY sip:phone@10.0.0.20 SIP/2.0\r\n")
    back = parse_message(wire)
    assert back.get("Content-Length") == str(len(m.body))
    assert back.body == m.body
    assert back.get("Event") == "message-summary"
    # round-trip of a parsed message
    m2 = parse_message(REGISTER)
    again = parse_message(serialize(m2))
    assert again.headers == m2.headers
    assert (again.method, again.uri) == (m2.method, m2.uri)


def test_serialize_updates_existing_content_length_in_place():
    raw = (b"MESSAGE sip:a@b SIP/2.0\r\n"
           b"Content-Length: 0\r\n"
           b"X-After: 1\r\n"
           b"\r\n")
    m = parse_message(raw)
    m.body = b"12345"
    wire = serialize(m)
    header_block = wire.split(b"\r\n\r\n")[0]
    lines = header_block.split(b"\r\n")
    assert lines[1] == b"Content-Length: 5"  # kept its position
    assert lines[2] == b"X-After: 1"


def test_parse_params_plain_and_quoted_and_brackets():
    base, params = parse_params("<sip:a@b;transport=udp>;tag=abc;lr")
    assert base == "<sip:a@b;transport=udp>"       # ; inside <> not split
    assert params == {"tag": "abc", "lr": None}
    base2, params2 = parse_params('"semi;colon" <sip:x@y>;note="a;b\\"c"')
    assert base2 == '"semi;colon" <sip:x@y>'
    assert params2["note"] == 'a;b"c'


def test_parse_auth():
    scheme, p = parse_auth(
        'Digest realm="atlanta.com", nonce="84f1,x", algorithm=MD5, qop="auth"')
    assert scheme == "Digest"
    assert p["realm"] == "atlanta.com"
    assert p["nonce"] == "84f1,x"                  # comma inside quotes preserved
    assert p["algorithm"] == "MD5"
    assert p["qop"] == "auth"
    with pytest.raises(ValueError):
        parse_auth("Digest realm=ok, bareword")
