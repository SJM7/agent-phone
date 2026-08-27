"""Pure-logic tests for agent_phone.macfocus.

No test here touches osascript, the GUI, or Automation permission: parsing
functions are exercised directly and flow logic runs against a monkeypatched
`_run_osascript`.
"""

import logging

import pytest

from agent_phone import macfocus
from agent_phone.macfocus import (
    APP_ITERM,
    APP_TERMINAL,
    WindowRef,
    _applescript_quote,
    _normalize_app_name,
    _parse_bool,
    _parse_iterm_front,
    _parse_terminal_front,
)


# ---------------------------------------------------------------------------
# WindowRef round-trip
# ---------------------------------------------------------------------------


def test_windowref_roundtrip_terminal():
    ref = WindowRef(
        app=APP_TERMINAL, window_id=15636, tab_index=2, label="Programming"
    )
    assert WindowRef.from_dict(ref.to_dict()) == ref


def test_windowref_roundtrip_iterm():
    ref = WindowRef(
        app=APP_ITERM,
        window_id=7,
        session_id="B23BDB77-8CD8-4CD8-9F3A-33E1F6E3A1D1",
        label="codex session",
    )
    assert WindowRef.from_dict(ref.to_dict()) == ref


def test_windowref_from_dict_coerces_and_defaults():
    # Values as they might come back from a JSON binding registry.
    ref = WindowRef.from_dict({"app": "Terminal", "window_id": "15636"})
    assert ref.window_id == 15636
    assert ref.tab_index is None
    assert ref.session_id is None
    assert ref.label == ""


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def test_parse_terminal_front():
    out = "15636\n1\nProgramming — caffeinate ◂ claude — 280×70"
    ref = _parse_terminal_front(out)
    assert ref == WindowRef(
        app=APP_TERMINAL,
        window_id=15636,
        tab_index=1,
        session_id=None,
        label="Programming — caffeinate ◂ claude — 280×70",
    )


def test_parse_terminal_front_label_may_contain_newlines():
    ref = _parse_terminal_front("42\n3\nline one\nline two")
    assert ref.window_id == 42
    assert ref.tab_index == 3
    assert ref.label == "line one\nline two"


def test_parse_terminal_front_no_selected_tab_found():
    # Script returns 0 when the scan finds no selected tab.
    ref = _parse_terminal_front("42\n0\ntitle")
    assert ref.tab_index is None


def test_parse_terminal_front_rejects_garbage():
    assert _parse_terminal_front("") is None
    assert _parse_terminal_front("NONE") is None
    assert _parse_terminal_front("only-one-line") is None
    assert _parse_terminal_front("not-an-int\n1\ntitle") is None
    assert _parse_terminal_front("1\nnot-an-int\ntitle") is None


def test_parse_iterm_front():
    out = "3\nB23BDB77-8CD8-4CD8-9F3A-33E1F6E3A1D1\nmy window"
    ref = _parse_iterm_front(out)
    assert ref == WindowRef(
        app=APP_ITERM,
        window_id=3,
        tab_index=None,
        session_id="B23BDB77-8CD8-4CD8-9F3A-33E1F6E3A1D1",
        label="my window",
    )


def test_parse_iterm_front_rejects_garbage():
    assert _parse_iterm_front("") is None
    assert _parse_iterm_front("NONE") is None
    assert _parse_iterm_front("3\nsession-only") is None
    assert _parse_iterm_front("nope\nsid\ntitle") is None


def test_normalize_app_name():
    assert _normalize_app_name("Terminal") == APP_TERMINAL
    assert _normalize_app_name("iTerm2") == APP_ITERM
    assert _normalize_app_name("Discord") is None
    assert _normalize_app_name("") is None


def test_parse_bool():
    assert _parse_bool("true") is True
    assert _parse_bool("true\n") is True
    assert _parse_bool("false") is False
    assert _parse_bool("") is False
    assert _parse_bool(None) is False


def test_applescript_quote():
    assert _applescript_quote("abc") == '"abc"'
    assert _applescript_quote('a"b') == '"a\\"b"'
    assert _applescript_quote("a\\b") == '"a\\\\b"'


# ---------------------------------------------------------------------------
# Flow logic with monkeypatched _run_osascript
# ---------------------------------------------------------------------------


def fake_runner(monkeypatch, responses):
    """Patch _run_osascript to answer from {script_constant: output}."""
    calls = []

    def fake(script):
        calls.append(script)
        for key, value in responses.items():
            if script == key or script.startswith(key.split("{", 1)[0]):
                return value
        raise AssertionError(f"unexpected script: {script!r}")

    monkeypatch.setattr(macfocus, "_run_osascript", fake)
    return calls


def test_frontmost_window_terminal(monkeypatch):
    fake_runner(
        monkeypatch,
        {
            macfocus.FRONTMOST_APP_SCRIPT: "Terminal",
            macfocus.TERMINAL_FRONT_SCRIPT: "15636\n1\nmy title",
        },
    )
    ref = macfocus.frontmost_window()
    assert ref.app == APP_TERMINAL
    assert ref.window_id == 15636
    assert ref.tab_index == 1
    assert ref.label == "my title"


def test_frontmost_window_iterm(monkeypatch):
    fake_runner(
        monkeypatch,
        {
            macfocus.FRONTMOST_APP_SCRIPT: "iTerm2",
            macfocus.ITERM_FRONT_SCRIPT: "9\nDEADBEEF-0000\niterm title",
        },
    )
    ref = macfocus.frontmost_window()
    assert ref.app == APP_ITERM
    assert ref.window_id == 9
    assert ref.session_id == "DEADBEEF-0000"


def test_frontmost_window_other_app(monkeypatch):
    fake_runner(monkeypatch, {macfocus.FRONTMOST_APP_SCRIPT: "Discord"})
    assert macfocus.frontmost_window() is None


def test_frontmost_window_no_windows(monkeypatch):
    fake_runner(
        monkeypatch,
        {
            macfocus.FRONTMOST_APP_SCRIPT: "Terminal",
            macfocus.TERMINAL_FRONT_SCRIPT: "NONE",
        },
    )
    assert macfocus.frontmost_window() is None


def test_frontmost_window_osascript_failure(monkeypatch):
    monkeypatch.setattr(macfocus, "_run_osascript", lambda script: None)
    assert macfocus.frontmost_window() is None


def test_focus_terminal_formats_ids(monkeypatch):
    seen = []
    monkeypatch.setattr(
        macfocus, "_run_osascript", lambda s: seen.append(s) or "OK"
    )
    ref = WindowRef(app=APP_TERMINAL, window_id=15636, tab_index=2)
    assert macfocus.focus(ref) is True
    assert "window id 15636" in seen[0]
    assert "tab 2 of w" in seen[0]


def test_focus_terminal_without_tab(monkeypatch):
    seen = []
    monkeypatch.setattr(
        macfocus, "_run_osascript", lambda s: seen.append(s) or "OK"
    )
    ref = WindowRef(app=APP_TERMINAL, window_id=1)
    assert macfocus.focus(ref) is True
    # tab_index of None becomes 0, which the script skips.
    assert "if 0 > 0" in seen[0]


def test_focus_iterm_quotes_session_id(monkeypatch):
    seen = []
    monkeypatch.setattr(
        macfocus, "_run_osascript", lambda s: seen.append(s) or "OK"
    )
    ref = WindowRef(app=APP_ITERM, window_id=3, session_id='sid"x')
    assert macfocus.focus(ref) is True
    assert 'if id of s is "sid\\"x"' in seen[0]


def test_focus_missing_window(monkeypatch):
    monkeypatch.setattr(macfocus, "_run_osascript", lambda s: "MISSING")
    ref = WindowRef(app=APP_TERMINAL, window_id=424242)
    assert macfocus.focus(ref) is False


def test_focus_osascript_failure(monkeypatch):
    monkeypatch.setattr(macfocus, "_run_osascript", lambda s: None)
    ref = WindowRef(app=APP_ITERM, window_id=3, session_id="sid")
    assert macfocus.focus(ref) is False


def test_focus_unknown_app_returns_false(monkeypatch):
    monkeypatch.setattr(
        macfocus,
        "_run_osascript",
        lambda s: pytest.fail("should not run osascript"),
    )
    assert macfocus.focus(WindowRef(app="Kitty", window_id=1)) is False


def test_exists_true_false_and_failure(monkeypatch):
    ref = WindowRef(app=APP_TERMINAL, window_id=15636)
    monkeypatch.setattr(macfocus, "_run_osascript", lambda s: "true")
    assert macfocus.exists(ref) is True
    monkeypatch.setattr(macfocus, "_run_osascript", lambda s: "false")
    assert macfocus.exists(ref) is False
    monkeypatch.setattr(macfocus, "_run_osascript", lambda s: None)
    assert macfocus.exists(ref) is False
    assert macfocus.exists(WindowRef(app="Kitty", window_id=1)) is False


def test_exists_iterm_formats_window_id(monkeypatch):
    seen = []
    monkeypatch.setattr(
        macfocus, "_run_osascript", lambda s: seen.append(s) or "true"
    )
    assert macfocus.exists(WindowRef(app=APP_ITERM, window_id=77)) is True
    assert "iTerm2" in seen[0] and "window id 77" in seen[0]


# ---------------------------------------------------------------------------
# _run_osascript error handling (subprocess mocked, still no GUI)
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_osascript_automation_denied_logs_hint(monkeypatch, caplog):
    err = (
        "execution error: Not authorized to send Apple events to "
        "System Events. (-1743)"
    )
    monkeypatch.setattr(
        macfocus.subprocess,
        "run",
        lambda *a, **k: _FakeProc(1, stderr=err),
    )
    with caplog.at_level(logging.ERROR, logger="agent_phone.macfocus"):
        assert macfocus._run_osascript("whatever") is None
    assert "Privacy & Security" in caplog.text
    assert "-1743" in caplog.text


def test_run_osascript_generic_failure_returns_none(monkeypatch, caplog):
    monkeypatch.setattr(
        macfocus.subprocess,
        "run",
        lambda *a, **k: _FakeProc(1, stderr="some other error (-1728)"),
    )
    with caplog.at_level(logging.ERROR, logger="agent_phone.macfocus"):
        assert macfocus._run_osascript("whatever") is None
    assert "-1728" in caplog.text


def test_run_osascript_strips_trailing_newline(monkeypatch):
    monkeypatch.setattr(
        macfocus.subprocess,
        "run",
        lambda *a, **k: _FakeProc(0, stdout="15636\n1\ntitle\n"),
    )
    assert macfocus._run_osascript("x") == "15636\n1\ntitle"


def test_run_osascript_timeout_returns_none(monkeypatch, caplog):
    def boom(*a, **k):
        raise macfocus.subprocess.TimeoutExpired(cmd="osascript", timeout=5)

    monkeypatch.setattr(macfocus.subprocess, "run", boom)
    with caplog.at_level(logging.ERROR, logger="agent_phone.macfocus"):
        assert macfocus._run_osascript("x") is None
    assert "timed out" in caplog.text
