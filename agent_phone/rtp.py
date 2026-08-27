from __future__ import annotations
import struct
from dataclasses import dataclass
from typing import Tuple

DTMF_EVENTS = {0: "0", 1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
               10: "*", 11: "#", 12: "A", 13: "B", 14: "C", 15: "D"}

@dataclass(frozen=True)
class RtpPacket:
    version: int
    padding: bool
    extension: bool
    marker: bool
    payload_type: int
    sequence: int
    timestamp: int
    ssrc: int
    csrcs: tuple[int, ...]
    payload: bytes

@dataclass(frozen=True)
class TelephoneEvent:
    event: int
    end: bool
    volume: int
    duration: int

def _read_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from('!H', data, offset)[0]

def _read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('!I', data, offset)[0]

def parse_rtp(data: bytes) -> RtpPacket:
    if len(data) < 12:
        raise ValueError("Data too short")
    
    byte0, byte1 = data[0], data[1]
    version = (byte0 >> 6) & 0x3
    if version != 2:
        raise ValueError("Invalid version")
    
    padding = bool(byte0 & 0x20)
    extension = bool(byte0 & 0x10)
    cc_count = byte0 & 0x0F
    marker = bool(byte1 & 0x80)
    payload_type = byte1 & 0x7F
    
    sequence = _read_u16(data, 2)
    timestamp = _read_u32(data, 4)
    ssrc = _read_u32(data, 8)
    
    offset = 12
    csrcs = []
    for _ in range(cc_count):
        if offset + 4 > len(data):
            raise ValueError("Data too short for CSRC")
        csrcs.append(_read_u32(data, offset))
        offset += 4
    
    if extension:
        if offset + 4 > len(data):
            raise ValueError("Data too short for extension header")
        ext_profile_id = _read_u16(data, offset)
        ext_length = _read_u16(data, offset + 2) # Length in 32-bit words
        offset += 4
        ext_data_len = ext_length * 4
        if offset + ext_data_len > len(data):
            raise ValueError("Data too short for extension data")
        offset += ext_data_len
    
    payload = data[offset:]
    
    if padding:
        if len(payload) < 1:
            raise ValueError("Padding present but payload empty")
        pad_count = payload[-1]
        if pad_count == 0:
            raise ValueError("Padding count is 0")
        if pad_count > len(payload):
            raise ValueError("Padding count exceeds payload length")
        payload = payload[:-pad_count]
        
    return RtpPacket(
        version=version,
        padding=padding,
        extension=extension,
        marker=marker,
        payload_type=payload_type,
        sequence=sequence,
        timestamp=timestamp,
        ssrc=ssrc,
        csrcs=tuple(csrcs),
        payload=payload
    )

def parse_telephone_event(payload: bytes) -> TelephoneEvent:
    if len(payload) < 4:
        raise ValueError("Payload too short")
    
    event_code = payload[0]
    byte1 = payload[1]
    end = bool(byte1 & 0x80)
    volume = byte1 & 0x3F
    duration = _read_u16(payload, 2)
    
    return TelephoneEvent(
        event=event_code,
        end=end,
        volume=volume,
        duration=duration
    )

class DtmfDetector:
    def __init__(self, payload_type: int) -> None:
        self._payload_type = payload_type
        self._last_seen_key: Tuple[int, int] | None = None

    def feed(self, data: bytes) -> str | None:
        try:
            packet = parse_rtp(data)
        except ValueError:
            return None
        
        if packet.payload_type != self._payload_type:
            return None
        
        try:
            event = parse_telephone_event(packet.payload)
        except ValueError:
            return None
        
        key = (packet.ssrc, packet.timestamp)
        
        if key == self._last_seen_key:
            return None
        
        self._last_seen_key = key
        
        if event.event in DTMF_EVENTS:
            return DTMF_EVENTS[event.event]
        
        return None
