"""The Agent Phone daemon: routes phone input to terminal attention.

Phone-agnostic core (attention router, window focus, agent hooks,
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
from agent_phone.whistle_stream import LiveCapture, WhistleWorker

log = logging.getLogger("agent_phone")

RECORDINGS_DIR = pathlib.Path.home() / ".agent-phone" / "recordings"
BINDINGS_PATH = pathlib.Path.home() / ".agent-phone" / "bindings.json"
HERMES_TURN_SETTLE = 4.0    # quiet seconds after a Hermes LLM call = turn done
# Grok binds Ctrl+Space and F8. AppleScript cannot `key down` a Control+Space
# chord (syntax error -2740). F8 is key code 100 and CAN be held.
GROK_PTT_DOWN = 'tell application "System Events" to key down 100'
GROK_PTT_UP = 'tell application "System Events" to key up 100'
# Whisper emits this on silence. It is not something the user said.
_BLANK_TRANSCRIPTS = {"[BLANK_AUDIO]", "[SILENCE]", "[INAUDIBLE]"}


def _usable_transcript(text: str) -> str:
    cleaned = text.strip()
    if cleaned.upper() in _BLANK_TRANSCRIPTS:
        return ""
    return cleaned


def _window_key(ref: macfocus.WindowRef) -> str:
    return f"{ref.app}:{ref.window_id}"


def match_agent(command: str) -> str | None:
    """Identify a harness from one `ps` command= line (already lowercased)."""
    # Basename of the executable only — a path like ~/.grok/config.toml
    # must not look like the Grok Build TUI.
    first = command.split(None, 1)[0] if command.strip() else ""
    base = first.rsplit("/", 1)[-1]
    if base == "grok" or base.startswith("grok-"):
        return "grok"
    if "hermes" in command:
        return "hermes"
    if "codex" in command:
        return "codex"
    if "claude" in command:
        return "claude"
    return None


def grok_main_turn_ended(payload: dict) -> bool:
    """True when a Grok Stop hook is a real main-agent turn completion.

    Grok also fires observe-only Stop at session end, and the same hook
    file can run inside a subagent. Neither should blink the lamp.
    """
    if payload.get("subagentType") or payload.get("subagent_type"):
        return False
    reason = payload.get("reason")
    return reason is None or reason == "end_turn"


class AgentPhoneDaemon:
    """Core logic; a backend supplies phone I/O via the handle_* methods."""

    def __init__(self, http_port: int = 8489,
                 stt_command: str | None = None,
                 voice_mode: str = "claude",
                 dictation_key: str = " ",
                 bindings_path: pathlib.Path = BINDINGS_PATH,
                 whiteboard_dir: pathlib.Path | None = None,
                 stt_engine: str = "whisper",
                 whistle_python: str | None = None) -> None:
        self.stt_command = stt_command
        self.stt_engine = stt_engine          # whisper | whistle
        self.whistle_python = whistle_python
        self.whistle_worker: WhistleWorker | None = None
        self.voice_mode = voice_mode          # claude | record | off
        self.dictation_key = dictation_key
        self._ptt_proc: subprocess.Popen | None = None
        self._ptt_held = False
        self._offhook_mode = voice_mode
        self.loop: asyncio.AbstractEventLoop | None = None
        self.backend = None            # set by main()

        self.router = AttentionRouter()
        self.windows: dict[str, macfocus.WindowRef] = {}
        self.sessions: dict[str, str] = {}      # agent session_id -> window key
        self.window_agent: dict[str, str] = {}  # window key -> claude|codex|hermes|grok
        self._pending_done: dict[str, asyncio.TimerHandle] = {}
        self.busy: set[str] = set()             # window keys with a turn running
        self._led_state: str | None = None
        self._bindings_path = bindings_path
        self._save_bindings()
        from agent_phone.whiteboard import WhiteboardBridge
        self.whiteboard = WhiteboardBridge(whiteboard_dir) if whiteboard_dir else None
        self._selected_target = None
        self._whiteboard_session = None

        self.hooks = HookServer(HookCallbacks(
            on_turn_done=lambda p: self._call_soon(self._turn_done, p),
            on_turn_start=lambda p: self._call_soon(self._turn_start, p),
            on_phone_event=lambda e: self._call_soon(self._phone_event, e),
        ), port=http_port, whiteboard=self.whiteboard)

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        if self.stt_engine == "whistle":
            self._start_whistle()
        await self.backend.start(self)
        self.hooks.start()
        log.info("agent-phone up (%s backend, %s stt), hooks on http://127.0.0.1:%d",
                 self.backend.name, self.stt_engine, self.hooks.port)
        await asyncio.Event().wait()

    def _start_whistle(self) -> None:
        python = self.whistle_python
        if not python or not pathlib.Path(python).exists():
            raise SystemExit(f"whistle python not found: {python}")
        self.whistle_worker = WhistleWorker(python, pathlib.Path(__file__).resolve().parent.parent)
        self.whistle_worker.start()

    def close(self) -> None:
        if self.whistle_worker is not None:
            self.whistle_worker.close()
            self.whistle_worker = None

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
        elif digit.isdigit():
            self.jump_to(int(digit))

    def handle_button(self, name: str) -> None:
        """The three function keys: Redial sends, Hold interrupts,
        Delete clears the input line — all aimed at the frontmost app."""
        if name == "redial":
            log.info("redial: sending Enter")
            self._send_keystroke("key code 36")            # Return
            self._refresh("sent")
        elif name == "hold":
            log.info("hold: sending Escape (interrupt)")
            self._send_keystroke("key code 53")            # Escape
            self._refresh("interrupt")
        elif name == "delete":
            log.info("delete: clearing input line")
            self._send_keystroke('keystroke "u" using control down')
            self._refresh("cleared")

    def _send_keystroke(self, action: str) -> None:
        script = f'tell application "System Events" to {action}'

        def run() -> None:
            try:
                subprocess.run(["osascript", "-e", script], capture_output=True,
                               timeout=5, check=True)
            except (subprocess.SubprocessError, OSError) as exc:
                log.error("keystroke failed: %s", exc)
        if self.loop is not None:
            self.loop.run_in_executor(None, run)
        else:
            run()

    def jump_to(self, n: int) -> None:
        """Speed dial: digit N focuses the Nth bound terminal (bind order)."""
        self._prune_dead_windows()
        keys = [k for k, _ in self.router.bindings()]
        if not 1 <= n <= len(keys):
            log.info("%d pressed but only %d terminal(s) bound", n, len(keys))
            self._refresh(f"no terminal {n}")
            return
        key = keys[n - 1]
        ref = self.windows.get(key)
        if ref is not None and macfocus.focus(ref):
            self._selected_target = ref
            log.info("jumped to %d: %s", n, key)
            self.router.clear_attention(key)
            self._refresh(f"{n}: {ref.label}")
        self._sync_led()

    def _detect_agent(self, ref: macfocus.WindowRef) -> str | None:
        """Which harness runs in this window, from its tty's process list.

        Works before any hook has fired (a freshly opened terminal has
        submitted no prompt yet). Last match wins so the most recently
        started harness decides if a tty somehow hosts traces of both.
        """
        term = macfocus.tty(ref)
        if not term:
            return None
        try:
            out = subprocess.run(
                ["ps", "-t", term.removeprefix("/dev/"), "-o",
                 "stat=,command="],
                capture_output=True, text=True, timeout=5)
        except (subprocess.SubprocessError, OSError):
            return None

        # Prefer the FOREGROUND process (stat contains '+') — that is the TUI
        # the user is actually looking at. Background stragglers on the same
        # tty (a stray `codex exec`, a helper) must not win over it.
        agent_fg = agent_any = None
        for line in out.stdout.lower().splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            stat, command = parts
            found = match_agent(command)
            if found:
                agent_any = found
                if "+" in stat:
                    agent_fg = found
        return agent_fg or agent_any

    def _agent_for(self, ref: macfocus.WindowRef) -> str | None:
        key = _window_key(ref)
        agent = self.window_agent.get(key)
        if agent is None:
            agent = self._detect_agent(ref)
            if agent:
                self.window_agent[key] = agent
                log.info("detected %s in %s", agent, key)
        return agent

    def _active_voice_mode(self) -> str:
        """Voice mode for the terminal the user is dictating into.

        Claude Code has native push-to-talk we can hold. Codex and Hermes
        record locally and paste. Grok does too when --stt whistle is on;
        otherwise Grok's own F8 hold is used. Live tty detection wins over
        a stale hook tag (Grok may also load Claude's hooks). --voice is
        the default when the frontmost window is unidentified.
        """
        if self.voice_mode == "off":
            return "off"
        # Identify whatever terminal is frontmost, bound or not — binding is
        # for the lamp and cycling, never a prerequisite for dictating.
        ref = macfocus.frontmost_window()
        if ref is not None:
            live = self._detect_agent(ref)
            if live:
                self.window_agent[_window_key(ref)] = live
            agent = live or self.window_agent.get(_window_key(ref))
            if agent in ("codex", "hermes"):
                return "record"       # no native dictation in these TUIs
            if agent == "grok":
                # Whistle replaces the cloud engine: same record-and-paste
                # path Codex already uses, with the warm worker underneath.
                return "record" if self.stt_engine == "whistle" else "grok"
            if agent == "claude":
                return "claude"
        return self.voice_mode

    def handle_offhook(self) -> None:
        if self.whiteboard and self.voice_mode != "off":
            try:
                self._whiteboard_session = self.whiteboard.begin(self._selected_target)
            except ValueError as exc:
                self._offhook_mode = "off"
                self._refresh(str(exc))
                return
            if self._whiteboard_session:
                target = self._whiteboard_session["target"]
                self._whiteboard_session["tty"] = macfocus.tty(target) if target else None
                self._offhook_mode = "record"
                self.backend.start_capture()
                return
        self._offhook_mode = self._active_voice_mode()
        if self._offhook_mode in ("claude", "grok"):
            log.info("receiver up: holding %s push-to-talk", self._offhook_mode)
            self._start_ptt_hold()
        elif self._offhook_mode == "record":
            log.info("receiver up: recording")
            self.backend.start_capture()

    def handle_onhook(self) -> None:
        mode = getattr(self, "_offhook_mode", self.voice_mode)
        if mode in ("claude", "grok"):
            log.info("receiver down: releasing push-to-talk")
            self._stop_ptt_hold()
            return
        if mode != "record":
            return
        session, self._whiteboard_session = self._whiteboard_session, None
        if session:
            self.whiteboard.finish(session)
        if self.stt_engine == "whistle":
            assert self.loop is not None
            self.loop.run_in_executor(None, self._finish_live, session)
            return
        wav = self.backend.stop_capture()
        if wav is None:
            if session:
                self.whiteboard.set_status(session, "failed", "No audio captured; marks remain in Sheets")
            return
        log.info("receiver down: %s", wav)
        assert self.loop is not None
        if session:
            self.loop.run_in_executor(None, self._transcribe, wav, session)
        else:
            self.loop.run_in_executor(None, self._transcribe, wav)

    def _finish_live(self, session) -> None:
        """Hangup: the worker already holds the committed words. Paste once."""
        wav = self.backend.stop_capture()
        text = getattr(self.backend, "live_transcript", None)
        if hasattr(self.backend, "live_transcript"):
            self.backend.live_transcript = None
        if text:
            text = _usable_transcript(text)
        if text is None:
            if wav is None:
                if session:
                    self.whiteboard.set_status(session, "failed", "No audio captured; marks remain in Sheets")
                log.info("receiver down: no audio")
                return
            log.info("whistle missed; falling back to file stt for %s", wav)
            self._transcribe(wav, session)
            return
        if not text:
            log.info("whistle heard no speech")
            if session:
                self.whiteboard.set_status(session, "failed", "Empty transcript; handoff saved, not pasted")
            return
        log.info("whistle transcript (%d chars)", len(text))
        if session:
            prompt = self.whiteboard.bundle(session, text)
            if prompt:
                self._call_soon(self._deliver_whiteboard, prompt, session)
            return
        self._call_soon(self._deliver_transcript, text)

    def _ptt_down_tell(self) -> str:
        return (f'tell application "System Events" to key down '
                f'"{self.dictation_key}"')

    def _ptt_up_tells(self) -> list[str]:
        return [f'tell application "System Events" to key up '
                f'"{self.dictation_key}"']

    def _osascript(self, tells: list[str]) -> None:
        args = ["osascript"]
        for tell in tells:
            args.extend(["-e", tell])
        result = subprocess.run(args, capture_output=True, timeout=5, text=True)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip() or (
                f"exit {result.returncode}")
            raise RuntimeError(err)

    def _start_ptt_hold(self) -> None:
        if self._ptt_proc is not None or self._ptt_held:
            return
        if self._offhook_mode == "grok":
            try:
                self._osascript([GROK_PTT_DOWN])
                self._ptt_held = True
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                log.error("could not start grok voice: %s", exc)
            return
        cmd = ["osascript",
               "-e", "repeat 7500 times",   # ~5 min safety cap
               "-e", self._ptt_down_tell(),
               "-e", "delay 0.04",
               "-e", "end repeat"]
        try:
            self._ptt_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
        except OSError as exc:
            log.error("could not start push-to-talk hold: %s", exc)

    def _stop_ptt_hold(self) -> None:
        proc, self._ptt_proc = self._ptt_proc, None
        held, self._ptt_held = self._ptt_held, False

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
                if held:
                    self._osascript([GROK_PTT_UP])
                else:
                    self._osascript(self._ptt_up_tells())
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
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
    def _save_bindings(self) -> None:
        """Write a current-session snapshot; never restore it on startup."""
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
        self._selected_target = ref
        self.router.bind(key, ref.label)
        self._save_bindings()
        agent = self._agent_for(ref)
        log.info("bound %s (%s)%s", key, ref.label,
                 f" [{agent}]" if agent else "")
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
            self._selected_target = ref
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
                self.busy.discard(key)
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
        # Hermes reports activity (pre_llm_call / pre_tool_call) rather than
        # a single turn-start; any activity cancels a pending turn-done.
        pending = self._pending_done.pop(sid, None)
        if pending is not None:
            pending.cancel()
        ref = macfocus.frontmost_window()
        if ref is not None and _window_key(ref) in self.windows:
            if self.sessions.get(sid) != _window_key(ref):
                log.info("linked session %s -> %s", sid, _window_key(ref))
            self.sessions[sid] = _window_key(ref)
            self.window_agent[_window_key(ref)] = payload.get("_agent", "claude")
        key = self.sessions.get(sid)
        if key:
            self.busy.add(key)
            self.router.clear_attention(key)
            self._sync_led()

    def _turn_done(self, payload: dict) -> None:
        sid = payload.get("session_id")
        # Grok also invokes Claude-compatible hooks, retaining its camelCase
        # envelope even when the forwarding script tags the event as Claude.
        grok_hook = (payload.get("_agent") == "grok" or
                     ("sessionId" in payload and "hookEventName" in payload))
        if grok_hook and not grok_main_turn_ended(payload):
            return
        if sid and payload.get("_agent") == "hermes":
            # Hermes has no turn-end event; a post_llm_call with nothing
            # following it IS the end of the turn. Debounce: fire only if
            # no further activity arrives within the settle window.
            pending = self._pending_done.pop(sid, None)
            if pending is not None:
                pending.cancel()
            if self.loop is not None:
                self._pending_done[sid] = self.loop.call_later(
                    HERMES_TURN_SETTLE, self._turn_done_now, sid)
                return
        self._turn_done_now(sid)

    def _turn_done_now(self, sid: str | None) -> None:
        self._pending_done.pop(sid, None)
        key = self.sessions.get(sid or "")
        if key is None:
            log.info("turn done for unbound session %s", sid)
            return
        self.busy.discard(key)
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
    def _transcribe(self, wav_path: pathlib.Path, session=None) -> None:
        if not self.stt_command:
            if session:
                self.whiteboard.set_status(session, "failed", "No transcription command configured; audio retained")
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
            if session:
                self.whiteboard.set_status(session, "failed", "Transcription failed; audio and saved marks retained")
            log.error("stt command failed: %s", exc)
            return
        text = _usable_transcript(out.stdout)
        if not text:
            log.info("file stt heard no speech in %s", wav_path)
        if session:
            prompt = self.whiteboard.bundle(session, text)
            if prompt and text:
                self._call_soon(self._deliver_whiteboard, prompt, session)
            elif prompt:
                self.whiteboard.set_status(session, "failed", "Empty transcript; handoff saved, not pasted")
            return
        if text:
            self._call_soon(self._deliver_transcript, text)

    def _deliver_whiteboard(self, text, session):
        target = session["target"]
        # Focus is deliberately verified; never fall back to the current browser.
        actual = None
        if target is not None and session.get("tty") and macfocus.focus(target):
            actual = macfocus.frontmost_window()
        if (actual is None or actual.app != target.app or actual.window_id != target.window_id
                or actual.tab_index != target.tab_index or actual.session_id != target.session_id
                or macfocus.tty(actual) != session.get("tty")):
            self.whiteboard.set_status(session, "failed", "Handoff saved. Select target with # or a phone digit before the next recording; paste brief.md manually for this one.")
            return
        if self._deliver_transcript(text):
            self.whiteboard.set_status(session, "delivered", "Handoff pasted · press Redial to send")
        else:
            self.whiteboard.set_status(session, "failed", "Paste failed; handoff saved in " + str(session["path"]))

    def _deliver_transcript(self, text: str) -> None:
        try:
            subprocess.run(["pbcopy"], input=text.encode(), timeout=5, check=True)
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to keystroke "v" using command down'],
                capture_output=True, timeout=5, check=True)
            log.info("transcript delivered (%d chars)", len(text))
            return True
        except (subprocess.SubprocessError, OSError) as exc:
            log.error("could not paste transcript (left on clipboard): %s", exc)
            return False

    # -- LED ----------------------------------------------------------------
    def _sync_led(self, force: bool = False) -> None:
        """One lamp, many terminals: show the highest-priority state."""
        if self.router.needs_attention():
            state = "attention"
        elif self.busy:
            state = "working"
        else:
            state = "idle"
        if force or state != self._led_state:
            self._led_state = state
            self.backend.set_led(state)


class UsbBackend:
    """Polycom CX300 over USB HID + CoreAudio capture through ffmpeg."""

    name = "usb"

    def __init__(self, audio_device: str = "Polycom CX300") -> None:
        self.audio_device = audio_device
        self.phone = None
        self.daemon: AgentPhoneDaemon | None = None
        self._ffmpeg: subprocess.Popen | None = None
        self._wav: pathlib.Path | None = None
        self._live: LiveCapture | None = None
        self.live_transcript: str | None = None

    async def start(self, daemon: AgentPhoneDaemon) -> None:
        from agent_phone.cx300 import Cx300Phone
        self.daemon = daemon
        self.phone = Cx300Phone(
            on_key=lambda d: daemon._call_soon(daemon.handle_key, d),
            on_offhook=lambda: daemon._call_soon(daemon.handle_offhook),
            on_onhook=lambda: daemon._call_soon(daemon.handle_onhook),
            on_connect=lambda: daemon._call_soon(self._on_connect),
            on_button=lambda n: daemon._call_soon(daemon.handle_button, n),
        )
        self.phone.start()

    def _on_connect(self) -> None:
        self.daemon._refresh("ready")
        self.daemon._sync_led(force=True)

    def set_led(self, state: str) -> None:
        self.phone.set_led(state)

    def show(self, top: str, bottom: str) -> None:
        self.phone.show(top, bottom)

    def show_dashboard(self, tl: str, bl: str, tr: str, br: str) -> None:
        self.phone.show_dashboard(tl, bl, tr, br)

    def start_capture(self) -> None:
        if self._ffmpeg is not None:
            return
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        self._wav = RECORDINGS_DIR / f"utterance-{int(time.time())}.wav"
        self.live_transcript = None
        worker = self.daemon.whistle_worker if self.daemon else None
        live = worker is not None and self.daemon is not None and self.daemon.stt_engine == "whistle"
        if live:
            # Raw samples on stdout so the worker can decode while the
            # receiver is still up. The WAV is written from those samples.
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
                   "-f", "avfoundation", "-i", f":{self.audio_device}",
                   "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"]
        else:
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
                   "-f", "avfoundation", "-i", f":{self.audio_device}",
                   "-ar", "16000", "-ac", "1", "-y", str(self._wav)]
        try:
            self._ffmpeg = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if live else subprocess.DEVNULL,
                stderr=subprocess.PIPE)
        except OSError as exc:
            log.error("could not start ffmpeg capture: %s", exc)
            self._ffmpeg = None
            self._wav = None
            return
        if live:
            assert self._wav is not None and worker is not None
            self._live = LiveCapture(self._ffmpeg, self._wav, worker)
            self._live.start()

    def stop_capture(self) -> pathlib.Path | None:
        live, self._live = self._live, None
        if live is not None:
            self.live_transcript = live.finish()
            self._ffmpeg = None
            wav = self._wav
            self._wav = None
            if wav is not None and wav.exists() and wav.stat().st_size > 44:
                return wav
            log.warning("capture produced no audio")
            return None
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

    def set_led(self, state: str) -> None:
        self.sip.set_mwi(state == "attention")

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
    parser.add_argument("--whiteboard-dir", type=pathlib.Path,
                        help="Enable authenticated local whiteboard handoffs in this directory")
    parser.add_argument("--voice", choices=("claude", "record", "off"),
                        default="claude",
                        help="fallback when the frontmost terminal's harness "
                             "is unknown. claude: hold Claude Code's "
                             "push-to-talk key; record: capture audio and "
                             "run --stt-command; off: ignore the receiver. "
                             "Grok Build uses native F8 hold unless "
                             "--stt whistle, which pastes locally. "
                             "Codex/Hermes always record")
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
                             "~/.agent-phone/models, if present). "
                             "Used as the fallback when --stt whistle fails")
    parser.add_argument("--stt", choices=("whisper", "whistle"), default="whisper",
                        help="whisper runs --stt-command after hangup. "
                             "whistle keeps a warm local model, transcribes "
                             "while the receiver is up, and pastes once on "
                             "hangup. Grok terminals use this paste path "
                             "instead of F8")
    parser.add_argument("--whistle-python", default=None,
                        help="python with cactus-needle installed "
                             "(default: ~/.agent-phone/whistle-venv/bin/python)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    whistle_python = args.whistle_python
    if args.stt == "whistle" and not whistle_python:
        whistle_python = str(pathlib.Path.home() / ".agent-phone" / "whistle-venv" / "bin" / "python")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    daemon = AgentPhoneDaemon(args.http_port, args.stt_command,
                              voice_mode=args.voice,
                              dictation_key=args.dictation_key,
                              whiteboard_dir=args.whiteboard_dir,
                              stt_engine=args.stt,
                              whistle_python=whistle_python)
    if args.backend == "usb":
        daemon.backend = UsbBackend(args.audio_device)
    else:
        daemon.backend = SipBackend(args.ip or _default_local_ip(),
                                    args.sip_port, args.rtp_port)
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass
    finally:
        daemon.close()


if __name__ == "__main__":
    main()
