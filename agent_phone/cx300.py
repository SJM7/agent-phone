"""USB transport for the Polycom CX300.

Wraps the pure protocol codec (cx300_protocol) with the device lifecycle:
a reader thread turning HID input reports into callbacks, a keepalive
timer that stops the phone from falling back to its Lync sign-in screen,
and write helpers for the LED and LCD. Reconnects if the phone is
unplugged and replugged.

The `hid` module (hidapi package) is imported lazily so the rest of the
package stays importable without the native library.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from agent_phone.cx300_protocol import (VID, PID, EventDetector,
                                        build_area_select, build_display_mode,
                                        build_keepalive, build_led, build_text,
                                        parse_input_report,
                                        sanitize_display_text)

log = logging.getLogger(__name__)

KEEPALIVE_INTERVAL = 25.0
RECONNECT_INTERVAL = 3.0
BLINK_INTERVAL = 0.5    # the CX300 has no native flash pattern; we toggle


class Cx300Phone:
    def __init__(self,
                 on_key: Callable[[str], None],
                 on_offhook: Callable[[], None],
                 on_onhook: Callable[[], None],
                 on_connect: Callable[[], None] | None = None,
                 on_button: Callable[[str], None] | None = None) -> None:
        self.on_key = on_key
        self.on_offhook = on_offhook
        self.on_onhook = on_onhook
        self.on_connect = on_connect
        self.on_button = on_button      # redial | hold | delete
        self._dev = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._blinking = False
        self._blink_lit = False
        self._next_blink = 0.0

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cx300",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        with self._lock:
            if self._dev is not None:
                try:
                    self._dev.close()
                finally:
                    self._dev = None

    @property
    def connected(self) -> bool:
        return self._dev is not None

    # -- outputs ------------------------------------------------------------
    def set_led(self, attention: bool) -> None:
        """Attention blinks red; all-clear shows steady green."""
        self._blinking = attention
        self._blink_lit = attention
        self._next_blink = time.monotonic() + BLINK_INTERVAL
        self._write(build_led("red" if attention else "green"))

    def _blink_tick(self, now: float) -> None:
        if not self._blinking or now < self._next_blink:
            return
        self._blink_lit = not self._blink_lit
        self._next_blink = now + BLINK_INTERVAL
        self._write(build_led("red" if self._blink_lit else "off"))

    def show_dashboard(self, top_left: str, bottom_left: str,
                       top_right: str, bottom_right: str) -> None:
        """Four-corner attention dashboard. Left corners are 16 chars (two
        full text chunks), right corners 8 (one chunk), so every report is
        a complete fixed-size packet."""
        reports = [build_display_mode("clear"),
                   build_display_mode("corners")]
        for area, text, width in (("top_left", top_left, 16),
                                  ("bottom_left", bottom_left, 16),
                                  ("top_right", top_right, 8),
                                  ("bottom_right", bottom_right, 8)):
            reports.append(build_area_select(area))
            reports += build_text(sanitize_display_text(text, width))
        for rpt in reports:
            self._write(rpt)

    def show(self, top: str, bottom: str = "") -> None:
        # Clear first so a shorter message never leaves stale characters,
        # then repaint both lines with sanitized, width-padded text.
        reports = [build_display_mode("clear"),
                   build_display_mode("two_line"),
                   build_area_select("top_line")]
        reports += build_text(sanitize_display_text(top))
        reports.append(build_area_select("bottom_line"))
        reports += build_text(sanitize_display_text(bottom))
        for rpt in reports:
            self._write(rpt)

    def _write(self, report: bytes) -> None:
        with self._lock:
            if self._dev is None:
                return
            try:
                self._dev.write(report)
            except OSError as exc:
                log.warning("HID write failed: %s", exc)
                self._drop()

    def _drop(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except OSError:
                pass
            self._dev = None

    # -- reader thread ------------------------------------------------------
    def _run(self) -> None:
        import hid
        while not self._stop.is_set():
            try:
                dev = hid.device()
                dev.open(VID, PID)
            except (OSError, IOError):
                self._stop.wait(RECONNECT_INTERVAL)
                continue
            with self._lock:
                self._dev = dev
            log.info("CX300 connected")
            try:
                dev.send_feature_report(build_keepalive())
            except OSError:
                pass
            if self.on_connect:
                self._safely(self.on_connect)
            self._read_loop(dev)
            with self._lock:
                self._drop()
            if not self._stop.is_set():
                log.warning("CX300 disconnected; retrying")

    def _read_loop(self, dev) -> None:
        detector = EventDetector()
        last_keepalive = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            self._blink_tick(now)
            if now - last_keepalive > KEEPALIVE_INTERVAL:
                last_keepalive = now
                try:
                    with self._lock:
                        dev.send_feature_report(build_keepalive())
                except OSError:
                    return
            try:
                data = dev.read(64, timeout_ms=250)
            except OSError:
                return
            if not data:
                continue
            state = parse_input_report(bytes(data))
            if state is None:
                continue
            for event, arg in detector.feed(state):
                if event == "key" and arg is not None:
                    self._safely(self.on_key, arg)
                elif event == "offhook":
                    self._safely(self.on_offhook)
                elif event == "onhook":
                    self._safely(self.on_onhook)
                elif self.on_button is not None:
                    self._safely(self.on_button, event)

    @staticmethod
    def _safely(fn, *args) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("CX300 callback error")
