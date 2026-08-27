"""macOS terminal window binding + focus layer for Agent Phone.

Captures a reference to the frontmost Terminal.app / iTerm2 window (what the
user is looking at when they press # on the phone), and can later re-focus
that exact window/tab/session or check whether it still exists.

Everything talks to the apps through ``osascript``.  All AppleScript lives in
module constants; all output parsing lives in pure functions so tests never
need a GUI or Automation permission.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

OSASCRIPT_TIMEOUT = 5.0  # seconds

APP_TERMINAL = "Terminal"
APP_ITERM = "iTerm2"

# Error -1743: the calling process has not been granted Automation permission
# to control the target app.
AUTOMATION_DENIED_CODE = "-1743"
AUTOMATION_DENIED_HINT = (
    "osascript was denied Automation permission (error -1743). Grant the "
    "process running agent-phone (e.g. Terminal) access to control "
    '"System Events", "Terminal" and "iTerm2" in System Settings -> '
    "Privacy & Security -> Automation, then retry."
)

# ---------------------------------------------------------------------------
# AppleScript snippets
# ---------------------------------------------------------------------------

# Which app process is frontmost right now.
FRONTMOST_APP_SCRIPT = (
    'tell application "System Events" to '
    "get name of first application process whose frontmost is true"
)

# Frontmost Terminal.app window: id, selected tab index (1-based; Terminal
# tabs have no `index` property so we scan for the one whose `selected` is
# true), then the window name.  Verified live on this machine.
TERMINAL_FRONT_SCRIPT = """\
tell application "Terminal"
	if (count windows) is 0 then return "NONE"
	set w to front window
	set wid to id of w
	set n to count tabs of w
	set sel to 0
	repeat with i from 1 to n
		if selected of tab i of w then
			set sel to i
			exit repeat
		end if
	end repeat
	return (wid as text) & linefeed & (sel as text) & linefeed & (name of w as text)
end tell
"""

# Focus one Terminal window (and select a tab if tab_index > 0).
TERMINAL_FOCUS_SCRIPT = """\
tell application "Terminal"
	if not (exists window id {window_id}) then return "MISSING"
	set w to window id {window_id}
	if miniaturized of w then set miniaturized of w to false
	if {tab_index} > 0 and {tab_index} <= (count tabs of w) then
		set selected of tab {tab_index} of w to true
	end if
	set frontmost of w to true
	activate
	return "OK"
end tell
"""

TERMINAL_EXISTS_SCRIPT = (
    'tell application "Terminal" to (exists window id {window_id}) as text'
)

TERMINAL_MINIMIZE_SCRIPT = """\
tell application "Terminal"
	if not (exists window id {window_id}) then return "MISSING"
	set miniaturized of window id {window_id} to true
	return "OK"
end tell
"""

# iTerm2: window `id` is a stable integer, and each session has a unique id
# string (`id of current session of current window`).  Based on iTerm2's
# published AppleScript dictionary; iTerm2 is not installed on the dev
# machine, so these three scripts are untested live.
ITERM_FRONT_SCRIPT = """\
tell application "iTerm2"
	if (count windows) is 0 then return "NONE"
	set w to current window
	set wid to id of w
	set sid to id of current session of w
	return (wid as text) & linefeed & sid & linefeed & (name of w as text)
end tell
"""

# `select` on a session/window makes it visible and selected per iTerm2's
# dictionary.  If the bound session is gone but the window remains, we still
# focus the window.
ITERM_FOCUS_SCRIPT = """\
tell application "iTerm2"
	if not (exists window id {window_id}) then return "MISSING"
	set w to window id {window_id}
	if miniaturized of w then set miniaturized of w to false
	repeat with t in tabs of w
		repeat with s in sessions of t
			if id of s is {session_id} then
				select s
				select w
				activate
				return "OK"
			end if
		end repeat
	end repeat
	select w
	activate
	return "OK"
end tell
"""

ITERM_EXISTS_SCRIPT = (
    'tell application "iTerm2" to (exists window id {window_id}) as text'
)

# iTerm2 windows expose the standard `miniaturized` property in the same
# dictionary as Terminal.app; untested live (iTerm2 not installed here).
ITERM_MINIMIZE_SCRIPT = """\
tell application "iTerm2"
	if not (exists window id {window_id}) then return "MISSING"
	set miniaturized of window id {window_id} to true
	return "OK"
end tell
"""


# ---------------------------------------------------------------------------
# WindowRef
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowRef:
    """A stable handle to one terminal window (+ tab/session)."""

    app: str  # APP_TERMINAL or APP_ITERM
    window_id: int
    tab_index: int | None = None  # Terminal.app: 1-based selected tab index
    session_id: str | None = None  # iTerm2: unique session id
    label: str = ""  # human-readable window title at bind time

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "window_id": self.window_id,
            "tab_index": self.tab_index,
            "session_id": self.session_id,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WindowRef":
        return cls(
            app=str(d["app"]),
            window_id=int(d["window_id"]),
            tab_index=None if d.get("tab_index") is None else int(d["tab_index"]),
            session_id=None if d.get("session_id") is None else str(d["session_id"]),
            label=str(d.get("label", "")),
        )


# ---------------------------------------------------------------------------
# osascript runner
# ---------------------------------------------------------------------------


def _run_osascript(script: str) -> str | None:
    """Run one AppleScript via osascript.

    Returns stdout with the trailing newline stripped, or None on any
    failure (never raises).  An Automation-permission denial (-1743) gets a
    dedicated, actionable log message.
    """
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=OSASCRIPT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.error("osascript timed out after %.1fs", OSASCRIPT_TIMEOUT)
        return None
    except OSError as exc:
        log.error("could not run osascript: %s", exc)
        return None

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        if AUTOMATION_DENIED_CODE in stderr:
            log.error("%s", AUTOMATION_DENIED_HINT)
        else:
            log.error("osascript failed (rc=%d): %s", proc.returncode, stderr)
        return None

    return proc.stdout.rstrip("\n")


def _applescript_quote(value: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------
# Pure parsers (unit-tested, no GUI needed)
# ---------------------------------------------------------------------------


def _parse_terminal_front(output: str) -> WindowRef | None:
    """Parse TERMINAL_FRONT_SCRIPT output: 'id\\ntab\\nname...'."""
    if not output or output == "NONE":
        return None
    lines = output.split("\n")
    if len(lines) < 3:
        log.warning("unexpected Terminal front-window output: %r", output)
        return None
    try:
        window_id = int(lines[0])
        tab_index = int(lines[1])
    except ValueError:
        log.warning("unexpected Terminal front-window output: %r", output)
        return None
    return WindowRef(
        app=APP_TERMINAL,
        window_id=window_id,
        tab_index=tab_index if tab_index > 0 else None,
        session_id=None,
        label="\n".join(lines[2:]),
    )


def _parse_iterm_front(output: str) -> WindowRef | None:
    """Parse ITERM_FRONT_SCRIPT output: 'id\\nsession-id\\nname...'."""
    if not output or output == "NONE":
        return None
    lines = output.split("\n")
    if len(lines) < 3:
        log.warning("unexpected iTerm2 front-window output: %r", output)
        return None
    try:
        window_id = int(lines[0])
    except ValueError:
        log.warning("unexpected iTerm2 front-window output: %r", output)
        return None
    return WindowRef(
        app=APP_ITERM,
        window_id=window_id,
        tab_index=None,
        session_id=lines[1],
        label="\n".join(lines[2:]),
    )


def _normalize_app_name(process_name: str) -> str | None:
    """Map a System Events process name to a supported app, else None."""
    name = process_name.strip()
    if name == "Terminal":
        return APP_TERMINAL
    if name == "iTerm2":
        return APP_ITERM
    return None


def _parse_bool(output: str | None) -> bool:
    return output is not None and output.strip() == "true"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def frontmost_window() -> WindowRef | None:
    """Return a reference to the currently focused terminal window.

    None if the frontmost app is not Terminal.app / iTerm2, if that app has
    no windows, or if osascript fails (already logged).
    """
    process_name = _run_osascript(FRONTMOST_APP_SCRIPT)
    if process_name is None:
        return None
    app = _normalize_app_name(process_name)
    if app is None:
        log.info("frontmost app %r is not a supported terminal", process_name)
        return None

    if app == APP_TERMINAL:
        output = _run_osascript(TERMINAL_FRONT_SCRIPT)
        return None if output is None else _parse_terminal_front(output)
    output = _run_osascript(ITERM_FRONT_SCRIPT)
    return None if output is None else _parse_iterm_front(output)


def focus(ref: WindowRef) -> bool:
    """Bring exactly this window (and tab/session) to the front.

    Returns False if the window no longer exists or osascript fails.
    """
    if ref.app == APP_TERMINAL:
        script = TERMINAL_FOCUS_SCRIPT.format(
            window_id=int(ref.window_id),
            tab_index=int(ref.tab_index or 0),
        )
    elif ref.app == APP_ITERM:
        script = ITERM_FOCUS_SCRIPT.format(
            window_id=int(ref.window_id),
            session_id=_applescript_quote(ref.session_id or ""),
        )
    else:
        log.error("cannot focus unknown app %r", ref.app)
        return False

    output = _run_osascript(script)
    if output is None:
        return False
    if output == "MISSING":
        log.info("window %s:%d no longer exists", ref.app, ref.window_id)
        return False
    return output == "OK"


def minimize(ref: WindowRef) -> bool:
    """Minimize this window to the Dock.

    Returns False if the window no longer exists or osascript fails.
    """
    if ref.app == APP_TERMINAL:
        script = TERMINAL_MINIMIZE_SCRIPT.format(window_id=int(ref.window_id))
    elif ref.app == APP_ITERM:
        script = ITERM_MINIMIZE_SCRIPT.format(window_id=int(ref.window_id))
    else:
        log.error("cannot minimize unknown app %r", ref.app)
        return False
    output = _run_osascript(script)
    if output == "MISSING":
        log.info("window %s:%d no longer exists", ref.app, ref.window_id)
        return False
    return output == "OK"


def exists(ref: WindowRef) -> bool:
    """Whether the bound window is still alive (for pruning dead bindings)."""
    if ref.app == APP_TERMINAL:
        script = TERMINAL_EXISTS_SCRIPT.format(window_id=int(ref.window_id))
    elif ref.app == APP_ITERM:
        script = ITERM_EXISTS_SCRIPT.format(window_id=int(ref.window_id))
    else:
        log.error("cannot check unknown app %r", ref.app)
        return False
    return _parse_bool(_run_osascript(script))
