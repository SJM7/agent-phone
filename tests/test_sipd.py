"""Tests for sipd's pure message builders and state transitions."""
from agent_phone.sip_message import parse_message, serialize
from agent_phone.sipd import (Registration, CallState, response_for,
                              registration_from, build_mwi_notify,
                              build_invite, build_ack, apply_invite_response)

REGISTER_RAW = (
    b"REGISTER sip:10.0.0.5 SIP/2.0\r\n"
    b"Via: SIP/2.0/UDP 10.0.0.20:5060;branch=z9hG4bKabc\r\n"
    b"From: <sip:agentphone@10.0.0.5>;tag=ff00\r\n"
    b"To: <sip:agentphone@10.0.0.5>\r\n"
    b"Call-ID: reg1@10.0.0.20\r\n"
    b"CSeq: 2 REGISTER\r\n"
    b"Contact: <sip:agentphone@10.0.0.20:5060>;expires=300\r\n"
    b"Expires: 3600\r\n"
    b"Content-Length: 0\r\n\r\n"
)


def reg():
    r = registration_from(parse_message(REGISTER_RAW), ("10.0.0.20", 5060))
    assert r is not None
    return r


def test_registration_from_contact_param_wins():
    r = reg()
    assert r.contact == "sip:agentphone@10.0.0.20:5060"
    assert r.aor == "sip:agentphone@10.0.0.5"
    assert r.expires == 300          # contact param beats Expires header
    assert r.addr == ("10.0.0.20", 5060)
    assert r.fresh()


def test_registration_missing_contact():
    raw = REGISTER_RAW.replace(b"Contact: <sip:agentphone@10.0.0.20:5060>;expires=300\r\n", b"")
    assert registration_from(parse_message(raw), ("1.1.1.1", 1)) is None


def test_response_for_echoes_dialog_headers_and_tags_to():
    resp = response_for(parse_message(REGISTER_RAW), 200, "OK", to_tag="beef")
    wire = parse_message(serialize(resp))
    assert wire.status_code == 200
    assert wire.get("Via") == "SIP/2.0/UDP 10.0.0.20:5060;branch=z9hG4bKabc"
    assert wire.get("Call-ID") == "reg1@10.0.0.20"
    assert wire.get("CSeq") == "2 REGISTER"
    assert ";tag=beef" in (wire.get("To") or "")
    assert wire.get("From") == "<sip:agentphone@10.0.0.5>;tag=ff00"


def test_response_for_does_not_double_tag():
    raw = REGISTER_RAW.replace(b"To: <sip:agentphone@10.0.0.5>",
                               b"To: <sip:agentphone@10.0.0.5>;tag=exists")
    resp = response_for(parse_message(raw), 200, "OK", to_tag="new")
    assert (resp.get("To") or "").count(";tag=") == 1
    assert ";tag=exists" in resp.get("To")


def test_mwi_notify_shape():
    n = build_mwi_notify(reg(), "10.0.0.5", 5060, waiting=True, cseq=7,
                         call_id="mwi1@10.0.0.5")
    wire = parse_message(serialize(n))
    assert wire.method == "NOTIFY"
    assert wire.uri == "sip:agentphone@10.0.0.20:5060"
    assert wire.get("Event") == "message-summary"
    assert wire.get("Subscription-State") == "active"
    assert wire.get("Content-Type") == "application/simple-message-summary"
    assert b"Messages-Waiting: yes" in wire.body
    off = build_mwi_notify(reg(), "10.0.0.5", 5060, waiting=False, cseq=8,
                           call_id="mwi2@10.0.0.5")
    assert b"Messages-Waiting: no" in off.body


def test_invite_offer_and_answer_flow():
    invite, call = build_invite(reg(), "10.0.0.5", 5060, rtp_port=4000)
    wire = parse_message(serialize(invite))
    assert wire.method == "INVITE"
    assert wire.get("Content-Type") == "application/sdp"
    assert b"m=audio 4000 RTP/AVP 0 101" in wire.body
    assert "delay=0" in (wire.get("Alert-Info") or "")
    assert call.established is False

    answer_sdp = ("v=0\r\no=- 1 1 IN IP4 10.0.0.20\r\ns=-\r\n"
                  "c=IN IP4 10.0.0.20\r\nt=0 0\r\n"
                  "m=audio 2226 RTP/AVP 0 127\r\n"
                  "a=rtpmap:0 PCMU/8000\r\n"
                  "a=rtpmap:127 telephone-event/8000\r\n").encode()
    resp = parse_message(
        b"SIP/2.0 200 OK\r\n"
        b"Via: SIP/2.0/UDP 10.0.0.5:5060;branch=z9hG4bKx\r\n"
        b"From: <sip:agentphone@10.0.0.5>;tag=" + call.from_tag.encode() + b"\r\n"
        b"To: <sip:agentphone@10.0.0.5>;tag=phonetag\r\n"
        b"Call-ID: " + call.call_id.encode() + b"\r\n"
        b"CSeq: 1 INVITE\r\n"
        b"Contact: <sip:agentphone@10.0.0.20:5060>\r\n"
        b"Content-Type: application/sdp\r\n"
        b"Content-Length: " + str(len(answer_sdp)).encode() + b"\r\n\r\n"
        + answer_sdp)
    assert apply_invite_response(call, resp) is True
    assert call.established is True
    assert call.to_tag == "phonetag"
    assert call.remote_rtp == ("10.0.0.20", 2226)
    assert call.dtmf_pt == 127
    assert call.audio_pt == 0
    assert call.remote_contact == "sip:agentphone@10.0.0.20:5060"

    ack = build_ack(call, call.remote_contact, "10.0.0.5", 5060)
    aw = parse_message(serialize(ack))
    assert aw.method == "ACK"
    assert aw.get("Call-ID") == call.call_id
    assert aw.get("CSeq") == "1 ACK"
    assert ";tag=phonetag" in (aw.get("To") or "")


def test_apply_invite_response_rejects_non_2xx():
    call = CallState(call_id="c1", from_tag="t1")
    resp = parse_message(b"SIP/2.0 486 Busy Here\r\nCSeq: 1 INVITE\r\n\r\n")
    assert apply_invite_response(call, resp) is False
    assert call.established is False
