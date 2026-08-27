"""Independent acceptance suite for cx300_protocol.py."""
import pytest

from agent_phone.cx300_protocol import (VID, PID, InputState, parse_input_report,
                            EventDetector, build_led, build_display_mode,
                            build_area_select, build_text, build_keepalive)


def report(key=0x00, flags=0x00, audio=0x00, trans=0x40, vol=(0x70, 0x0B),
           mic=0x00, with_id=True):
    # Live-verified layout: [ID 0x01,] flags, key, audio, transducer, vol, vol, mic
    data = bytes([flags, key, audio, trans, vol[0], vol[1], mic])
    return (b"\x01" + data) if with_id else data


def test_ids():
    assert VID == 0x095D and PID == 0x9201


def test_parse_with_and_without_report_id():
    a = parse_input_report(report(key=0x0C, with_id=True))
    b = parse_input_report(report(key=0x0C, with_id=False))
    assert a == b
    assert a is not None and a.key == "#"


def test_all_keypad_codes():
    for code, expected in [(0x00, None), (0x01, "0"), (0x02, "1"), (0x05, "4"),
                           (0x0A, "9"), (0x0B, "*"), (0x0C, "#"), (0x0D, None),
                           (0xFF, None)]:
        st = parse_input_report(report(key=code))
        assert st is not None and st.key == expected, hex(code)


def test_flag_bits_independent():
    st = parse_input_report(report(flags=0x01))
    assert st.offhook and not (st.hold or st.redial or st.long_press
                               or st.mute_key or st.delete)
    assert parse_input_report(report(flags=0x02)).hold
    assert parse_input_report(report(flags=0x04)).redial
    assert parse_input_report(report(flags=0x08)).long_press
    assert parse_input_report(report(flags=0x10)).mute_key
    assert parse_input_report(report(flags=0x20)).delete
    combo = parse_input_report(report(flags=0x31))
    assert combo.offhook and combo.mute_key and combo.delete and not combo.hold


def test_audio_and_transducer_and_volume_and_mic():
    st = parse_input_report(report(audio=0x00, trans=0x40, vol=(0xAB, 0xCD), mic=0x01))
    assert st.audio_enabled is True
    assert st.transducer == "handset"
    assert st.volume == (0xAB, 0xCD)
    assert st.mic_muted is True
    assert parse_input_report(report(audio=0x03)).audio_enabled is False
    assert parse_input_report(report(trans=0x50)).transducer == "speaker"
    assert parse_input_report(report(trans=0x52)).transducer == "speaker"
    assert parse_input_report(report(trans=0x60)).transducer == "headset"
    assert parse_input_report(report(trans=0x00)).transducer is None
    assert parse_input_report(report(mic=0x00)).mic_muted is False


def test_parse_bad_input_returns_none():
    assert parse_input_report(b"") is None
    assert parse_input_report(b"\x01\x00\x00") is None
    assert parse_input_report(bytes(10)) is None
    assert parse_input_report(b"\x02" + bytes(7)) is None   # wrong report id
    assert parse_input_report(bytes(6)) is None
    # live capture from the real phone: '#' pressed while on-hook
    live = bytes.fromhex("01000c00003cb500")
    st = parse_input_report(live)
    assert st is not None and st.key == "#" and st.offhook is False
    # live capture: receiver lifted (handset transducer active)
    live2 = bytes.fromhex("0101000040d55a00")
    st2 = parse_input_report(live2)
    assert st2 is not None and st2.key is None and st2.offhook is True
    assert st2.transducer == "handset"


def test_event_detector_key_edges():
    det = EventDetector()
    assert det.feed(parse_input_report(report(key=0x0C))) == [("key", "#")]
    assert det.feed(parse_input_report(report(key=0x0C))) == []      # held
    assert det.feed(parse_input_report(report(key=0x00))) == []      # release
    assert det.feed(parse_input_report(report(key=0x0B))) == [("key", "*")]
    assert det.feed(parse_input_report(report(key=0x02))) == [("key", "1")]


def test_event_detector_hook_edges():
    det = EventDetector()
    assert det.feed(parse_input_report(report(flags=0x01))) == [("offhook", None)]
    assert det.feed(parse_input_report(report(flags=0x01))) == []
    assert det.feed(parse_input_report(report(flags=0x00))) == [("onhook", None)]
    assert det.feed(parse_input_report(report(flags=0x00))) == []


def test_event_detector_key_and_hook_same_report_order():
    det = EventDetector()
    events = det.feed(parse_input_report(report(key=0x0B, flags=0x01)))
    assert events == [("key", "*"), ("offhook", None)]


def test_event_detector_instances_independent():
    a, b = EventDetector(), EventDetector()
    a.feed(parse_input_report(report(key=0x0C)))
    assert b.feed(parse_input_report(report(key=0x0C))) == [("key", "#")]


def test_build_led():
    assert build_led("green") == bytes([0x16, 0x01])
    assert build_led("red") == bytes([0x16, 0x03])
    assert build_led("orange_red") == bytes([0x16, 0x04])
    assert build_led("orange") == bytes([0x16, 0x05])
    assert build_led("dnd") == bytes([0x16, 0x06])
    assert build_led("off") == bytes([0x16, 0x07])
    assert build_led("green_orange") == bytes([0x16, 0x08])
    with pytest.raises(ValueError):
        build_led("magenta")


def test_build_display_mode_and_area():
    assert build_display_mode("clear") == bytes([0x13, 0x00])
    assert build_display_mode("corners") == bytes([0x13, 0x0D])
    assert build_display_mode("two_line") == bytes([0x13, 0x15])
    with pytest.raises(ValueError):
        build_display_mode("weird")
    assert build_area_select("top_left") == bytes([0x14, 0x01, 0x80])
    assert build_area_select("bottom_line") == bytes([0x14, 0x0A, 0x80])
    with pytest.raises(ValueError):
        build_area_select("middle")


def test_build_text_chunks():
    reports = build_text("AGENT PHONE")          # 11 chars -> 8 + 3
    assert len(reports) == 2
    assert reports[0] == bytes([0x15, 0x00]) + "AGENT PH".encode("utf-16-le")
    assert reports[1] == bytes([0x15, 0x80]) + "ONE".encode("utf-16-le")
    exact = build_text("12345678")
    assert exact == [bytes([0x15, 0x80]) + "12345678".encode("utf-16-le")]
    assert build_text("") == [bytes([0x15, 0x80])]
    uni = build_text("über")
    assert uni == [bytes([0x15, 0x80]) + "über".encode("utf-16-le")]


def test_build_keepalive():
    assert build_keepalive() == bytes([0x17, 0x09, 0x04, 0x01, 0x02])
    assert build_keepalive(lcid=0x07) == bytes([0x17, 0x07, 0x04, 0x01, 0x02])
