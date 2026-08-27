"""Daemon logic tests with a stub backend and patched macfocus."""
from agent_phone import daemon as daemon_mod
from agent_phone import macfocus
from agent_phone.daemon import AgentPhoneDaemon
from agent_phone.macfocus import WindowRef


class StubBackend:
    name = "stub"

    def __init__(self):
        self.led = False
        self.led_calls = []
        self.shown = []
        self.capturing = False

    def set_led(self, on):
        self.led = on
        self.led_calls.append(on)

    def show(self, top, bottom):
        self.shown.append((top, bottom))

    def start_capture(self):
        self.capturing = True

    def stop_capture(self):
        self.capturing = False
        return None


def make_daemon(monkeypatch, n_windows=3):
    daemon = AgentPhoneDaemon(http_port=0)
    daemon.backend = StubBackend()
    refs = [WindowRef(app="Terminal", window_id=100 + i, tab_index=1,
                      label=f"term-{i}") for i in range(n_windows)]
    focused = []
    monkeypatch.setattr(macfocus, "exists", lambda ref: True)
    monkeypatch.setattr(macfocus, "focus", lambda ref: focused.append(ref) or True)

    current = {"ref": None}
    monkeypatch.setattr(macfocus, "frontmost_window", lambda: current["ref"])

    def bind(i):
        current["ref"] = refs[i]
        daemon.bind_frontmost()

    def link_and_finish(i):
        # UserPromptSubmit while that window is frontmost links the session,
        # then Stop marks it needing attention.
        current["ref"] = refs[i]
        daemon._turn_start({"session_id": f"sid-{i}"})
        daemon._turn_done({"session_id": f"sid-{i}"})

    return daemon, refs, focused, bind, link_and_finish


def test_star_cycle_clears_like_notification_log(monkeypatch):
    daemon, refs, focused, bind, finish = make_daemon(monkeypatch)
    for i in range(3):
        bind(i)
    for i in range(3):
        finish(i)
    backend = daemon.backend
    assert backend.led is True
    assert daemon.router.needs_attention() == [
        "Terminal:100", "Terminal:101", "Terminal:102"]

    daemon.focus_next()          # visit first waiting terminal
    assert focused[-1] == refs[0]
    assert backend.led is True   # two still unread
    daemon.focus_next()
    assert focused[-1] == refs[1]
    assert backend.led is True   # one still unread
    daemon.focus_next()          # cycling to the LAST waiting terminal...
    assert focused[-1] == refs[2]
    assert backend.led is False  # ...clears the log: LED off
    assert daemon.router.needs_attention() == []

    # a further * finds nothing and does not re-focus anything
    n = len(focused)
    daemon.focus_next()
    assert len(focused) == n


def test_reread_after_new_attention(monkeypatch):
    daemon, refs, focused, bind, finish = make_daemon(monkeypatch)
    for i in range(2):
        bind(i)
    finish(0)
    daemon.focus_next()
    assert daemon.backend.led is False       # only item, read immediately
    finish(1)                                # new notification arrives
    assert daemon.backend.led is True
    daemon.focus_next()
    assert focused[-1] == refs[1]
    assert daemon.backend.led is False


def test_turn_start_still_clears_without_cycling(monkeypatch):
    daemon, refs, focused, bind, finish = make_daemon(monkeypatch)
    bind(0)
    finish(0)
    assert daemon.backend.led is True
    # user just starts typing in that terminal instead of pressing *
    daemon._turn_start({"session_id": "sid-0"})
    assert daemon.backend.led is False


def _capture_osascript(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class R:
            stdout = ""
        return R()

    monkeypatch.setattr(daemon_mod.subprocess, "run", fake_run)
    return calls


def test_voice_claude_holds_and_releases_push_to_talk(monkeypatch):
    calls = _capture_osascript(monkeypatch)
    d = AgentPhoneDaemon(http_port=0, voice_mode="claude")
    d.backend = StubBackend()
    d.handle_offhook()
    d.handle_onhook()
    assert len(calls) == 2
    assert calls[0][0] == "osascript" and 'key down " "' in calls[0][2]
    assert 'key up " "' in calls[1][2]
    assert d.backend.capturing is False       # no ffmpeg recording in this mode


def test_voice_claude_custom_key(monkeypatch):
    calls = _capture_osascript(monkeypatch)
    d = AgentPhoneDaemon(http_port=0, voice_mode="claude", dictation_key="g")
    d.backend = StubBackend()
    d.handle_offhook()
    assert 'key down "g"' in calls[0][2]


def test_voice_record_uses_backend_capture(monkeypatch):
    calls = _capture_osascript(monkeypatch)
    d = AgentPhoneDaemon(http_port=0, voice_mode="record")
    d.backend = StubBackend()
    d.handle_offhook()
    assert d.backend.capturing is True
    d.handle_onhook()
    assert d.backend.capturing is False
    assert calls == []                        # no push-to-talk keystrokes


def test_voice_off_ignores_receiver(monkeypatch):
    calls = _capture_osascript(monkeypatch)
    d = AgentPhoneDaemon(http_port=0, voice_mode="off")
    d.backend = StubBackend()
    d.handle_offhook()
    d.handle_onhook()
    assert calls == [] and d.backend.capturing is False
