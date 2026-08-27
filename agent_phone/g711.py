from __future__ import annotations
import struct

_ULAW_TABLE = [struct.pack("<h", sample) for i in range(256) for sample in [
    (0x84 - t) if (u & 0x80) else (t - 0x84)
    for u in [(~i) & 0xFF]
    for t in [((u & 0x0F) << 3) + 0x84]
    for t in [t << ((u & 0x70) >> 4)]
]]

def ulaw_to_pcm16(data: bytes) -> bytes:
    return b"".join(_ULAW_TABLE[b] for b in data)

def wav_bytes(pcm: bytes, sample_rate: int = 8000) -> bytes:
    if len(pcm) % 2 != 0 or sample_rate <= 0:
        raise ValueError
    pcm_len = len(pcm)
    riff_len = 36 + pcm_len
    return b"".join([
        b"RIFF",
        struct.pack("<I", riff_len),
        b"WAVE",
        b"fmt ",
        struct.pack("<I", 16),
        struct.pack("<H", 1),
        struct.pack("<H", 1),
        struct.pack("<I", sample_rate),
        struct.pack("<I", sample_rate * 2),
        struct.pack("<H", 2),
        struct.pack("<H", 16),
        b"data",
        struct.pack("<I", pcm_len),
        pcm
    ])
