"""Independent acceptance suite for rtp.py (written by the reviewer,
never shown to the producer)."""
import struct

import pytest

from agent_phone.rtp import (RtpPacket, TelephoneEvent, DTMF_EVENTS, parse_rtp,
                 parse_telephone_event, DtmfDetector)


def mk(version=2, padding=0, ext=0, cc=0, marker=0, pt=101, seq=1, ts=1000,
       ssrc=0xDEADBEEF, csrcs=(), ext_profile=0, ext_words=b"", payload=b""):
    b0 = (version << 6) | (padding << 5) | (ext << 4) | cc
    b1 = (marker << 7) | pt
    head = struct.pack("!BBHII", b0, b1, seq, ts, ssrc)
    for c in csrcs:
        head += struct.pack("!I", c)
    if ext:
        assert len(ext_words) % 4 == 0
        head += struct.pack("!HH", ext_profile, len(ext_words) // 4) + ext_words
    return head + payload


def dtmf_payload(event, end=False, volume=10, duration=160):
    b1 = (0x80 if end else 0) | (volume & 0x3F)
    return struct.pack("!BBH", event, b1, duration)


def test_minimal_packet():
    p = parse_rtp(mk(payload=b"\x01\x02"))
    assert p.version == 2 and p.padding is False and p.extension is False
    assert p.marker is False and p.payload_type == 101
    assert p.sequence == 1 and p.timestamp == 1000 and p.ssrc == 0xDEADBEEF
    assert p.csrcs == () and p.payload == b"\x01\x02"


def test_csrcs():
    p = parse_rtp(mk(cc=2, csrcs=(7, 8), payload=b"xy"))
    assert p.csrcs == (7, 8)
    assert p.payload == b"xy"


def test_extension_header_skipped():
    p = parse_rtp(mk(ext=1, ext_profile=0xBEDE, ext_words=b"\x00" * 8, payload=b"pl"))
    assert p.extension is True
    assert p.payload == b"pl"


def test_padding_stripped():
    # payload "abc" + 3 pad bytes, last one is the count (3)
    raw = mk(padding=1, payload=b"abc" + b"\x00\x00\x03")
    p = parse_rtp(raw)
    assert p.padding is True
    assert p.payload == b"abc"


def test_marker_and_pt_bits():
    p = parse_rtp(mk(marker=1, pt=0))
    assert p.marker is True and p.payload_type == 0


def test_errors():
    with pytest.raises(ValueError):
        parse_rtp(b"\x80\x65\x00\x01")                       # short
    with pytest.raises(ValueError):
        parse_rtp(mk(version=1))                             # bad version
    with pytest.raises(ValueError):
        parse_rtp(mk(cc=3, csrcs=(1,)))                      # short for CSRCs
    with pytest.raises(ValueError):
        parse_rtp(mk(ext=1, ext_words=b"")[:14])             # short for ext header
    with pytest.raises(ValueError):
        parse_rtp(mk(padding=1, payload=b"a\x00\x09"))       # pad count > payload
    with pytest.raises(ValueError):
        parse_rtp(mk(padding=1, payload=b"abc\x00"))         # pad count 0


def test_telephone_event_parse():
    ev = parse_telephone_event(dtmf_payload(11, end=True, volume=63, duration=800))
    assert ev.event == 11 and ev.end is True and ev.volume == 63 and ev.duration == 800
    ev2 = parse_telephone_event(dtmf_payload(10, end=False, volume=5) + b"surplus")
    assert ev2.event == 10 and ev2.end is False and ev2.volume == 5
    with pytest.raises(ValueError):
        parse_telephone_event(b"\x0b\x80")


def test_dtmf_events_table():
    assert DTMF_EVENTS[10] == "*" and DTMF_EVENTS[11] == "#" and DTMF_EVENTS[0] == "0"
    assert DTMF_EVENTS[15] == "D" and len(DTMF_EVENTS) == 16


def press(det, event, ts, n_repeats=2, seq0=100, ssrc=1):
    """Simulate one held digit: initial + repeats + end packet, same timestamp."""
    out = []
    for i in range(n_repeats + 2):
        end = i == n_repeats + 1
        pkt = mk(pt=101, seq=seq0 + i, ts=ts, ssrc=ssrc,
                 marker=1 if i == 0 else 0,
                 payload=dtmf_payload(event, end=end, duration=160 * (i + 1)))
        out.append(det.feed(pkt))
    return out


def test_detector_reports_digit_once():
    det = DtmfDetector(101)
    results = press(det, 11, ts=5000)
    assert results[0] == "#"
    assert all(r is None for r in results[1:])


def test_detector_consecutive_distinct_digits():
    det = DtmfDetector(101)
    assert press(det, 10, ts=1000)[0] == "*"
    assert press(det, 5, ts=2000, seq0=200)[0] == "5"


def test_detector_same_digit_new_press():
    det = DtmfDetector(101)
    assert press(det, 11, ts=1000)[0] == "#"
    assert press(det, 11, ts=9000, seq0=300)[0] == "#"


def test_detector_ignores_other_payload_types():
    det = DtmfDetector(101)
    audio = mk(pt=0, ts=1234, payload=b"\xff" * 160)
    assert det.feed(audio) is None
    # and a following real digit still fires
    assert press(det, 3, ts=1234)[0] == "3"


def test_detector_malformed_ignored_state_intact():
    det = DtmfDetector(101)
    assert press(det, 7, ts=100)[0] == "7"
    assert det.feed(b"garbage") is None
    # repeat of same key after garbage is still recognized as same digit
    pkt = mk(pt=101, seq=999, ts=100, ssrc=1, payload=dtmf_payload(7, end=True))
    assert det.feed(pkt) is None


def test_detector_unknown_event_silent_but_recorded():
    det = DtmfDetector(101)
    flash = mk(pt=101, seq=1, ts=777, payload=dtmf_payload(16))
    assert det.feed(flash) is None
    repeat = mk(pt=101, seq=2, ts=777, payload=dtmf_payload(16, end=True))
    assert det.feed(repeat) is None
    # different timestamp -> real digit fires
    assert press(det, 1, ts=888)[0] == "1"


def test_detector_instances_independent():
    a, b = DtmfDetector(101), DtmfDetector(101)
    assert press(a, 11, ts=42)[0] == "#"
    assert press(b, 11, ts=42)[0] == "#"
