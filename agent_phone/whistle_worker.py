"""Warm Whistle process. Reads length-prefixed s16le, writes JSON results.

The daemon keeps this process up for the whole session. One model load,
then each request is a single clip under 30 seconds.
"""
from __future__ import annotations

import array
import json
import struct
import sys

_HEADER = struct.Struct(">I")
_MAX_SAMPLES = int(29 * 16000)


def _read_exact(size: int) -> bytes | None:
    chunks = []
    remaining = size
    while remaining:
        chunk = sys.stdin.buffer.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _samples(pcm: bytes) -> array.array:
    if len(pcm) % 2:
        pcm = pcm[:-1]
    shorts = array.array("h")
    shorts.frombytes(pcm)
    if sys.byteorder != "little":
        shorts.byteswap()
    scale = 1.0 / 32768.0
    return array.array("f", (sample * scale for sample in shorts))


def _respond(payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(_HEADER.pack(len(body)))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def main() -> None:
    from needle.agent.whistle import Whistle

    model = Whistle()
    print("whistle ready", file=sys.stderr, flush=True)
    while True:
        header = _read_exact(_HEADER.size)
        if header is None:
            return
        (size,) = _HEADER.unpack(header)
        pcm = _read_exact(size)
        if pcm is None:
            return
        try:
            samples = _samples(pcm)
            if len(samples) > _MAX_SAMPLES:
                raise RuntimeError("audio limit is 30 s")
            if not samples:
                result = {"text": "", "language": "", "words": []}
            else:
                result = model.transcribe(samples, language="en", word_timestamps=True)
        except Exception as exc:
            result = {"error": str(exc), "text": "", "words": []}
        _respond(result)


if __name__ == "__main__":
    main()
