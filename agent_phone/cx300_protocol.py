from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Optional


VID = 0x095D
PID = 0x9201


KEY_MAP = {0x00: None, 0x01: "0", 0x02: "1", 0x03: "2", 0x04: "3", 0x05: "4",
           0x06: "5", 0x07: "6", 0x08: "7", 0x09: "8", 0x0A: "9", 0x0B: "*", 0x0C: "#"}


@dataclass(frozen=True)
class InputState:
    key: Optional[str]
    offhook: bool
    hold: bool
    redial: bool
    long_press: bool
    mute_key: bool
    delete: bool
    audio_enabled: bool
    transducer: Optional[str]
    volume: Tuple[int, int]
    mic_muted: bool


def parse_input_report(data: bytes) -> InputState | None:
    # Live-verified layout (CX300, 2026-08): 8 bytes INCLUDING report ID 0x01,
    # then [flags, key, audio, transducer, vol, vol, mic]. The community doc
    # had key/flags swapped due to ambiguous byte indexing.
    if len(data) == 8 and data[0] == 0x01:
        data = data[1:]
    if len(data) != 7:
        return None
    flags = data[0]
    raw_key = data[1]
    key = KEY_MAP.get(raw_key) if raw_key in KEY_MAP else None
    audio = data[2]
    transducer_byte = data[3]
    vol = data[4:6]
    mic_muted = data[6]

    if audio != 0x00:
        audio_enabled = False
    else:
        audio_enabled = True

    transducer_map = {0x40: "handset", 0x50: "speaker", 0x52: "speaker", 0x60: "headset"}
    transducer = transducer_map.get(transducer_byte)

    return InputState(
        key=key, offhook=bool(flags & 0x01), hold=bool(flags & 0x02),
        redial=bool(flags & 0x04), long_press=bool(flags & 0x08),
        mute_key=bool(flags & 0x10), delete=bool(flags & 0x20),
        audio_enabled=audio_enabled, transducer=transducer, volume=(vol[0], vol[1]),
        mic_muted=bool(mic_muted)
    )


_BUTTON_FLAGS = ("redial", "hold", "delete")


class EventDetector:
    def __init__(self) -> None:
        self._prev_key: Optional[str] = None
        self._prev_offhook: bool = False
        self._prev_buttons = {name: False for name in _BUTTON_FLAGS}

    def feed(self, state: InputState) -> List[Tuple[str, Optional[str]]]:
        events = []
        current_key = state.key
        prev_key = self._prev_key

        if current_key is not None:
            if current_key != prev_key:
                events.append(("key", current_key))

        for name in _BUTTON_FLAGS:
            pressed = getattr(state, name)
            if pressed and not self._prev_buttons[name]:
                events.append((name, None))
            self._prev_buttons[name] = pressed

        if state.offhook and not self._prev_offhook:
            events.append(("offhook", None))
        elif not state.offhook and self._prev_offhook:
            events.append(("onhook", None))

        self._prev_key = current_key
        self._prev_offhook = state.offhook
        return events


_COLOR_MAP = {"green": 0x01, "red": 0x03, "orange_red": 0x04, "orange": 0x05,
              "dnd": 0x06, "off": 0x07, "green_orange": 0x08}


def build_led(color: str) -> bytes:
    if color not in _COLOR_MAP:
        raise ValueError(f"Unknown color: {color}")
    return bytes([0x16, _COLOR_MAP[color]])


_MODE_MAP = {"clear": 0x00, "corners": 0x0D, "two_line": 0x15}


def build_display_mode(mode: str) -> bytes:
    if mode not in _MODE_MAP:
        raise ValueError(f"Unknown mode: {mode}")
    return bytes([0x13, _MODE_MAP[mode]])


_AREA_MAP = {"top_left": 0x01, "bottom_left": 0x02, "top_right": 0x03,
             "bottom_right": 0x04, "top_line": 0x05, "bottom_line": 0x0A}


def build_area_select(area: str) -> bytes:
    if area not in _AREA_MAP:
        raise ValueError(f"Unknown area: {area}")
    return bytes([0x14, _AREA_MAP[area], 0x80])


def build_text(text: str) -> List[bytes]:
    if not text:
        return [bytes([0x15, 0x80])]
    results = []
    chunks = [text[i:i+8] for i in range(0, len(text), 8)]
    for i, chunk in enumerate(chunks):
        is_final = (i == len(chunks) - 1)
        flag = 0x80 if is_final else 0x00
        encoded = chunk.encode('utf-16-le')
        results.append(bytes([0x15, flag]) + encoded)
    return results


def build_keepalive(lcid: int = 0x09) -> bytes:
    return bytes([0x17, lcid, 0x04, 0x01, 0x02])


_DISPLAY_REPLACEMENTS = {"—": "-", "–": "-", "’": "'", "‘": "'", "“": '"',
                         "”": '"', "×": "x", "…": "..."}


def sanitize_display_text(text: str, width: int = 24) -> str:
    """Make text safe for the CX300's tiny LCD font: fancy punctuation to
    ASCII, everything non-printable-ASCII dropped, whitespace collapsed,
    then truncated and space-padded to `width` so a new write fully
    overpaints whatever was on the line before."""
    chars = []
    for ch in text:
        if ch.isspace():
            ch = " "
        for c in _DISPLAY_REPLACEMENTS.get(ch, ch):
            if 0x20 <= ord(c) < 0x7F:
                chars.append(c)
    collapsed = " ".join("".join(chars).split())
    return collapsed[:width].ljust(width)
