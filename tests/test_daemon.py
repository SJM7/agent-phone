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
        self.dashboards = []
        self.capturing = False

    def set_led(self, on):
        self.led = on
        self.led_calls.append(on)

    def show(self, top, bottom):
        self.shown.append((top, bottom))

    def show_dashboard(self, tl, bl, tr, br):
        self.dashboards.append((tl, bl, tr, br))

    def start_capture(self):
        self.capturing = True

    def stop_capture(self):
        self.capturing = False
        return None


def make_daemon(monkeypatch, tmp_path, n_windows=3):
    daemon = AgentPhoneDaemon(http_port=0,
                              bindings_path=tmp_path / "bindings.json")
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


def test_star_cycle_clears_like_notification_log(monkeypatch, tmp_path):
    daemon, refs, focused, bind, finish = make_daemon(monkeypatch, tmp_path)
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

    # a further * switches to quiet-time browsing: it continues cycling
    # through all bound terminals, starting after the last visited one
    daemon.focus_next()
    assert focused[-1] == refs[0]
    assert backend.led is False
    daemon.focus_next()
    assert focused[-1] == refs[1]


def test_reread_after_new_attention(monkeypatch, tmp_path):
    daemon, refs, focused, bind, finish = make_daemon(monkeypatch, tmp_path)
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


def test_turn_start_still_clears_without_cycling(monkeypatch, tmp_path):
    daemon, refs, focused, bind, finish = make_daemon(monkeypatch, tmp_path)
    bind(0)
    finish(0)
    assert daemon.backend.led is True
    # user just starts typing in that terminal instead of pressing *
    daemon._turn_start({"session_id": "sid-0"})
    assert daemon.backend.led is False


class FakeProc:
    def __init__(self, cmd):
        self.cmd = cmd
        self.terminated = False

    def terminate(self):
        self.terminated = True


def _capture_osascript(monkeypatch):
    runs, procs = [], []

    def fake_run(cmd, **kwargs):
        runs.append(cmd)
        class R:
            stdout = ""
        return R()

    def fake_popen(cmd, **kwargs):
        proc = FakeProc(cmd)
        procs.append(proc)
        return proc

    monkeypatch.setattr(daemon_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(daemon_mod.subprocess, "Popen", fake_popen)
    return runs, procs


def test_voice_claude_holds_and_releases_push_to_talk(monkeypatch, tmp_path):
    runs, procs = _capture_osascript(monkeypatch)
    d = AgentPhoneDaemon(http_port=0, bindings_path=tmp_path / "b.json", voice_mode="claude")
    d.backend = StubBackend()
    d.handle_offhook()
    # hold = a repeat loop posting key-downs (synthetic events don't
    # auto-repeat, and the terminal detects a hold by its repeat stream)
    assert len(procs) == 1
    script = " ".join(procs[0].cmd)
    assert "repeat" in script and 'key down " "' in script
    d.handle_offhook()                        # second lift is a no-op
    assert len(procs) == 1
    d.handle_onhook()
    assert procs[0].terminated is True
    assert len(runs) == 1 and 'key up " "' in runs[0][2]
    assert d.backend.capturing is False       # no ffmpeg recording in this mode
    d.handle_onhook()                         # second hang-up: no double release
    assert len(runs) == 2                     # key up is still sent (harmless)


def test_voice_claude_custom_key(monkeypatch, tmp_path):
    runs, procs = _capture_osascript(monkeypatch)
    d = AgentPhoneDaemon(http_port=0, bindings_path=tmp_path / "b.json", voice_mode="claude", dictation_key="g")
    d.backend = StubBackend()
    d.handle_offhook()
    assert 'key down "g"' in " ".join(procs[0].cmd)


def test_voice_record_uses_backend_capture(monkeypatch, tmp_path):
    runs, procs = _capture_osascript(monkeypatch)
    d = AgentPhoneDaemon(http_port=0, bindings_path=tmp_path / "b.json", voice_mode="record")
    d.backend = StubBackend()
    d.handle_offhook()
    assert d.backend.capturing is True
    d.handle_onhook()
    assert d.backend.capturing is False
    assert runs == [] and procs == []         # no push-to-talk keystrokes


def test_dashboard_rendering(monkeypatch, tmp_path):
    daemon, refs, focused, bind, finish = make_daemon(monkeypatch, tmp_path)
    backend = daemon.backend
    for i in range(3):
        bind(i)
    assert backend.dashboards[-1] == ("bound term-2", "all quiet", "*0", "#3")
    finish(0)
    assert backend.dashboards[-1] == ("done term-0", "term-0", "*1", "#3")
    finish(1)
    assert backend.dashboards[-1] == ("done term-1", "term-0", "*2", "#3")
    daemon.focus_next()                       # visits term-0, clears it
    assert backend.dashboards[-1] == ("go: term-0", "term-1", "*1", "#3")
    daemon.focus_next()                       # last one read: quiet again
    assert backend.dashboards[-1] == ("go: term-1", "all quiet", "*0", "#3")
    daemon.focus_next()                       # quiet -> browse mode
    assert backend.dashboards[-1] == ("view: term-2", "all quiet", "*0", "#3")


def test_bindings_persist_across_restart(monkeypatch, tmp_path):
    path = tmp_path / "bindings.json"
    monkeypatch.setattr(macfocus, "exists", lambda ref: True)
    ref = WindowRef(app="Terminal", window_id=42, tab_index=1, label="term-42")
    monkeypatch.setattr(macfocus, "frontmost_window", lambda: ref)

    d1 = AgentPhoneDaemon(http_port=0, bindings_path=path)
    d1.backend = StubBackend()
    d1.bind_frontmost()
    assert path.exists()

    d2 = AgentPhoneDaemon(http_port=0, bindings_path=path)   # fresh "restart"
    d2.backend = StubBackend()
    assert d2.router.bindings() == [("Terminal:42", "term-42")]
    assert d2.windows["Terminal:42"] == ref
    # a session can link and mark attention right away after restart
    d2._turn_start({"session_id": "s1"})
    d2._turn_done({"session_id": "s1"})
    assert d2.backend.led is True


def test_voice_off_ignores_receiver(monkeypatch, tmp_path):
    runs, procs = _capture_osascript(monkeypatch)
    d = AgentPhoneDaemon(http_port=0, bindings_path=tmp_path / "b.json", voice_mode="off")
    d.backend = StubBackend()
    d.handle_offhook()
    d.handle_onhook()
    assert runs == [] and procs == []
    assert d.backend.capturing is False
