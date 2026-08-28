"""The Agent Phone daemon: routes phone input to terminal attention.

Phone-agnostic core (attention router, window focus, Claude Code hooks,
speech-to-text) with two phone backends:

  usb  Polycom CX300: HID keys/hook/LED/LCD + CoreAudio capture via ffmpeg
  sip  Polycom VVX:   SIP registrar, MWI LED, persistent call, RTP DTMF

Flow:
  #            bind the frontmost terminal window
  Stop hook    mark that session's window as needing attention -> LED
  *            focus the next window needing attention
  off-hook     record handset audio; on hang-up transcribe and paste into
               the frontmost terminal
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pathlib
import shlex
import signal
import socket
import subprocess
import time

from agent_phone import macfocus
from agent_phone.attention import AttentionRouter
from agent_phone.hookserver import HookCallbacks, HookServer

log = logging.getLogger("agent_phone")

RECORDINGS_DIR = pathlib.Path.home() / ".agent-phone" / "recordings"
BINDINGS_PATH = pathlib.Path.home() / ".agent-phone" / "bindings.json"


def _window_key(ref: macfocus.WindowRef) -> str:
    return f"{ref.app}:{ref.window_id}"


class AgentPhoneDaemon:
    """Core logic; a backend supplies phone I/O via the handle_* methods."""

    def __init__(self, http_port: int = 8489,
                 stt_command: str | None = None,
                 voice_mode: str = "claude",
                 dictation_key: str = " ",
                 bindings_path: pathlib.Path = BINDINGS_PATH) -> None:
        self.stt_command = stt_command
        self.voice_mode = voice_mode          # claude | record | off
        self.dictation_key = dictation_key
        self._ptt_proc: subprocess.Popen | None = None
        self._offhook_mode = voice_mode
        self.loop: asyncio.AbstractEventLoop | None = None
        self.backend = None            # set by main()

        self.router = AttentionRouter()
        self.windows: dict[str, macfocus.WindowRef] = {}
        self.sessions: dict[str, str] = {}      # agent session_id -> window key
        self.window_agent: dict[str, str] = {}  # window key -> claude | codex
        self._led_on = False
        self._bindings_path = bindings_path
        self._load_bindings()

        self.hooks = HookServer(HookCallbacks(
            on_turn_done=lambda p: self._call_soon(self._turn_done, p),
            on_turn_start=lambda p: self._call_soon(self._turn_start, p),
            on_phone_event=lambda e: self._call_soon(self._phone_event, e),
        ), port=http_port)

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        await self.backend.start(self)
        self.hooks.start()
        log.info("agent-phone up (%s backend), hooks on http://127.0.0.1:%d",
                 self.backend.name, self.hooks.port)
        await asyncio.Event().wait()

    def _call_soon(self, fn, *args) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(fn, *args)

    # -- phone input (backends call these, loop thread) ---------------------
    def handle_key(self, digit: str) -> None:
        if digit == "#":
            self.bind_frontmost()
        elif digit == "*":
            self.focus_next()
        elif digit == "0":
            self.minimize_all()

    def _active_voice_mode(self) -> str:
        """Voice mode for the terminal the user is dictating into.

        Claude Code has native push-to-talk we can hold; Codex (and anything
        else) gets local record+transcribe+paste. The frontmost bound
        window's linked agent decides; --voice is the default/override.
        """
        if self.voice_mode == "off":
            return "off"
        if not self.window_agent:
            return self.voice_mode
        ref = macfocus.frontmost_window()
        if ref is not None:
            agent = self.window_agent.get(_window_key(ref))
            if agent == "codex":
                return "record"
            if agent == "claude":
                return "claude"
        return self.voice_mode

    def handle_offhook(self) -> None:
        self._offhook_mode = self._active_voice_mode()
        if self._offhook_mode == "claude":
            # Hold Claude Code's push-to-talk key for as long as the receiver
            # is up. Terminals detect a held key by its repeat stream, and
            # synthetic key events don't auto-repeat, so post key-downs on a
            # repeat-like cadence until hang-up.
            log.info("receiver up: holding push-to-talk")
            self._start_ptt_hold()
        elif self._offhook_mode == "record":
            log.info("receiver up: recording")
            self.backend.start_capture()

    def handle_onhook(self) -> None:
        mode = getattr(self, "_offhook_mode", self.voice_mode)
        if mode == "claude":
            log.info("receiver down: releasing push-to-talk")
            self._stop_ptt_hold()
            return
        if mode != "record":
            return
        wav = self.backend.stop_capture()
        if wav is None:
            return
        log.info("receiver down: %s", wav)
        assert self.loop is not None
        self.loop.run_in_executor(None, self._transcribe, wav)

    def _start_ptt_hold(self) -> None:
        if self._ptt_proc is not None:
            return
        cmd = ["osascript",
               "-e", "repeat 7500 times",   # ~5 min safety cap
               "-e", f'tell application "System Events" to key down '
                     f'"{self.dictation_key}"',
               "-e", "delay 0.04",
               "-e", "end repeat"]
        try:
            self._ptt_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
        except OSError as exc:
            log.error("could not start push-to-talk hold: %s", exc)

    def _stop_ptt_hold(self) -> None:
        proc, self._ptt_proc = self._ptt_proc, None

        def release() -> None:
            # The repeat loop must be FULLY dead before the key-up goes out,
            # or its final key-down can land after our release and leave
            # dictation engaged (took a second hang-up to clear).
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
            try:
                subprocess.run(
                    ["osascript", "-e",
                     f'tell application "System Events" to key up '
                     f'"{self.dictation_key}"'],
                    capture_output=True, timeout=5, check=True)
            except (subprocess.SubprocessError, OSError) as exc:
                log.error("push-to-talk key up failed: %s", exc)
        if self.loop is not None:
            self.loop.run_in_executor(None, release)
        else:
            release()

    # -- display ------------------------------------------------------------
    def _refresh(self, headline: str) -> None:
        """Repaint the four-corner dashboard: latest event, who's next,
        and the waiting/bound counts labeled with their phone keys."""
        waiting = self.router.needs_attention()
        labels = dict(self.router.bindings())
        key = self.router.current() or (waiting[0] if waiting else None)
        next_line = labels.get(key, key or "") if waiting else "all quiet"
        self.backend.show_dashboard(headline, next_line,
                                    f"*{len(waiting)}", f"#{len(labels)}")

    # -- binding / focus ----------------------------------------------------
    def _load_bindings(self) -> None:
        try:
            entries = json.loads(self._bindings_path.read_text())
        except (OSError, ValueError):
            return
        for entry in entries:
            try:
                ref = macfocus.WindowRef.from_dict(entry)
            except (KeyError, TypeError):
                continue
            key = _window_key(ref)
            self.windows[key] = ref
            self.router.bind(key, ref.label)
        if self.windows:
            log.info("restored %d binding(s)", len(self.windows))

    def _save_bindings(self) -> None:
        try:
            self._bindings_path.parent.mkdir(parents=True, exist_ok=True)
            self._bindings_path.write_text(json.dumps(
                [ref.to_dict() for ref in self.windows.values()], indent=2))
        except OSError as exc:
            log.warning("could not save bindings: %s", exc)

    def bind_frontmost(self) -> None:
        ref = macfocus.frontmost_window()
        if ref is None:
            log.info("# pressed but no terminal window is frontmost")
            self._refresh("no terminal focused")
            return
        key = _window_key(ref)
        self.windows[key] = ref
        self.router.bind(key, ref.label)
        self._save_bindings()
        log.info("bound %s (%s)", key, ref.label)
        self._refresh(f"bound {ref.label}")

    def focus_next(self) -> None:
        """* key: next terminal needing attention; when all is quiet,
        browse round-robin through every bound terminal instead."""
        self._prune_dead_windows()
        key = self.router.next_attention()
        browsing = key is None
        if browsing:
            key = self.router.next_bound()
        if key is None:
            log.info("* pressed but nothing is bound")
            self._refresh("nothing bound")
            return
        ref = self.windows.get(key)
        focused = ref is not None and macfocus.focus(ref)
        if focused:
            log.info("%s %s", "browsing" if browsing else "focused", key)
        if not browsing:
            # Notification-log semantics: cycling to a terminal marks it
            # read; the LED goes dark when the last waiting one is visited.
            self.router.clear_attention(key)
        if focused:
            self._refresh(("view: " if browsing else "go: ") + ref.label)
        self._sync_led()

    def minimize_all(self) -> None:
        """0 key: sweep every bound terminal window into the Dock."""
        self._prune_dead_windows()
        count = sum(1 for ref in self.windows.values() if macfocus.minimize(ref))
        log.info("minimized %d bound window(s)", count)
        self._refresh(f"minimized {count}" if count else "nothing bound")

    def _prune_dead_windows(self) -> None:
        pruned = False
        for key, ref in list(self.windows.items()):
            if not macfocus.exists(ref):
                self.router.unbind(key)
                del self.windows[key]
                pruned = True
                for sid, wkey in list(self.sessions.items()):
                    if wkey == key:
                        del self.sessions[sid]
        if pruned:
            self._save_bindings()

    # -- Claude Code hooks --------------------------------------------------
    def _turn_start(self, payload: dict) -> None:
        sid = payload.get("session_id")
        if not sid:
            return
        ref = macfocus.frontmost_window()
        if ref is not None and _window_key(ref) in self.windows:
            if self.sessions.get(sid) != _window_key(ref):
                log.info("linked session %s -> %s", sid, _window_key(ref))
            self.sessions[sid] = _window_key(ref)
            self.window_agent[_window_key(ref)] = payload.get("_agent", "claude")
        key = self.sessions.get(sid)
        if key:
            self.router.clear_attention(key)
            self._sync_led()

    def _turn_done(self, payload: dict) -> None:
        sid = payload.get("session_id")
        key = self.sessions.get(sid or "")
        if key is None:
            log.info("turn done for unbound session %s", sid)
            return
        if self.router.mark_attention(key):
            log.info("attention: %s", key)
            label = dict(self.router.bindings()).get(key, key)
            self._refresh(f"done {label}")
        self._sync_led()

    def _phone_event(self, event: dict) -> None:
        """Telephony events from a SIP phone's telNotification POSTs."""
        kind = event.get("type", "")
        if kind == "OffHookEvent":
            self.handle_offhook()
        elif kind == "OnHookEvent":
            self.handle_onhook()

    # -- speech to text -----------------------------------------------------
    def _transcribe(self, wav_path: pathlib.Path) -> None:
        if not self.stt_command:
            log.info("no --stt-command configured; audio saved to %s", wav_path)
            return
        cmd = [str(pathlib.Path(arg).expanduser()) if arg.startswith("~")
               else arg
               for arg in (a.replace("{wav}", str(wav_path))
                           for a in shlex.split(self.stt_command))]
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
            self.backend.set_led(want)


class UsbBackend:
    """Polycom CX300 over USB HID + CoreAudio capture through ffmpeg."""

    name = "usb"

    def __init__(self, audio_device: str = "Polycom CX300") -> None:
        self.audio_device = audio_device
        self.phone = None
        self.daemon: AgentPhoneDaemon | None = None
        self._ffmpeg: subprocess.Popen | None = None
        self._wav: pathlib.Path | None = None

    async def start(self, daemon: AgentPhoneDaemon) -> None:
        from agent_phone.cx300 import Cx300Phone
        self.daemon = daemon
        self.phone = Cx300Phone(
            on_key=lambda d: daemon._call_soon(daemon.handle_key, d),
            on_offhook=lambda: daemon._call_soon(daemon.handle_offhook),
            on_onhook=lambda: daemon._call_soon(daemon.handle_onhook),
            on_connect=lambda: daemon._call_soon(self._on_connect),
        )
        self.phone.start()

    def _on_connect(self) -> None:
        self.daemon._refresh("ready")
        self.phone.set_led(bool(self.daemon.router.needs_attention()))

    def set_led(self, on: bool) -> None:
        self.phone.set_led(on)

    def show(self, top: str, bottom: str) -> None:
        self.phone.show(top, bottom)

    def show_dashboard(self, tl: str, bl: str, tr: str, br: str) -> None:
        self.phone.show_dashboard(tl, bl, tr, br)

    def start_capture(self) -> None:
        if self._ffmpeg is not None:
            return
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        self._wav = RECORDINGS_DIR / f"utterance-{int(time.time())}.wav"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-f", "avfoundation", "-i", f":{self.audio_device}",
               "-ar", "16000", "-ac", "1", "-y", str(self._wav)]
        try:
            self._ffmpeg = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                            stderr=subprocess.PIPE)
        except OSError as exc:
            log.error("could not start ffmpeg capture: %s", exc)
            self._ffmpeg = None
            self._wav = None

    def stop_capture(self) -> pathlib.Path | None:
        proc, wav = self._ffmpeg, self._wav
        self._ffmpeg = None
        self._wav = None
        if proc is None:
            return None
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if wav is not None and wav.exists() and wav.stat().st_size > 44:
            return wav
        log.warning("capture produced no audio")
        return None


class SipBackend:
    """Polycom VVX over SIP/RTP (see docs/phone-setup.md)."""

    name = "sip"

    def __init__(self, local_ip: str, sip_port: int = 5060,
                 rtp_port: int = 4000) -> None:
        self.local_ip = local_ip
        self.sip_port = sip_port
        self.rtp_port = rtp_port
        self.daemon: AgentPhoneDaemon | None = None
        self.sip = None
        self.dtmf = None
        self.audio_pt: int | None = None
        self._buffer: bytearray | None = None

    async def start(self, daemon: AgentPhoneDaemon) -> None:
        from agent_phone.rtp import DtmfDetector, parse_rtp
        from agent_phone.sipd import UdpSipEndpoint
        self.daemon = daemon
        self._parse_rtp = parse_rtp
        self._DtmfDetector = DtmfDetector

        self.sip = UdpSipEndpoint(
            self.local_ip, self.sip_port,
            on_registered=lambda reg: self.sip.call is None
            and self.sip.place_call(self.rtp_port),
            on_call_established=self._on_call,
            on_call_down=self._on_call_down,
        )
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(
            lambda: self.sip, local_addr=("0.0.0.0", self.sip_port))

        backend = self

        class _Rtp(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr) -> None:
                backend._on_rtp(data)

        await loop.create_datagram_endpoint(
            _Rtp, local_addr=("0.0.0.0", self.rtp_port))

    def _on_call(self, call) -> None:
        self.dtmf = self._DtmfDetector(call.dtmf_pt if call.dtmf_pt is not None
                                       else 101)
        self.audio_pt = call.audio_pt if call.audio_pt is not None else 0

    def _on_call_down(self) -> None:
        self.dtmf = None
        assert self.daemon and self.daemon.loop
        self.daemon.loop.call_later(
            3.0, lambda: self.sip.registration and self.sip.call is None
            and self.sip.place_call(self.rtp_port))

    def _on_rtp(self, data: bytes) -> None:
        if self.dtmf is not None:
            digit = self.dtmf.feed(data)
            if digit is not None:
                self.daemon.handle_key(digit)
        if self._buffer is not None and self.audio_pt is not None:
            try:
                pkt = self._parse_rtp(data)
            except ValueError:
                return
            if pkt.payload_type == self.audio_pt:
                self._buffer.extend(pkt.payload)

    def set_led(self, on: bool) -> None:
        self.sip.set_mwi(on)

    def show(self, top: str, bottom: str) -> None:
        pass  # VVX screen is not driven in this backend

    def show_dashboard(self, tl: str, bl: str, tr: str, br: str) -> None:
        pass

    def start_capture(self) -> None:
        self._buffer = bytearray()

    def stop_capture(self) -> pathlib.Path | None:
        from agent_phone import g711
        buf, self._buffer = self._buffer, None
        if not buf:
            return None
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        wav = RECORDINGS_DIR / f"utterance-{int(time.time())}.wav"
        wav.write_bytes(g711.wav_bytes(g711.ulaw_to_pcm16(bytes(buf))))
        return wav


def _default_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Phone daemon")
    parser.add_argument("--backend", choices=("usb", "sip"), default="usb")
    parser.add_argument("--audio-device", default="Polycom CX300",
                        help="CoreAudio input name (usb backend)")
    parser.add_argument("--ip", default=None,
                        help="local IP the phone can reach (sip backend)")
    parser.add_argument("--sip-port", type=int, default=5060)
    parser.add_argument("--rtp-port", type=int, default=4000)
    parser.add_argument("--http-port", type=int, default=8489)
    parser.add_argument("--voice", choices=("claude", "record", "off"),
                        default="claude",
                        help="claude: hold Claude Code's push-to-talk key "
                             "while the receiver is up (its dictation types "
                             "into the prompt box); record: capture audio and "
                             "run --stt-command; off: ignore the receiver")
    parser.add_argument("--dictation-key", default=" ",
                        help="key held for Claude Code push-to-talk "
                             "(default: space)")
    default_model = pathlib.Path.home() / ".agent-phone/models/ggml-base.en.bin"
    default_stt = (f"whisper-cli -nt -m {default_model} -f {{wav}}"
                   if default_model.exists() else None)
    parser.add_argument("--stt-command", default=default_stt,
                        help="speech-to-text command for record mode; "
                             "{wav} is replaced with the recording path; "
                             "stdout is the transcript "
                             "(default: whisper-cli with the model in "
                             "~/.agent-phone/models, if present)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    daemon = AgentPhoneDaemon(args.http_port, args.stt_command,
                              voice_mode=args.voice,
                              dictation_key=args.dictation_key)
    if args.backend == "usb":
        daemon.backend = UsbBackend(args.audio_device)
    else:
        daemon.backend = SipBackend(args.ip or _default_local_ip(),
                                    args.sip_port, args.rtp_port)
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
