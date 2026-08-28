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
    phone.set_led(True)
    assert writes[-1] == RED
    early = phone._next_blink - 0.1
    phone._blink_tick(early)                    # before the interval: no-op
    assert len(writes) == 1
    phone._blink_tick(phone._next_blink + 0.01)
    assert writes[-1] == OFF                    # off phase
    phone._blink_tick(phone._next_blink + 0.01)
    assert writes[-1] == RED                    # back on


def test_blink_stops_to_steady_green():
    phone, writes = make_phone()
    phone.set_led(True)
    phone._blink_tick(phone._next_blink + 0.01)
    phone.set_led(False)
    assert writes[-1] == GREEN                  # all-clear is steady green
    n = len(writes)
    phone._blink_tick(phone._next_blink + 10)   # disabled: never toggles
    assert len(writes) == n
