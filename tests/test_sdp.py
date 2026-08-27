"""Independent acceptance suite for sdp.py."""
import pytest

from agent_phone.sdp import SdpAudio, parse_sdp, find_payload, build_offer


def test_build_offer_roundtrip():
    text = build_offer(42, "10.0.0.5", 4000)
    assert "o=agentphone 42 42 IN IP4 10.0.0.5\r\n" in text
    assert "m=audio 4000 RTP/AVP 0 101\r\n" in text
    assert text.endswith("a=sendrecv\r\n")
    a = parse_sdp(text)
    assert a.address == "10.0.0.5"
    assert a.port == 4000
    assert a.payloads == (0, 101)
    assert a.rtpmap == {0: "pcmu", 101: "telephone-event"}


def test_custom_dtmf_pt():
    a = parse_sdp(build_offer(1, "1.2.3.4", 5004, dtmf_pt=127))
    assert a.payloads == (0, 127)
    assert a.rtpmap[127] == "telephone-event"


def test_media_c_overrides_session_c():
    text = ("v=0\r\no=x 1 1 IN IP4 9.9.9.9\r\ns=-\r\nc=IN IP4 9.9.9.9\r\nt=0 0\r\n"
            "m=audio 6000 RTP/AVP 0\r\nc=IN IP4 10.0.0.20\r\na=rtpmap:0 PCMU/8000\r\n")
    a = parse_sdp(text)
    assert a.address == "10.0.0.20"
    assert a.port == 6000


def test_session_c_used_when_no_media_c():
    text = "v=0\r\nc=IN IP4 8.8.4.4\r\nm=audio 7000 RTP/AVP 0\r\n"
    assert parse_sdp(text).address == "8.8.4.4"


def test_no_c_anywhere():
    assert parse_sdp("v=0\r\nm=audio 7000 RTP/AVP 0\r\n").address is None


def test_first_audio_section_after_video():
    text = ("v=0\r\nc=IN IP4 1.1.1.1\r\n"
            "m=video 9000 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
            "m=audio 8000 RTP/AVP 8 101\r\n"
            "a=rtpmap:8 PCMA/8000\r\na=rtpmap:101 telephone-event/8000\r\n"
            "m=audio 8100 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\n")
    a = parse_sdp(text)
    assert a.port == 8000
    assert a.payloads == (8, 101)
    assert a.rtpmap == {8: "pcma", 101: "telephone-event"}
    assert 96 not in a.rtpmap and 0 not in a.rtpmap


def test_session_level_rtpmap_ignored():
    text = ("v=0\r\na=rtpmap:0 PCMU/8000\r\n"
            "m=audio 5000 RTP/AVP 0\r\n")
    assert parse_sdp(text).rtpmap == {}


def test_lf_only_lines():
    text = "v=0\nc=IN IP4 2.2.2.2\nm=audio 5000 RTP/AVP 0 101\na=rtpmap:101 telephone-event/8000\n"
    a = parse_sdp(text)
    assert a.port == 5000 and a.rtpmap[101] == "telephone-event"


def test_codec_channels_suffix_stripped():
    text = "v=0\nm=audio 5000 RTP/AVP 10\na=rtpmap:10 L16/44100/2\n"
    assert parse_sdp(text).rtpmap[10] == "l16"


def test_malformed_rtpmap_ignored():
    text = ("v=0\nm=audio 5000 RTP/AVP 0 101\n"
            "a=rtpmap:abc PCMU/8000\na=rtpmap:101 nogood\n")
    assert parse_sdp(text).rtpmap == {}


def test_errors():
    with pytest.raises(ValueError):
        parse_sdp("v=0\r\nc=IN IP4 1.1.1.1\r\n")
    with pytest.raises(ValueError):
        parse_sdp("v=0\nm=audio abc RTP/AVP 0\n")
    with pytest.raises(ValueError):
        parse_sdp("v=0\nm=audio 5000 RTP/AVP 0 xx\n")


def test_find_payload():
    a = parse_sdp(build_offer(1, "1.1.1.1", 4000))
    assert find_payload(a, "PCMU") == 0
    assert find_payload(a, "telephone-event") == 101
    assert find_payload(a, "pcma") is None
    # static PCMU with no rtpmap entry
    bare = parse_sdp("v=0\nm=audio 5000 RTP/AVP 0\n")
    assert find_payload(bare, "PCMU") == 0
    # rtpmap wins over static assumption
    remapped = parse_sdp("v=0\nm=audio 5000 RTP/AVP 0\na=rtpmap:0 PCMA/8000\n")
    assert find_payload(remapped, "pcmu") is None
    assert find_payload(remapped, "pcma") == 0
