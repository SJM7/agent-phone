"""Tests for the CX300 transport's LED blink state machine (no device)."""
from agent_phone.cx300 import Cx300Phone


def make_phone():
    phone = Cx300Phone(on_key=lambda d: None, on_offhook=lambda: None,
                       on_onhook=lambda: None)
    writes = []
    phone._write = writes.append
    return phone, writes


RED = bytes([0x16, 0x03])
OFF = bytes([0x16, 0x07])
GREEN = bytes([0x16, 0x01])


def test_blink_toggles_red_off():
    phone, writes = make_phone()
    phone.set_led("attention")
    assert writes[-1] == RED
    early = phone._next_blink - 0.1
    phone._blink_tick(early)                    # before the interval: no-op
    assert len(writes) == 1
    phone._blink_tick(phone._next_blink + 0.01)
    assert writes[-1] == OFF                    # off phase
    phone._blink_tick(phone._next_blink + 0.01)
    assert writes[-1] == RED                    # back on


def test_working_state_is_steady_orange():
    phone, writes = make_phone()
    phone.set_led("working")
    assert writes[-1] == bytes([0x16, 0x05])    # orange, no blinking
    phone._blink_tick(phone._next_blink + 10)
    assert len(writes) == 1


def test_working_spinner_ticks_on_dashboard_headline():
    phone, writes = make_phone()
    phone.show_dashboard("done term-1", "next", "*1", "#2")
    n = len(writes)
    phone.set_led("working")
    phone._spin_tick(phone._next_spin + 0.01)
    # spinner repaints the top-left area with a frame char + headline
    painted = b"".join(writes[n:])
    assert bytes([0x14, 0x01, 0x80]) in painted          # top_left select
    assert "/ done t".encode("utf-16-le") in painted
    phone._spin_tick(phone._next_spin + 0.01)
    assert "- done t".encode("utf-16-le") in b"".join(writes)
    # leaving working state repaints the headline without the spinner
    phone.set_led("idle")
    assert "done ter".encode("utf-16-le") in b"".join(writes[-4:])


def test_blink_stops_to_steady_green():
    phone, writes = make_phone()
    phone.set_led("attention")
    phone._blink_tick(phone._next_blink + 0.01)
    phone.set_led("idle")
    assert writes[-1] == GREEN                  # all-clear is steady green
    n = len(writes)
    phone._blink_tick(phone._next_blink + 10)   # disabled: never toggles
    assert len(writes) == n
