"""Independent acceptance suite for g711.py."""
import io
import struct
import wave

import pytest

from agent_phone.g711 import ulaw_to_pcm16, wav_bytes


def sample(byte):
    return struct.unpack("<h", ulaw_to_pcm16(bytes([byte])))[0]


def test_known_values():
    assert sample(0xFF) == 0
    assert sample(0x7F) == 0
    assert sample(0x00) == -32124
    assert sample(0x80) == 32124


def test_sign_symmetry():
    for b in range(256):
        pos, neg = sample(b | 0x80), sample(b & 0x7F)
        assert pos == -neg, f"byte {b:#x}: {pos} vs {neg}"


def test_reference_algorithm_all_bytes():
    for b in range(256):
        u = (~b) & 0xFF
        t = (((u & 0x0F) << 3) + 0x84) << ((u & 0x70) >> 4)
        expected = (0x84 - t) if (u & 0x80) else (t - 0x84)
        assert sample(b) == expected, f"byte {b:#x}"


def test_lengths_and_empty():
    assert ulaw_to_pcm16(b"") == b""
    assert len(ulaw_to_pcm16(bytes(160))) == 320


def test_wav_readable_by_wave_module():
    pcm = ulaw_to_pcm16(bytes(range(256)))
    blob = wav_bytes(pcm, sample_rate=8000)
    with wave.open(io.BytesIO(blob)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 8000
        assert w.getnframes() == len(pcm) // 2
        assert w.readframes(w.getnframes()) == pcm


def test_wav_header_exact():
    blob = wav_bytes(b"\x01\x02\x03\x04", sample_rate=16000)
    assert blob[:4] == b"RIFF"
    assert struct.unpack("<I", blob[4:8])[0] == 36 + 4
    assert blob[8:16] == b"WAVEfmt "
    fmt = struct.unpack("<IHHIIHH", blob[16:36])
    assert fmt == (16, 1, 1, 16000, 32000, 2, 16)
    assert blob[36:40] == b"data"
    assert struct.unpack("<I", blob[40:44])[0] == 4
    assert blob[44:] == b"\x01\x02\x03\x04"


def test_wav_errors():
    with pytest.raises(ValueError):
        wav_bytes(b"\x01\x02\x03")
    with pytest.raises(ValueError):
        wav_bytes(b"\x01\x02", sample_rate=0)
