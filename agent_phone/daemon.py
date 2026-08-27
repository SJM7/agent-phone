"""The Agent Phone daemon: wires the SIP endpoint, RTP/DTMF stream, attention
router, macOS focus layer, and Claude Code hooks together.

Flow:
  #            bind the frontmost terminal window
  Stop hook    mark that session's window as needing attention -> LED blinks
  *            focus the next window needing attention
  off-hook     record handset audio (G.711 -> WAV), transcribe, paste into
               the frontmost terminal on hang-up
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import shlex
import socket
import subprocess
import time

from agent_phone import g711, macfocus
from agent_phone.attention import AttentionRouter
from agent_phone.hookserver import HookCallbacks, HookServer
from agent_phone.rtp import DtmfDetector, parse_rtp
from agent_phone.sipd import CallState, Registration, UdpSipEndpoint

log = logging.getLogger("agent_phone")

RECORDINGS_DIR = pathlib.Path.home() / ".agent-phone" / "recordings"


def _window_key(ref: macfocus.WindowRef) -> str:
    return f"{ref.app}:{ref.window_id}"


class RtpReceiver(asyncio.DatagramProtocol):
    def __init__(self, daemon: "AgentPhoneDaemon") -> None:
        self.daemon = daemon
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.daemon.on_rtp(data)


class AgentPhoneDaemon:
    def __init__(self, local_ip: str, sip_port: int = 5060,
                 rtp_port: int = 4000, http_port: int = 8489,
                 stt_command: str | None = None) -> None:
        self.local_ip = local_ip
        self.rtp_port = rtp_port
        self.stt_command = stt_command
        self.loop: asyncio.AbstractEventLoop | None = None

        self.router = AttentionRouter()
        self.windows: dict[str, macfocus.WindowRef] = {}
        self.sessions: dict[str, str] = {}      # claude session_id -> window key
        self.dtmf: DtmfDetector | None = None
        self.audio_pt: int | None = None
        self.recording: bytearray | None = None
        self._led_on = False

        self.sip = UdpSipEndpoint(
            local_ip, sip_port,
            on_registered=self._on_registered,
            on_call_established=self._on_call_established,
            on_call_down=self._on_call_down,
        )
        self.hooks = HookServer(HookCallbacks(
            on_turn_done=self._on_turn_done,
            on_turn_start=self._on_turn_start,
            on_phone_event=self._on_phone_event,
        ), port=http_port)

    # -- lifecycle ----------------------------------------------------------
    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        await self.loop.create_datagram_endpoint(
            lambda: self.sip, local_addr=("0.0.0.0", self.sip.local_port))
        await self.loop.create_datagram_endpoint(
            lambda: RtpReceiver(self), local_addr=("0.0.0.0", self.rtp_port))
        self.hooks.start()
        log.info("agent-phone up: SIP %s:%d, RTP %d, hooks http://127.0.0.1:%d",
                 self.local_ip, self.sip.local_port, self.rtp_port,
                 self.hooks.port)
        await asyncio.Event().wait()

    def _call_soon(self, fn, *args) -> None:
        """Run fn on the event loop thread (hook callbacks arrive on threads)."""
        assert self.loop is not None
        self.loop.call_soon_threadsafe(fn, *args)

    # -- SIP events ---------------------------------------------------------
    def _on_registered(self, reg: Registration) -> None:
        if self.sip.call is None:
            self.sip.place_call(self.rtp_port)

    def _on_call_established(self, call: CallState) -> None:
        self.dtmf = DtmfDetector(call.dtmf_pt if call.dtmf_pt is not None else 101)
        self.audio_pt = call.audio_pt if call.audio_pt is not None else 0
        self._sync_led()

    def _on_call_down(self) -> None:
        self.dtmf = None
        assert self.loop is not None
        self.loop.call_later(3.0, lambda: self.sip.registration
                             and self.sip.call is None
                             and self.sip.place_call(self.rtp_port))

    # -- RTP / keypad -------------------------------------------------------
    def on_rtp(self, data: bytes) -> None:
        if self.dtmf is not None:
            digit = self.dtmf.feed(data)
            if digit == "#":
                self.bind_frontmost()
            elif digit == "*":
                self.focus_next()
        if self.recording is not None and self.audio_pt is not None:
            try:
                pkt = parse_rtp(data)
            except ValueError:
                return
            if pkt.payload_type == self.audio_pt:
                self.recording.extend(pkt.payload)

    def bind_frontmost(self) -> None:
        ref = macfocus.frontmost_window()
        if ref is None:
            log.info("# pressed but no terminal window is frontmost")
            return
        key = _window_key(ref)
        self.windows[key] = ref
        self.router.bind(key, ref.label)
        log.info("bound %s (%s)", key, ref.label)

    def focus_next(self) -> None:
        self._prune_dead_windows()
        key = self.router.next_attention()
        if key is None:
            log.info("* pressed but nothing needs attention")
            return
        ref = self.windows.get(key)
        if ref is not None and macfocus.focus(ref):
            log.info("focused %s", key)
        self._sync_led()

    def _prune_dead_windows(self) -> None:
        for key, ref in list(self.windows.items()):
            if not macfocus.exists(ref):
                self.router.unbind(key)
                del self.windows[key]
                for sid, wkey in list(self.sessions.items()):
                    if wkey == key:
                        del self.sessions[sid]

    # -- Claude Code hooks --------------------------------------------------
    def _on_turn_start(self, payload: dict) -> None:
        self._call_soon(self._turn_start, payload)

    def _turn_start(self, payload: dict) -> None:
        sid = payload.get("session_id")
        if not sid:
            return
        ref = macfocus.frontmost_window()
        if ref is not None and _window_key(ref) in self.windows:
            self.sessions[sid] = _window_key(ref)
        key = self.sessions.get(sid)
        if key:
            self.router.clear_attention(key)
            self._sync_led()

    def _on_turn_done(self, payload: dict) -> None:
        self._call_soon(self._turn_done, payload)

    def _turn_done(self, payload: dict) -> None:
        sid = payload.get("session_id")
        key = self.sessions.get(sid or "")
        if key is None:
            log.info("turn done for unbound session %s", sid)
            return
        if self.router.mark_attention(key):
            log.info("attention: %s", key)
        self._sync_led()

    # -- phone hook state / recording ---------------------------------------
    def _on_phone_event(self, event: dict) -> None:
        self._call_soon(self._phone_event, event)

    def _phone_event(self, event: dict) -> None:
        kind = event.get("type", "")
        if kind == "OffHookEvent":
            self.recording = bytearray()
            log.info("receiver up: recording")
        elif kind == "OnHookEvent" and self.recording is not None:
            pcm_ulaw = bytes(self.recording)
            self.recording = None
            log.info("receiver down: %d bytes of audio", len(pcm_ulaw))
            if pcm_ulaw:
                assert self.loop is not None
                self.loop.run_in_executor(None, self._transcribe, pcm_ulaw)

    def _transcribe(self, pcm_ulaw: bytes) -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        wav_path = RECORDINGS_DIR / f"utterance-{int(time.time())}.wav"
        wav_path.write_bytes(g711.wav_bytes(g711.ulaw_to_pcm16(pcm_ulaw)))
        if not self.stt_command:
            log.info("no --stt-command configured; audio saved to %s", wav_path)
            return
        cmd = [arg.replace("{wav}", str(wav_path))
               for arg in shlex.split(self.stt_command)]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=120, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            log.error("stt command failed: %s", exc)
            return
        text = out.stdout.strip()
        if text:
            self._call_soon(self._deliver_transcript, text)

    def _deliver_transcript(self, text: str) -> None:
        """Put the transcript on the clipboard and paste into the front app."""
        try:
            subprocess.run(["pbcopy"], input=text.encode(), timeout=5, check=True)
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to keystroke "v" using command down'],
                capture_output=True, timeout=5, check=True)
            log.info("transcript delivered (%d chars)", len(text))
        except (subprocess.SubprocessError, OSError) as exc:
            log.error("could not paste transcript (left on clipboard): %s", exc)

    # -- LED ----------------------------------------------------------------
    def _sync_led(self) -> None:
        want = bool(self.router.needs_attention())
        if want != self._led_on:
            self._led_on = want
            self.sip.set_mwi(want)


def _default_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Phone daemon")
    parser.add_argument("--ip", default=None, help="local IP the phone can reach")
    parser.add_argument("--sip-port", type=int, default=5060)
    parser.add_argument("--rtp-port", type=int, default=4000)
    parser.add_argument("--http-port", type=int, default=8489)
    parser.add_argument("--stt-command", default=None,
                        help="speech-to-text command; {wav} is replaced with "
                             "the recording path; stdout is the transcript "
                             "(e.g. 'whisper-cli -nt -f {wav}')")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    daemon = AgentPhoneDaemon(args.ip or _default_local_ip(), args.sip_port,
                              args.rtp_port, args.http_port, args.stt_command)
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
