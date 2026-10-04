"""Turn Whistle's 30-second clips into one transcript while a call is open.

Whistle transcribes a finished buffer and rejects anything over 30 seconds.
The encoder reads the whole buffer, so a word can still change when later
audio arrives. Until the tail reaches 26 seconds, nothing is frozen: each
pass replaces the hypothesis, and hangup pastes that hypothesis. Past 26
seconds the words well behind the live edge are kept, the audio is dropped,
and the next window supplies the seam.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import struct
import subprocess
import threading
import wave
from pathlib import Path

log = logging.getLogger("agent_phone.whistle")

SAMPLE_RATE = 16000
OVERLAP_S = 1.0
MAX_TAIL_S = 26.0
FORCE_KEEP_S = 3.0
SILENCE_KEEP_S = 2.0
MAX_MODEL_S = 28.0
_HEADER = struct.Struct(">I")


def _norm_token(token: str) -> str:
    return token.strip(".,!?;:\"'").lower()


def join_hypotheses(closed: str, latest: str) -> str:
    """Append a new window, dropping a repeated seam of up to six words."""
    if not closed:
        return latest
    if not latest:
        return closed
    head = closed.split()
    tail = latest.split()
    cap = min(6, len(head), len(tail))
    repeated = 0
    for count in range(1, cap + 1):
        if [_norm_token(word) for word in head[-count:]] == [_norm_token(word) for word in tail[:count]]:
            repeated = count
    return " ".join(head + tail[repeated:])


class StreamCommitter:
    """Hold the open tail and the transcript of any window already closed."""

    def __init__(self) -> None:
        self.pcm = bytearray()
        self.origin = 0.0
        self.closed_text = ""
        self.latest = ""

    def append(self, data: bytes) -> None:
        if len(data) % 2:
            data = data[:-1]
        self.pcm.extend(data)

    def duration(self) -> float:
        return (len(self.pcm) / 2) / SAMPLE_RATE

    def text(self) -> str:
        return join_hypotheses(self.closed_text, self.latest).strip()

    def absorb(self, result: dict, *, final: bool = False) -> None:
        """Fold one Whistle result into the transcript.

        Under 26 seconds the latest full-buffer text replaces the open
        hypothesis and the audio stays. Past that, words that end well
        before the live edge are closed and their audio is dropped. A
        final pass under 28 seconds pastes the whole buffer as one clip.
        """
        words = result.get("words") or []
        heard = (result.get("text") or "").strip()
        dur = self.duration()
        if dur < MAX_TAIL_S or (final and dur <= MAX_MODEL_S):
            if heard or final:
                self.latest = heard
            elif not self.latest and dur > SILENCE_KEEP_S:
                self._drop_until(self.origin + dur - SILENCE_KEEP_S)
            if final:
                self.pcm.clear()
            return

        cutoff = self.origin + dur - FORCE_KEEP_S
        closed: list[str] = []
        closed_through = self.origin
        for word in words:
            token = str(word.get("word") or "").strip()
            if not token:
                continue
            end = self.origin + float(word["end"])
            if end > cutoff:
                break
            closed.append(token)
            closed_through = end
        if not closed and heard:
            # No word ended before the lookahead. Keep the hypothesis and
            # the newest audio so the next pass still has the phrase.
            self.closed_text = join_hypotheses(self.closed_text, heard)
            self.latest = ""
            self._drop_until(self.origin + dur - FORCE_KEEP_S)
            return
        if closed:
            self.closed_text = join_hypotheses(self.closed_text, " ".join(closed))
            self.latest = ""
            self._drop_until(max(self.origin, closed_through - OVERLAP_S))

    def _drop_until(self, abs_time: float) -> None:
        if abs_time <= self.origin:
            return
        samples = int(round((abs_time - self.origin) * SAMPLE_RATE))
        nbytes = min(len(self.pcm), samples * 2)
        del self.pcm[:nbytes]
        self.origin += nbytes / 2 / SAMPLE_RATE

    def model_pcm(self) -> bytes:
        """PCM to send. Never longer than the model accepts."""
        max_bytes = int(MAX_MODEL_S * SAMPLE_RATE) * 2
        if len(self.pcm) > max_bytes:
            log.error("whistle tail exceeded %ss; dropping the oldest audio", MAX_MODEL_S)
            self._drop_until(self.origin + self.duration() - MAX_TAIL_S)
        return bytes(self.pcm)


class WhistleWorker:
    """One long-lived process with the model already loaded."""

    def __init__(self, python: str, repo_root: Path) -> None:
        self.python = python
        self.repo_root = repo_root
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stderr: threading.Thread | None = None

    def start(self) -> None:
        env = os.environ.copy()
        env["NEEDLE_TELEMETRY"] = "0"
        env["DO_NOT_TRACK"] = "1"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(self.repo_root), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        self.proc = subprocess.Popen(
            [self.python, "-m", "agent_phone.whistle_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self.repo_root),
        )
        self._stderr = threading.Thread(target=self._drain_stderr, name="whistle-stderr", daemon=True)
        self._stderr.start()
        log.info("whistle worker pid %s", self.proc.pid)

    def transcribe(self, pcm: bytes) -> dict:
        if not pcm:
            return {"text": "", "words": []}
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError("whistle worker is not running")
        with self._lock:
            assert self.proc.stdin and self.proc.stdout
            self.proc.stdin.write(_HEADER.pack(len(pcm)))
            self.proc.stdin.write(pcm)
            self.proc.stdin.flush()
            header = _read_exact(self.proc.stdout, _HEADER.size)
            (size,) = _HEADER.unpack(header)
            body = _read_exact(self.proc.stdout, size)
        result = json.loads(body.decode("utf-8"))
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None or proc.poll() is not None:
            return
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _drain_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            log.info("whistle: %s", line.decode("utf-8", "replace").rstrip())


class LiveCapture:
    """Read ffmpeg's 16 kHz s16le stream, commit words, write a WAV copy."""

    def __init__(self, proc: subprocess.Popen, wav_path: Path, worker: WhistleWorker) -> None:
        self.proc = proc
        self.wav_path = wav_path
        self.worker = worker
        self.committer = StreamCommitter()
        self.transcript: str | None = None
        self.error: Exception | None = None
        self._decoded_end = -1.0
        self._thread = threading.Thread(target=self._run, name="whistle-capture", daemon=True)
        self._stderr = threading.Thread(target=self._drain_ffmpeg, name="ffmpeg-stderr", daemon=True)

    def start(self) -> None:
        self._stderr.start()
        self._thread.start()

    def finish(self) -> str | None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
        self._thread.join(timeout=20)
        if self._thread.is_alive():
            self.proc.kill()
            self._thread.join(timeout=3)
            self.error = self.error or RuntimeError("whistle capture hung")
        if self.error:
            return None
        return self.transcript or ""

    def _run(self) -> None:
        import time
        wav = wave.open(str(self.wav_path), "wb")
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        next_tick = time.monotonic() + 0.5
        try:
            assert self.proc.stdout is not None
            while True:
                chunk = self.proc.stdout.read(3200)
                if not chunk:
                    break
                wav.writeframes(chunk)
                self.committer.append(chunk)
                now = time.monotonic()
                if now >= next_tick:
                    try:
                        self._decode(final=False)
                    except Exception:
                        log.exception("whistle tick failed; recording continues")
                        next_tick = now + 5
                    else:
                        next_tick = now + 0.5
            self._decode(final=True)
            self.transcript = self.committer.text()
        except Exception as exc:
            log.exception("whistle capture failed")
            self.error = exc
        finally:
            wav.close()

    def _decode(self, *, final: bool) -> None:
        end = self.committer.origin + self.committer.duration()
        if not final and end < 0.4:
            return
        if not final and end <= self._decoded_end + 0.05:
            return
        pcm = self.committer.model_pcm()
        result = self.worker.transcribe(pcm) if pcm else {"text": "", "words": []}
        self.committer.absorb(result, final=final)
        self._decoded_end = self.committer.origin + self.committer.duration()
        if self.committer.text():
            log.debug("whistle hypothesis: %s", self.committer.text())

    def _drain_ffmpeg(self) -> None:
        if self.proc.stderr is None:
            return
        for line in self.proc.stderr:
            log.info("ffmpeg: %s", line.decode("utf-8", "replace").rstrip())


def _read_exact(stream, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise RuntimeError("whistle worker closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
