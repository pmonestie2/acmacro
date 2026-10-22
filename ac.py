"""ac.py

Controller-centric automation utilities.

All automation is exposed through `AcController(window)`; mouse positions are
translated relative to the target window so multiple controllers can operate
independently.
"""

from collections import deque
from pathlib import Path
from contextlib import contextmanager
import random
import subprocess
import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

from gui import bring_to_front_exact, find_window_exact, key_press, client_geometry, focus_window, release_input
from focus import SharedFocus, focused, process_identity
from game_instance import GameInstance
from PIL import Image
import pyautogui as pag
import win32gui

from chatproc import (
    begin_chat, chat_ready, read_chat_internal, next_reply, reply_decision, reply_sent, suspend_chat, stop_chat, configure_chat_path,
)
from chat_audit import log_interaction

from config import SERVER_IP, GAME_CLIENT, GAME_CLIENTS, net7_proxy
from objects import *
from util import log, log_every, log_event
from watchdog import GameWindowLost, GameUnresponsive, RestartException, StopMacro, request_interrupt, run_every, run_interrupts
import memory
from player import Player, PLAYER, PlayerSettings
from gate_navigation import GATE_IDS, submit as submit_gate_navigation
from protocol.game_packet_monitor import GamePacketMonitor
from job_types import JobMonitor

pag.PAUSE = 0.03

# Constants
SLOT_POS = [50, 90, 130, 170, 196]
GATE_APPROACH_TIMEOUT = 240  # Allow slow approaches beyond the previous 160-second limit.
# A gate has two sequential waits: target availability, then entry panel.
# Keep the sector watchdog beyond both budgets plus UI/transition overhead.
POS_CHANGE_TIME = 2 * GATE_APPROACH_TIMEOUT + 120
STUCK_TIMEOUT = 1500
RELOGIN_TIME = 90
MAX_RELOGINS = 3
RELOGIN_WINDOW = 10 * 60


_CHECK_PROGRESS_LOCK = threading.Lock()


class GateFocusExpired(RuntimeError):
    """End a gate focus turn normally; never request relogin."""


class AcController:
    """Controller that maps ACTool-style coordinates to a given window."""

    def __init__(self, window_name: str, settings: PlayerSettings | None = None,
                 singleton: bool = False, *, shared_install: bool = False) -> None:
        self.window_name = window_name
        self.player = PLAYER
        self.offset_x = 0
        self.offset_y = 0
        self.window = None
        self.instance = (GameInstance(settings, shared_install=True) if shared_install else GameInstance(settings)) if settings else None
        self.focus = None
        self.singleton = singleton
        self.station_lock = True
        self.gate_navigation = getattr(settings, "gate_navigation", "packet")
        self._remote_logoff = threading.Event()
        self._exclusive_deadline = None
        self._exclusive_error = GameUnresponsive
        self._exclusive_budget = 45
        self._packet_monitor = None
        self._gate_close_active = False
        self._prepared_gate = None
        self.departure_stops = set()
        self._confirmed_arrival = None
        self._map_ready_key = None
        self._station_waiting = False
        self._pending_station_position = None
        self._held_keys = set()
        self._mouse_held = False
        if self.instance:
            self.window_name = self.instance.title
            blocked, preferred = self.instance.slot_availability() if self.instance.automatic else ((), None)
            shared_slots = tuple(f"vendor-window-{index}" for index in range(4)) if shared_install else None
            self.focus = SharedFocus(self.instance.client, settings.name, on_wait=self._focus_wait, singleton=singleton,
                                     slots=shared_slots if shared_install else GAME_CLIENTS if self.instance.automatic else None,
                                     blocked_slots=blocked, preferred_slot=preferred)
            if self.instance.automatic:
                self.instance.assign_slot(self.focus.slot)
            elif shared_install:
                self.instance.slot = self.focus.slot
            self.focus.enable_trace(Path(__file__).parent / f"focus_{settings.name}.jsonl")
            if not self.instance.client.is_file():
                self.focus.close()
                raise FileNotFoundError(f"Cannot find game client: {self.instance.client}")
            configure_chat_path(self.instance.chat_log)
            log_event(f"Game slot {self.focus.slot}: {self.instance.client}; chat={self.instance.chat_log}")
        try:
            self.init_window()
        except BaseException:
            if self.focus:
                self.focus.close()
            raise
        self.schedule_event: Optional[threading.Event] = None
        self.relogin_attempts = 0
        self._last_progress_version = self.player.progress_version
        self._relogin_times = deque()
        self._logging_in = False
        self._restart_pending = False
        self._reply_pending = False
        self._chat_stop = None
        self._stopping = False
        self._progress_generation = 0
        self._terminated = False
        self._paused = False
        self._pause_total = 0
        self._pause_control = None
        if settings:
            from macro_control import PauseControl
            self._pause_control = PauseControl(settings.name)
        if self.focus:
            try:
                self.focus.start_comms(self._remote_logoff.set)
                log_event(f"Peer HTTP listening on 127.0.0.1:{self.focus.peer.port} for {settings.name}")
                self.player.on_position = self._position_changed
            except BaseException:
                self.focus.close()
                raise

    def init_window(self) -> None:
        self._check_pause()
        if self.instance:
            # Renaming/positioning must not obscure another owner's screenshot.
            self.focus.acquire()
            self.focus.depth += 1
            try:
                self.window = self.instance.discover()
            finally:
                self.focus.depth -= 1
                self.focus.boundary()
        else:
            self.window = find_window_exact(self.window_name)
        hwnd = self.window

        if hwnd:
            try:
                left, top = win32gui.ClientToScreen(hwnd, (0, 0))
                self.offset_x, self.offset_y = left, top
            except win32gui.error:
                if not win32gui.IsWindow(hwnd):
                    self.window = None
                else:
                    raise
                self.offset_x = 0
                self.offset_y = 0
        # Window identity and coordinates are refreshed without focus chatter.

    def _focus_wait(self):
        pag.failSafeCheck()
        self._check_remote_logoff()
        self._check_pause()
        if getattr(self, "_chat_stop", None) and not self._stopping:
            self._stop_for_chat()

    def send_logoff(self, toon, request_id=None):
        if not self.focus:
            raise RuntimeError("Peer messaging requires a configured game instance")
        return self.focus.send_logoff(toon, request_id)

    def polling_time(self):
        """Timeout clock including focus waits, excluding explicit user pauses."""
        return self.active_time()

    def active_time(self):
        """Wall-clock deadlines excluding time explicitly paused by the user."""
        return time.monotonic() - getattr(self, '_pause_total', 0)

    def _check_pause(self):
        control = getattr(self, '_pause_control', None)
        if control is None or getattr(self, '_paused', False):
            return
        if self.focus and self.focus.depth:
            return
        if getattr(self, '_held_keys', ()) or getattr(self, '_mouse_held', False) or not control.requested():
            return
        if self.focus:
            self.focus.cancel_wait()
        started = time.monotonic()
        self._paused = True
        self._progress_generation += 1
        self._restart_pending = False
        control.acknowledge(True)
        log_event(f"Macro PAUSED by signal for {self.player.toon_name}; input focus released")
        try:
            while control.requested():
                self._check_remote_logoff()
                if self._chat_stop and not self._stopping:
                    self._stop_for_chat()
                time.sleep(.1)
        finally:
            duration = time.monotonic() - started
            self._pause_total += duration
            self.player.set_time += duration
            self.player.relogin_time += duration
            self._paused = False
            control.acknowledge(False)
            if not self._stopping:
                log_event(f"Macro RESUMED by signal for {self.player.toon_name}; paused {duration:.1f}s")

    def _position_changed(self, position):
        self._map_ready_key = None
        if self.singleton or not self.focus:
            return
        station = "earthS" if position.startswith("earthJOB") else (
            position if position in {"earthS", "charonS", "somerS", "prasadS"} else None)
        if self.player.type_ == "new":
            # Trade-only characters never wait for or reserve any station.
            # Publish None to clear a previous claim, including at Charon.
            station = None
        if not self.station_lock:
            station = None
        if self.focus.depth:
            # Map recognition may publish arrival inside the departure lock.
            # Keep only the latest location; perform any wait after unlocking.
            self._pending_station_position = position
            return
        self._pending_station_position = None
        blocker = self.focus.station_blocker(station)
        if not blocker:
            return
        self._station_waiting = True
        started = time.monotonic()
        try:
            while blocker:
                self.focus.release()
                log_event(f"Same station {station} as {blocker}; waiting 60 seconds without focus")
                self._yield(60)
                blocker = self.focus.station_blocker(station)
            log_event(f"Station {station} is available; resuming")
        finally:
            paused = time.monotonic() - started
            self.player.set_time += paused
            self.player.relogin_time += paused
            self._station_waiting = False

    def _check_exclusive_deadline(self):
        if self._exclusive_deadline is not None and time.monotonic() >= self._exclusive_deadline:
            raise self._exclusive_error(f"Exclusive focus section exceeded its {self._exclusive_budget}-second budget")

    def exclusive_remaining(self, reserve=0, default=45):
        if self._exclusive_deadline is None:
            return default
        self._check_exclusive_deadline()
        return max(0, self._exclusive_deadline - time.monotonic() - reserve)

    @contextmanager
    def exclusive_focus(self, name, timeout=45, timeout_error=GameUnresponsive):
        """Pin the existing global input mutex; never acquire a second lock."""
        if self.singleton or not self.focus:
            yield
            return
        if self._exclusive_deadline is not None or self.focus.depth:
            raise RuntimeError("Exclusive focus sections must begin at a safe boundary")
        try:
            with self.input_action():
                # Queue waiting is outside this budget. Both games use the same
                # mutex, so only one can enter and start its timer at a time.
                started = self.focus.acquired_at if timeout_error is GateFocusExpired else time.monotonic()
                self._exclusive_deadline = started + timeout
                self._exclusive_error, self._exclusive_budget = timeout_error, timeout
                log_event(f"Exclusive focus started: {name}; budget={timeout}s")
                try:
                    yield
                    self._check_exclusive_deadline()
                finally:
                    self._exclusive_deadline = None
                    self._exclusive_error, self._exclusive_budget = GameUnresponsive, 45
        finally:
            # Always start a fresh normal focus turn, including on timeout/error.
            self.focus.release()
            log_event(f"Exclusive focus released: {name}")

    def _check_remote_logoff(self):
        if not self._remote_logoff.is_set() or self._stopping:
            return
        if self.focus and self.focus.depth:
            return
        self._stopping = True
        self.terminate()
        stop_chat()
        log_event(f"HTTP logoff requested for {self.player.toon_name}")
        # Stop only this instance. No focus or input is needed while a peer owns it.
        if self.instance:
            self.instance.stop()
        self.window = None
        raise StopMacro("Peer requested logoff; owned game and macro stopped")

    @property
    def startup_popup(self):
        return bool(self.instance and self.instance.popup)

    def complete_startup(self):
        """Advance intro/agreement screens, preserving the original login timing."""
        deadline = self.active_time() + 60
        attempts = {}
        spaces = 0
        while self.active_time() < deadline:
            self.init_window()
            if self.window:
                # A main HWND can exist during the intro, before login is ready.
                # The working login sequence sent Space, waited ten seconds,
                # then sent Space again. Do not skip it based on window class.
                if spaces >= 2 and not self.startup_popup:
                    return
                hwnd = self.window
                if self.startup_popup:
                    attempts[hwnd] = attempts.get(hwnd, 0) + 1
                    if attempts[hwnd] > 3:
                        raise GameUnresponsive("EBPopup did not close after three Space attempts")
                self.key_press("space", 0.2)
                spaces += 1
                self._yield(10 if spaces == 1 else 1)
            else:
                if self.instance.pid and process_identity(self.instance.pid) != self.instance.born:
                    raise GameWindowLost("Game client exited during startup")
                self._yield(0.5)
        raise GameUnresponsive("Main game window did not appear within 60 seconds")

    @contextmanager
    def with_focus(self, name):
        """Group a short sequence; normal 20s safe handoffs still apply.

        Long state waits belong outside this context. It is not the 45s
        exclusive deployment lock: completed input can pause at the budget.
        """
        focus = self.focus
        previous = getattr(focus, "section", None) if focus else None
        if focus:
            focus.section = name
        try:
            with self.input_action():
                if focus:
                    focus.record("section_started", name=name)
                started = self.polling_time()
                try:
                    yield
                finally:
                    if focus:
                        focus.record("section_finished", name=name,
                                     seconds=round(self.polling_time() - started, 3))
        finally:
            if focus:
                focus.section = previous

    def focus_owned_window(self):
        # A transient foreground refusal must not abort an otherwise healthy
        # macro. Keep the input mutex and retry once; send no input on failure.
        if focus_window(self.window):
            return True
        log_event(f'Foreground acquisition refused for {self.window_name}; retrying once')
        time.sleep(.2)
        self._check_exclusive_deadline()
        return focus_window(self.window)

    @contextmanager
    def input_action(self):
        self._check_pause()
        self._check_exclusive_deadline()
        focus = getattr(self, "focus", None)
        if not focus:
            yield
            return
        outer = focus.depth == 0
        if outer:
            self._check_remote_logoff()
            # Leave room for an atomic row/count/input operation before 20s.
            if not focus.singleton and focus.owned and time.monotonic() - focus.acquired_at >= 10:
                focus.release(reason="reserve time for next action")
            focus.acquire()
            focus.last_wait = 0
            try:
                self.require_window()
                if win32gui.GetForegroundWindow() != self.window:
                    if not self.focus_owned_window():
                        focus.record("foreground_failed", hwnd=self.window)
                        raise RuntimeError(f"Cannot focus {self.window_name}; no input was sent")
                    focus.record("foreground_acquired", hwnd=self.window)
            except BaseException:
                focus.release()
                raise
        elif win32gui.GetForegroundWindow() != self.window:
            self.require_window()
            if not focus_window(self.window):
                focus.record("foreground_failed", hwnd=self.window, reason="lost during action")
                raise RuntimeError(f"Lost focus for {self.window_name}")
            focus.record("foreground_acquired", hwnd=self.window, reason="restored during action")
        focus.depth += 1
        try:
            yield
        except BaseException:
            if outer:
                self.release_inputs()
            raise
        finally:
            focus.depth -= 1
            if outer:
                focus.boundary()
        if outer:
            self._check_remote_logoff()
            self._check_pause()
            if self._pending_station_position is not None:
                self._position_changed(self._pending_station_position)
            run_interrupts()

    def release_inputs(self):
        # Release before lending focus, even when an action raises an exception.
        for key in list(self._held_keys):
            release_input(pag.keyUp, key, _pause=False)
            self._held_keys.discard(key)
            if self.focus:
                self.focus.depth -= 1
        if self._mouse_held:
            release_input(pag.mouseUp, _pause=False)
            self._mouse_held = False
            if self.focus:
                self.focus.depth -= 1

    def close(self):
        if getattr(self, '_pause_control', None):
            self._pause_control.close()
        if self._packet_monitor:
            self._packet_monitor.close()
        if self.player.on_position == self._position_changed:
            self.player.on_position = None
        if self.focus:
            try:
                self.release_inputs()
            finally:
                self.focus.close()

    @focused
    def key_press(self, key, delay=0.03):
        if key == 'm':
            self._map_ready_key = None
        # A grouped walking segment may begin late in the current turn. Lend
        # focus before a held key would cross the limit, never midway through it.
        if (self.focus and not self.focus.singleton and self._exclusive_deadline is None
                and self.focus.owned and not self._held_keys and not self._mouse_held
                and time.monotonic() - self.focus.acquired_at + delay + 0.5 >= self.focus.max_hold):
            self._renew_focus("reserve time for held key")
        key_press(key) if delay == 0.03 else key_press(key, delay)

    def _renew_focus(self, reason):
        """Lend at a completed-input boundary, then resume the nested group."""
        saved_depth = self.focus.depth
        self.focus.depth = 0
        try:
            self.focus.release(reason=reason)
            self._check_remote_logoff()
            self._check_pause()
            self.focus.acquire()
            self.focus.last_wait = 0
            self.require_window()
            if win32gui.GetForegroundWindow() != self.window:
                if not focus_window(self.window):
                    self.focus.record("foreground_failed", reason=reason, hwnd=self.window)
                    raise RuntimeError("Cannot restore focus after timed handoff")
                self.focus.record("foreground_acquired", reason=reason, hwnd=self.window)
        finally:
            self.focus.depth = saved_depth

    # --- coordinate translation ---
    def require_window(self) -> None:
        if self.instance and self.window and not self.instance.valid():
            self.window = None
        if not self.window or not win32gui.IsWindow(self.window):
            self.init_window()
        if not self.window:
            raise GameWindowLost(f"{self.window_name} window disappeared.")

    def refresh_offset(self) -> None:
        self.require_window()
        try:
            self.client_size, self.screen_size, origin = client_geometry(self.window)
        except (win32gui.error, RuntimeError):
            # It can close between the IsWindow check and GetClientRect/ClientToScreen.
            if not win32gui.IsWindow(self.window):
                self.window = None
                raise GameWindowLost(f"{self.window_name} window disappeared while reading its geometry.") from None
            raise
        self.offset_x, self.offset_y = origin

    def translate(self, x: int, y: int) -> Tuple[int, int]:
        self.refresh_offset()
        sx = self.screen_size[0] / self.client_size[0]
        sy = self.screen_size[1] / self.client_size[1]
        return round(x * sx) + self.offset_x, round(y * sy) + self.offset_y

    def test_obj(self, obj:ActoolObject, screenshot=None, **kwargs) -> bool:
        """Test against a screenshot in game coordinates, excluding window borders."""
        screenshot = self.screenshot() if screenshot is None else screenshot
        return obj.test(screenshot=screenshot, **kwargs)

    # --- pyautogui wrappers ---

    @focused
    def moveTo(self, x: int, y: int, duration: Optional[float] = None) -> None:
        tx, ty = self.translate(x, y)
        pag.moveTo(tx, ty, duration=0 if duration is None else duration)

    def mouseDown(self) -> None:
        if self._mouse_held:
            return
        if self.focus:
            self.focus.acquire()
            if not focus_window(self.window):
                self.focus.release()
                raise RuntimeError("Cannot focus game for mouse input")
            self.focus.depth += 1
        try:
            pag.mouseDown()
            self._mouse_held = True
        except BaseException:
            if self.focus:
                self.focus.depth -= 1
                self.focus.boundary()
            raise

    def mouseUp(self) -> None:
        if not self._mouse_held:
            return
        try:
            release_input(pag.mouseUp)
        finally:
            if self._mouse_held and self.focus:
                self.focus.depth -= 1
            self._mouse_held = False
            if self.focus:
                self.focus.boundary()

    @focused
    def click(self) -> None:
        pag.click()

    @focused
    def screenshot(self):
        self.refresh_offset()
        image = pag.screenshot(region=(self.offset_x, self.offset_y, *self.screen_size))
        if image.size != self.client_size:
            image = image.resize(self.client_size, Image.Resampling.NEAREST)
        return image

    @focused
    def typewrite(self, text: str, interval: float = 0.02) -> None:
        if not self.focus or self.singleton:
            pag.typewrite(text, interval=interval)
            return
        for index in range(0, len(text), 32):
            pag.typewrite(text[index:index + 32], interval=interval)
            if self.focus.expired():
                self._yield(0.001)

    def keyDown(self, key: str) -> None:
        if key in self._held_keys:
            return
        if self.focus:
            self.focus.acquire()
            if not focus_window(self.window):
                self.focus.release()
                raise RuntimeError("Cannot focus game for keyboard input")
            self.focus.depth += 1
        try:
            pag.keyDown(key)
            self._held_keys.add(key)
        except BaseException:
            if self.focus:
                self.focus.depth -= 1
                self.focus.boundary()
            raise

    def keyUp(self, key: str) -> None:
        if key not in self._held_keys:
            return
        try:
            release_input(pag.keyUp, key)
        finally:
            if key in self._held_keys and self.focus:
                self.focus.depth -= 1
            self._held_keys.discard(key)
            if self.focus:
                self.focus.boundary()

    # --- helpers ---
    def _yield(self, seconds: float, incr: float = 0.3) -> None:
        protected = bool(self.focus and self.focus.depth)
        if self.focus and not protected and seconds > 0 and not self.focus.singleton:
            if seconds >= 0.5:
                self.focus.release(reason=f"passive wait {seconds:g}s")
            else:
                self.focus.boundary()
        if seconds > 0.0:
            target = self.active_time() + seconds
            while True:
                remaining = target - self.active_time()
                if remaining <= 0:
                    break
                time.sleep(min(incr, remaining))
                self._check_exclusive_deadline()
                if not protected:
                    self._check_remote_logoff()
                    self._check_pause()
                elif self._exclusive_deadline is None and self.focus.expired() and not self._held_keys and not self._mouse_held:
                    # Completed input can pause even inside a longer procedure.
                    self._renew_focus("20s budget during protected pause")
                # A STOP can arrive while a login interrupt is already running.
                # Check it directly so it need not wait for that interrupt to finish.
                if not protected and self._chat_stop and not self._stopping:
                    self._stop_for_chat()
                if not self._logging_in and not self._terminated:
                    self.require_window()
                if not protected:
                    run_interrupts()
                    if self.focus:
                        self.focus.boundary()

    @focused
    def press(self, key: str) -> None:
        if key == 'm':
            self._map_ready_key = None
        pag.press(key)
        self._yield(0.1)

    @focused
    def say(self, text: str) -> None:
        self.typewrite(text, interval=0.02)
        pag.press("enter")

    @focused
    def lclick(self, x: int, y: int, click: int = 1, shift: bool = False, yield_time: float = 0) -> None:
        self.moveTo(x, y)
        if shift:
            pag.keyDown("shift")
        try:
            if click != 0:
                pag.click()
        finally:
            if shift:
                release_input(pag.keyUp, "shift")
        self._yield(yield_time)

    # --- game helpers ---
    @focused
    def sbreset(self, value: int) -> None:
        if not self.bring_to_front():
            self.require_window()
            raise RuntimeError("Cannot focus the game for starbase reset.")
        memory.setmemory_u32_window(self.window, 0x00BDBB80, 0)
        memory.setmemory_u32_window(self.window, 0x00BDB1C0, value)
        self.say("/starbase reset")
        self._yield(1)

    @focused
    def close_job(self) -> None:
        log("close1job: confirm #1")
        self.lclick(598, 269)
        self._yield(0.5)
        log("close1job: confirm #2")
        self.lclick(543, 298)
        self._yield(0.5)

    def clear_jobs(self) -> None:
        self.key_press("m")
        self._yield(0.3)
        self.key_press("u")
        self._yield(0.3)
        for _ in range(6):
            self.lclick(110, 280)
            self.close_job()
            self.lclick(110, 380)
            self.close_job()
            self.lclick(323, 587, yield_time=0.3)

    def count_mission(self) -> int:
        self.start_packet_capture()
        monitor = self._packet_monitor
        snapshot = monitor.mission_snapshot() if monitor else None
        if snapshot is not None:
            names = '; '.join(f"{item['slot'] + 1}:{item['name']}" for item in snapshot['missions'])
            log_event(f"Mission count={snapshot['count']} source=packet 0x001b; "
                      f"occupied slots (includes promotion/story missions); missions=[{names}]")
            return snapshot['count']
        reason = monitor.mission_unavailable_reason() if monitor else 'owned proxy capture not available'
        log_event(f"Mission count: packet snapshot unavailable: {reason}; using screen estimate")
        return self._count_mission_ui()

    @focused
    def _count_mission_ui(self) -> int:
        ret = 0
        self.press("m")
        self.press("u")
        for y in (286, 394, 515):
            self.lclick(100, y)
            time.sleep(1.0)
            if self.test_obj(FORFEIT_OBJ):
                ret += 1
        self.lclick(100, 286)
        time.sleep(1.0)
        for _ in range(5):
            self.lclick(321, 584)
            time.sleep(0.1)
        jobs_below_3 = 0
        screenshot = self.screenshot()
        if self.test_obj(MISSION_4_OBJ, screenshot=screenshot):
            jobs_below_3 = max(jobs_below_3, 4)
        if self.test_obj(MISSION_5_OBJ, screenshot=screenshot):
            jobs_below_3 = max(jobs_below_3, 5)
        if self.test_obj(MISSION_6_OBJ, screenshot=screenshot):
            jobs_below_3 = max(jobs_below_3, 6)
        if self.test_obj(MISSION_7_OBJ, screenshot=screenshot):
            jobs_below_3 = max(jobs_below_3, 7)
        if self.test_obj(MISSION_8_OBJ, screenshot=screenshot):
            jobs_below_3 = max(jobs_below_3, 8)
        max_job = max(ret, jobs_below_3)
        self.press("m")
        log_event(f"Mission count={max_job} source=screen estimate; "
                  f"forfeit buttons in first three rows={ret}; highest recognized mission index={jobs_below_3}")
        return max_job

    @focused
    def empty_one(self, x: int, y: int) -> None:
        self.moveTo(x, y)
        self._yield(0.1)
        self.mouseDown()
        self._yield(0.2)
        self.moveTo(99, 128)
        self._yield(0.2)
        self.mouseUp()
        self._yield(0.1)
        self.lclick(240, 165)
        self._yield(0.2)

    def empty_inventory(self) -> None:
        with self.with_focus("inventory setup"):
            self.press("m")
            self.press("i")
            for _ in range(10):
                self.lclick(37, 553, yield_time=0.1)
        slots = [
            (69, 324), (132, 324), (221, 324),
            (69, 388), (132, 388), (221, 388),
            (69, 450), (132, 450), (221, 450),
            (69, 509), (132, 509), (221, 509),
        ]
        # Four completed drags per turn; never hand off with the mouse down.
        for start in range(0, len(slots), 4):
            with self.with_focus("inventory cleanup"):
                for x, y in slots[start:start + 4]:
                    self.empty_one(x, y)

    @focused
    def get_map(self) -> None:
        key = self.map_key()
        if self._map_ready_key == key:
            obj = self.position_objects().get(self.player.current_position)
            if obj:
                frame = self.screenshot()
                if not self.test_obj(LARGE_MAP_OBJ, screenshot=frame) and self.test_obj(obj, screenshot=frame):
                    return
        self.press("u")
        self._yield(0.1)
        self.press("m")
        self._yield(0.3)
        # Do not continue with small-map coordinates until the large-map
        # control disappears. Clear the cursor so it cannot mask that control.
        for attempt in range(4):
            self.moveTo(780, 180)
            if not self.test_obj(LARGE_MAP_OBJ, screenshot=self.screenshot()):
                break
            if attempt == 3:
                raise GameUnresponsive("Map remained large after three toggle attempts")
            if attempt:
                log(f"Map still large; retrying toggle ({attempt + 1}/3)")
            self.lclick(711, 187)
            self._yield(0.4)
        self.key_press("-", 3.0)
        for sx, sy, ex, ey in [
            (396, 397, 396, 600),
            (396, 397, 396, 600),
            (90, 416, 400, 416),
            (90, 416, 400, 416),
        ]:
            tsx, tsy = self.translate(sx, sy)
            tex, tey = self.translate(ex, ey)
            pag.moveTo(tsx, tsy)
            self.mouseDown()
            pag.moveTo(tex, tey, duration=0.1)
            self.mouseUp()
        self._yield(0.2)
        self._map_ready_key = key

    def map_key(self):
        instance = self.instance
        return (getattr(instance, 'proxy_pid', None), getattr(instance, 'proxy_born', None),
                self.player.current_position)

    @staticmethod
    def position_objects():
        return {'charonS': CHARON_S_OBJ, 'earthS': EARTH_S_OBJ, 'jupiter': JUPITER_OBJ,
                'saturn': SATURN_OBJ, 'akeron': AKERON_OBJ, 'aba': ABA_OBJ,
                'uranus': URANUS_OBJ, 'earth': EARTH_OBJ, 'pluto': PLUTO_OBJ,
                'newed': NEWED_OBJ, 'somerS': SOMERS_OBJ, 'prasadS': PRASAD_S_OBJ,
                'xipe': XIPE_OBJ, 'swoop': SWOOP_OBJ, 'glenn': GLENN_OBJ, 'carp': CARP_OBJ}

    @property
    def job_monitor(self) -> JobMonitor | None:
        return self._packet_monitor

    def start_packet_capture(self) -> None:
        if (self.instance and isinstance(self.instance.proxy_pid, int)
                and self._packet_monitor is None):
            self._packet_monitor = GamePacketMonitor(self.instance, log_event)
            log_event('Passive game packet capture started: positions, missions and jobs')

    @contextmanager
    def gate_departure_focus(self):
        if self.singleton or not self.focus or self.focus.depth or self._exclusive_deadline is not None:
            yield
            return
        self.focus.release(reason='start gate departure')
        try:
            with self.exclusive_focus('Gate exit through warp engagement', timeout=20, timeout_error=GateFocusExpired):
                yield
        except GateFocusExpired:
            log_event('Gate departure focus budget ended; continuing route without relogin')

    def wait_warp_engaged(self, sector, target_id, injected):
        if self._packet_monitor is None:
            return
        sent = time.time()
        duration = min(18, self.exclusive_remaining(reserve=1, default=6))
        deadline = self.active_time() + duration
        while self.active_time() < deadline:
            current = self._packet_monitor.position(sector)
            if current and current['moved'] >= sent and current['speed'] >= 500:
                log_event(f'Warp motion confirmed: {sector}; focus may yield')
                return
            self._yield(.2)
        log_event(f'Warp motion not yet confirmed: {sector}; continuing normal gate wait')

    def prepare_gate(self, current, target, x, y, on_enter=None):
        """Normalize/select once and keep focus through submission and motion."""
        target_id = GATE_IDS.get((current, target))
        state = dict(key=(current, target, self.map_key()[:2]), active=False,
                     injected=False, selected=False, entered=False, map_selected=False)
        self._prepared_gate = state
        with self.gate_departure_focus():
            self.press('`')
            self.get_map()
            self.lclick(x, y)
            state['map_selected'] = True
            self._yield(.3)
            frame = self.screenshot()
            gate_seen = self.test_obj(GATE_OBJ, screenshot=frame)
            station_seen = self.test_obj(STATION_OBJ, screenshot=frame)
            state['active'] = gate_seen or station_seen
            if state['active']:
                if on_enter:
                    on_enter()
                    state['entered'] = True
                return state
            if self.gate_navigation == 'packet' and target_id is not None:
                report = submit_gate_navigation(self.instance, target_id)
                state['injected'] = report['state'] == 'handler_returned'
                log_event(f"Gate packet {current}->{target} id={target_id:#x}: {report['state']}"
                          + (f"; {report['error']}" if 'error' in report else ''))
            elif self.gate_navigation == 'packet':
                log_event(f'Gate {current}->{target}: no recorded ID; using map clicks')
            if not self.wait_selected_gate(state, x, y, on_enter=on_enter,
                    fast=not self.singleton):
                return state
            if state['active']:
                return state
            self.wait_warp_engaged(current, target_id, state['injected'])
        return state

    def selection_repair(self, x, y):
        """Reselect a lost target without resending a route or toggling warp."""
        next_check = [self.polling_time() + 16]
        def repair():
            now = self.polling_time()
            if now < next_check[0]:
                return
            next_check[0] = now + 16
            frame = self.screenshot()
            if any(self.test_obj(obj, screenshot=frame)
                   for obj in (GATE_OBJ, STATION_OBJ, IGATE_OBJ, ISTATION_OBJ)):
                return
            log_event('Gate target panel lost; reselecting the same map target without another warp command')
            self.get_map()
            self.lclick(x, y)
        return repair

    def wait_selected_gate(self, state, x, y, on_enter=None, fast=False):
        # An inactive panel can become active before our next screenshot (or
        # while focus is lent). Both states are valid selection evidence.
        def selected():
            frame = self.screenshot()
            state['active'] = (self.test_obj(GATE_OBJ, screenshot=frame) or
                               self.test_obj(STATION_OBJ, screenshot=frame))
            state['selected'] = True
            if state['active']:
                if on_enter:
                    on_enter()
                    state['entered'] = True
            elif not state['injected']:
                self.press('q')
        return self.wait_gate_objects([GATE_OBJ, STATION_OBJ, IGATE_OBJ, ISTATION_OBJ],
            current=3 if self.singleton else .3, fast=fast, timeout=GATE_APPROACH_TIMEOUT,
            on_found=selected, yield_fn=self.selection_repair(x, y))

    def prepare_departure(self, current):
        if current in getattr(self, "departure_stops", ()):
            return
        path = self.shortest_path(current, self.player.current_dest)
        if not path or len(path) < 2 or current.endswith('S'):
            return
        target = path[1]
        self.prepare_gate(current, target, *GATES[current][target])

    @focused
    def close_map(self) -> None:
        self.key_press("i")
        self._yield(0.1)
        self.key_press("m")
        self._yield(0.1)
        self.key_press("m")
        self._yield(0.1)

    def gate_poll_interval(self, current=2, fast=False):
        return current if self.singleton or fast else 8

    def wait_gate_objects(self, objs, current=2, fast=False, **kwargs):
        target_id = kwargs.pop('gate_target', None)
        if (target_id is not None and not self.singleton and not fast and self.focus and
                self.instance and isinstance(self.instance.proxy_pid, int)):
            if self._packet_monitor is None:
                self._packet_monitor = GamePacketMonitor(self.instance, log_event)
                log_event('Passive gate distance capture started; close approach threshold=10km')
            return self.wait_gate_entry(objs, target_id, self.gate_poll_interval(current, fast), **kwargs)
        return self.wait_objects(objs, yield_time=self.gate_poll_interval(current, fast),
                                 final_check=not self.singleton and not fast, **kwargs)

    def wait_gate_entry(self, objs, target_id, interval, timeout=160, on_found=None, yield_fn=None):
        """Pin focus only for the last 10 km; timeout lends focus, never relogs.

        Position-only capture belongs to this client. Far/unknown distance keeps
        normal screenshot frequency. Close turns poll once per second and end
        immediately after the existing entry callback clicks the gate.
        """
        started, next_screen, cooldown = self.polling_time(), 0, 0
        while self.polling_time() - started < timeout:
            now = self.polling_time()
            distance = self._packet_monitor.distance(self.player.current_position, target_id)
            if distance is not None and distance <= 10000 and time.monotonic() >= cooldown:
                # Start a fresh mutex turn so pre-existing ownership cannot add
                # time to the 20-second capture budget.
                self.focus.release(reason='start close gate approach')
                log_event(f'Gate approach {target_id:#x}: {distance/1000:.1f}km; exclusive focus up to 20s or gate click')
                self._gate_close_active = True
                try:
                    with self.exclusive_focus('Gate within 10km', timeout=20, timeout_error=GateFocusExpired):
                        if self.wait_objects(objs, yield_time=1,
                                             timeout=min(18, timeout - (self.polling_time() - started)),
                                             final_check=True, on_found=on_found, yield_fn=yield_fn):
                            return True
                except GateFocusExpired:
                    log_event('Gate approach focus budget ended; continuing normal gate wait')
                finally:
                    self._gate_close_active = False
                # Offer the peer a turn before another bounded approach lock.
                self._yield(.5)
                cooldown = time.monotonic() + 1
                next_screen = self.polling_time() + interval
            elif now >= next_screen:
                if self.wait_objects(objs, timeout=0, final_check=True, on_found=on_found):
                    return True
                next_screen = self.polling_time() + interval
                if yield_fn:
                    with self.with_focus("gate target verification"):
                        yield_fn()
            self.focus.release(reason='passive gate distance wait')
            self._yield(min(.25, max(0, timeout - (self.polling_time() - started))))
        return False

    def wait_objects(self, objs: List[object], yield_time: float = 2.0, timeout: float = 160.0, yield_fn: Optional[Callable[[], None]] = None, final_check=False, on_found=None) -> bool:
        start = self.polling_time()
        max_x = 0
        max_y = 0
        for o in objs:
            try:
                ax, ay = o.anchor
            except Exception:
                ax, ay = 0, 0
            max_x = max(max_x, ax)
            max_y = max(max_y, ay)
        first = True
        while True:
            if self.polling_time() - start >= timeout and not final_check:
                return False
            # Cursor clearance, screenshot and any immediate response belong
            # to one turn. The actual polling sleep always stays outside it.
            with self.with_focus("state check"):
                if first:
                    self.moveTo(max_x + 30, max_y + 30)
                    first = False
                sc = self.screenshot()
                for o in objs:
                    if self.test_obj(o, screenshot=sc):
                        log(f"object {getattr(o,'name',str(o))} found")
                        if on_found:
                            on_found()
                        return True
                if self.polling_time() - start >= timeout:
                    return False
                if yield_fn:
                    yield_fn()
            remaining = max(0, timeout - (self.polling_time() - start))
            self._yield(min(yield_time, remaining) if final_check else yield_time)

    def transition_space(self, expected=None) -> bool:
        # Inventory icons differ between character classes, and the old chat
        # pixel pattern depends on chat contents. Neither proves arrival.
        # Recognize the destination map instead; a loading screen can be missed.
        log(f"transition_space; expected={expected}")
        self.start_packet_capture()
        previous = self.player.current_position
        self._confirmed_arrival = None
        deadline = self.polling_time() + 60
        wall_deadline = self.active_time() + 120
        while True:
            position = None
            with self.gate_departure_focus():
                monitor = self._packet_monitor
                if expected and monitor and monitor.arrived(expected):
                    position = expected
                    self.player.set_pos(position)
                else:
                    position = self.get_current_position()
                if position and (position == expected if expected else position != previous):
                    self._confirmed_arrival = position
                    log_event(f"Transition confirmed: {previous}->{position}")
                    if previous and (previous.endswith('S') or previous.startswith('earthJOB')):
                        # Undocking starts the next hop here, before Pluto's
                        # route loop resumes. Equip/activate first; the loop's
                        # next setup call must not swap equipment during warp.
                        self._skip_next_travel_setup = False
                        self.setup_kranken()
                        self._skip_next_travel_setup = True
                    self.prepare_departure(position)
            if self._confirmed_arrival == position and position:
                return True
            if self.polling_time() >= deadline or self.active_time() >= wall_deadline:
                log_event(f"Transition not confirmed: expected={expected}; last={position}")
                return False
            self._yield(3 if self.singleton else 8)

    def gate_internal(self, x: int, y: int, no_gate: bool = False) -> bool:
        log(f"gate::{x} -- {y}")
        injected = False
        current = self.player.current_position
        target = next((name for name, point in GATES.get(current, {}).items() if point == (x, y)), None)
        target_id = GATE_IDS.get((current, target))
        def enter():
            self._yield(0.1 if self._gate_close_active else 0.3)
            if not no_gate:
                self.lclick(1236, 515)

        if no_gate:
            # Preserve satellite/selection-only callers: no injected route.
            with self.with_focus("gate map selection"):
                self.press("`")
                self.get_map()
                self.lclick(x, y)
                frame = self.screenshot()
                gate_seen = self.test_obj(GATE_OBJ, screenshot=frame)
                station_seen = self.test_obj(STATION_OBJ, screenshot=frame)
                active = gate_seen or station_seen
            if not active:
                if not self.wait_gate_objects([IGATE_OBJ, ISTATION_OBJ], current=3,
                        timeout=GATE_APPROACH_TIMEOUT, on_found=lambda: self.press('q')):
                    return False
                return self.wait_gate_objects([GATE_OBJ, STATION_OBJ], current=3,
                        timeout=GATE_APPROACH_TIMEOUT, on_found=enter)
            return True

        self.start_packet_capture()
        state = self._prepared_gate
        key = (current, target, self.map_key()[:2])
        if not state or state['key'] != key or not state['map_selected']:
            state = self.prepare_gate(current, target, x, y, on_enter=enter)
        # Consume only this hop; transition_space may prepare the next one.
        self._prepared_gate = None
        if not state['map_selected']:
            return False
        if state['active']:
            if not state['entered']:
                with self.with_focus("gate entry"):
                    enter()
        else:
            if not state['selected']:
                if not self.wait_selected_gate(state, x, y, on_enter=enter):
                    return False
            log("waiting for station")
            if not state['entered'] and not self.wait_gate_objects([GATE_OBJ, STATION_OBJ], current=3,
                    timeout=GATE_APPROACH_TIMEOUT, on_found=enter, gate_target=target_id,
                    yield_fn=self.selection_repair(x, y)):
                return False
        if not no_gate:
            if not self.transition_space(expected=target):
                raise GameUnresponsive("Gate entry did not complete its sector transition")
        return True

    def gate_sat(self, x: int, y: int, fast=False, approach_timeout=30, strict=False, skip_completed=False) -> None:
        log(f"gate::{x} -- {y}")
        with self.with_focus("satellite map selection"):
            self.get_map()
            self.lclick(x, y)
        self._yield(3)
        if not self.test_obj(SAT_OBJ):
            log("waiting for isat obj")
            if not self.wait_gate_objects([SAT_OBJ, ISAT_OBJ], timeout=12, fast=fast):
                if skip_completed:
                    log_event(f"Job delivery at ({x}, {y}): no control; job completed or absent, continuing")
                    return
                if strict:
                    raise GameUnresponsive("Job delivery target control did not appear")
                return
            if not self.test_obj(SAT_OBJ):
                self.key_press("q")
                self._yield(3)
        log("waiting for sat")
        ready = self.wait_gate_objects([SAT_OBJ], timeout=approach_timeout,
            yield_fn=lambda: self.key_press("a", 1), fast=fast)
        if strict and not ready:
            raise GameUnresponsive("Job delivery control did not become active; cargo retained")
        self._yield(1)
        self.key_press("a", 2.0)
        for _ in range(7):
            self.lclick(1238, 516)
            self._yield(1)

    def gate(self, x: int, y: int, no_gate: bool = False) -> bool:
        while True:
            log(f"trying gate {x} {y}")
            ok = self.gate_internal(x, y, no_gate=no_gate)
            if ok:
                log(f"gate is done {x} {y}")
                return True
            log(f"gate failed doing again {x} {y}")

    def shortest_path(self, start, end):
        q = deque([[start]])
        seen = {start}
        while q:
            path = q.popleft()
            node = path[-1]
            if node == end:
                return path
            for nxt in GATES.get(node, {}):
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(path + [nxt])
        return None

    def travel_one(self, current: str, target: str) -> None:
        log(f"travel from {current} to {target}")
        if current not in GATES:
            raise ValueError(f"No exits known for sector {current}")
        if target not in GATES[current]:
            raise ValueError(f"No gate from {current} → {target}")
        entry = GATES[current][target]
        x, y = entry
        self._confirmed_arrival = None
        self.gate(x, y)
        # Navigation/entry success is not evidence of the destination sector.
        # IDs are reused across sectors: never submit the next route based on
        # an assumed arrival, even when the transition animation was observed.
        actual = self._confirmed_arrival or self.get_current_position()
        if actual != target:
            raise GameUnresponsive(f"Gate {current}->{target} arrived in {actual or 'an unrecognized sector'}")

    def travel_all(self, src: str, dest: str) -> None:
        log(f"travel_all from {src} to {dest}")
        path = self.shortest_path(src, dest)
        log(f"travel path: {path}")
        self.player.current_position = src
        self.player.current_dest = dest
        for p in path[1:]:
            self.travel_one(self.player.current_position, p)

    def sell_all(self) -> None:
        LEFT_ARR = (37, 558)
        RIGHT_ARR = (237, 556)
        INVENTORY_SLOTS = [
            (62, 327), (138, 327), (214, 327),
            (62, 390), (138, 390), (214, 390),
            (62, 453), (138, 453), (214, 453),
            (62, 515), (138, 515), (214, 515),
        ]
        # Keep one complete inventory pass together. Per-click handoffs
        # make the vendor UI lose input and multiply activation delays.
        with self.input_action():
            for _ in range(9):
                self.lclick(*LEFT_ARR)
            for _ in range(5):
                for pos in INVENTORY_SLOTS:
                    self.lclick(*pos, shift=True)
                for _ in range(3):
                    self.lclick(*RIGHT_ARR)
                # Safe page boundary for the normal 20s budget if needed.
                self._yield(0.001)

    def repeat_vendor_click(self, x, y, count, yield_time):
        """Keep a short purchase/navigation burst in one normal focus turn."""
        with self.input_action():
            for _ in range(count):
                self.lclick(x, y, yield_time=yield_time)

    def wait_trade(self) -> None:
        opened = self.wait_objects([TRADE_OPEN_OBJ], yield_time=1.5, timeout=10.0,
                                  on_found=lambda: self.lclick(507, 663))
        if not opened:
            # Preserve the existing recovery click when recognition times out.
            self.lclick(507, 663)
        self.wait_objects([TRADE2_OPEN_OBJ], yield_time=1.5, timeout=10.0)
        log("trade2 is open")

    def dismiss_trade(self) -> None:
        with self.with_focus("vendor dismissal"):
            self.lclick(1255, 253)
            self._yield(0.5)
            self.lclick(392, 626)
        self.wait_objects([TRADE_CLOSE_OBJ], yield_time=1.5, timeout=30.0)
        self._yield(1)

    def get_current_position(self) -> Optional[str]:
        self.get_map()
        checks = [
            (CHARON_S_OBJ, "charonS"),
            (CHARON_S1_OBJ, "charonS"),
            (EARTH_S_OBJ, "earthS"),
            (JUPITER_OBJ, "jupiter"),
            (SATURN_OBJ, "saturn"),
            (AKERON_OBJ, "akeron"),
            (ABA_OBJ, "aba"),
            (URANUS_OBJ, "uranus"),
            (EARTH_OBJ, "earth"),
            (PLUTO_OBJ, "pluto"),
            (NEWED_OBJ, "newed"),
            (SOMERS_OBJ, "somerS"),
            (PRASAD_S_OBJ, "prasadS"),
            (XIPE_OBJ, "xipe"),
            (SWOOP_OBJ, "swoop"),
            (GLENN_OBJ, "glenn"),
            (CARP_OBJ, "carp"),
        ]
        for _ in range(3):
            sc = self.screenshot()
            for obj, name in checks:
                if self.test_obj(obj, screenshot=sc):
                    log(f"found position obj: {name}")
                    self.player.set_pos(name)
                    self._map_ready_key = self.map_key()
                    return name
            self._yield(1.5)
        log("no position obj found")
        return None

    # --- sector-specific procedures ---
    @focused
    def charon1_2(self):
        self.sbreset(805890)
        self.key_press("a", 4.0)

    @focused
    def charon2_3(self):
        self.sbreset(329497)
        self.key_press("a", 4.0)
        self.key_press("left", 0.2)
        self.key_press("a", 3.0)

    @focused
    def charon3_2(self):
        self.sbreset(589281)
        self.key_press("a", 5.0)

    @focused
    def charon2_1(self):
        self.sbreset(93644)
        self.key_press("a", 5.0)
        self.key_press("right", 0.5)
        self.key_press("a", 4.0)

    def charon_space(self):
        with self.with_focus("Charon exit controls"):
            self.sbreset(739690)
            self.key_press("a", 3.0)
            self.lclick(867, 95)
            self._yield(0.4)
            self.lclick(887, 95)
        if not self.transition_space(expected="pluto"):
            raise GameUnresponsive("Charon undocking did not reach Pluto and Charon")

    def charon3_vendor(self):
        with self.with_focus("Charon vendor approach"):
            self.sbreset(772754)
            self.key_press("a", 3.0)
            self.key_press("right", 1)
            self.lclick(924, 620)
            self._yield(0.3)
            self.lclick(952, 653)
            self._yield(0.3)
            self.lclick(999, 681)
        self.wait_trade()
        log("tradeopen - will click")
        self._yield(0.5)
        with self.input_action():
            self.lclick(1000, 322)
            self._yield(0.5)
            self.lclick(1000, 322)
            self._yield(0.5)
        self.repeat_vendor_click(993, 718, 40, 0.1)
        self.dismiss_trade()

    @focused
    def earth1_2(self):
        log("earth1")
        self.sbreset(143648)
        self._yield(0.2)
        self.key_press("a", 5.0)

    @focused
    def earth2_3(self):
        self.sbreset(478678)
        self.key_press("a", 4.0)
        self.key_press("left", 0.4)
        self.key_press("a", 3.0)

    def earth3_vendor(self, *, reset_position: bool = True) -> None:
        with self.with_focus("Earth vendor approach"):
            if reset_position:
                self.sbreset(411668)
                self.key_press("a", 3.0)
                self._yield(1)
                self.key_press("left", 0.2)
                self._yield(1)
                for _ in range(2):
                    self.key_press("a", 0.2)
                    self._yield(0.5)
                    self.lclick(423, 229)
                    self.lclick(474, 186)
                    self.lclick(508, 182)
                    self.lclick(514, 206)
                    self.lclick(506, 367)
            else:
                # Same vendor position after dismiss_trade; reopen by clicking
                # Lenorn's face rather than repeating the reset/walking route.
                self.lclick(650, 255)
        self.wait_trade()
        self.sell_all()
        self.dismiss_trade()
        self._yield(3)

    @focused
    def earth2_j(self):
        self.sbreset(60293)
        self.key_press("a", 5.0)

    @focused
    def earthj_2(self):
        self.sbreset(207543)
        self.key_press("a", 2.0)
        self.key_press("left", 0.4)
        self.key_press("a", 3.0)

    def earth3_2(self):
        for _ in range(2):
            with self.with_focus("Earth station walking segment"):
                self.sbreset(411668)
                self.key_press("a", 3.8)
                self._yield(0.4)
                self.key_press("left", 0.6)
                self.key_press("a", 2.8)
                self._yield(0.4)
                self.key_press("left", 0.55)
                self.key_press("a", 3.0)

    @focused
    def earth2_1(self):
        self.sbreset(866689)
        self.key_press("a", 3.0)
        self.key_press("right", 0.2)
        self.key_press("a", 3.0)

    def earth1_space(self):
        with self.with_focus("Earth exit controls"):
            self.sbreset(904300)
            self.key_press("a", 1.0)
            self.lclick(1171, 105)
        if not self.transition_space(expected="earth"):
            raise GameUnresponsive("Earth undocking did not reach Earth sector")

    @focused
    def somer1_2(self):
        self.sbreset(0)
        self.key_press("a", 5)

    @focused
    def somer2_3(self):
        self.sbreset(82013)
        self.key_press("{LEFT}", 0.1)
        self.key_press("a", 5)

    def somer3_vendor(self):
        log("somer3")
        with self.with_focus("Somers vendor approach"):
            self.sbreset(0)
            self.key_press("a", 3.4)
            self.key_press("{RIGHT}", 0.2)
            self.key_press("a", 3)
            x = 26
            for _ in range(4):
                self.lclick(x, 278)
                self._yield(0.4)
                x += 100
        self.wait_trade()
        self._yield(2)
        self.sell_all()
        self._yield(1)
        self.repeat_vendor_click(1256, 698, 35, 0.05)
        self._yield(1)
        self.lclick(1042, 498, yield_time=0.5)
        self.repeat_vendor_click(988, 717, 44, 0.1)
        self._yield(2)
        self.dismiss_trade()

    @focused
    def somer3_2(self):
        self.sbreset(0)
        self.key_press("a", 4)
        self.key_press("{LEFT}", 0.7)
        self.key_press("a", 3)
        self.key_press("{LEFT}", 0.4)
        self.key_press("a", 2)

    @focused
    def somer2_1(self):
        self.sbreset(202581)
        self.key_press("{RIGHT}", 0.1)
        self.key_press("a", 4)
        self.key_press("{RIGHT}", 0.1)
        self.key_press("a", 1)

    def somer1_space(self):
        log("somero1")
        self.sbreset(70570)
        self.lclick(1087, 165)
        self._yield(3)
        if not self.transition_space(expected="newed"):
            raise GameUnresponsive("Somerled undocking did not reach New Edinburgh")

    @focused
    def prasad1_2(self):
        self.sbreset(0)
        self.key_press("a", 3)
        self.key_press("{RIGHT}", 0.2)
        self.key_press("a", 3)

    @focused
    def prasad2_3(self):
        log("prasad2")
        self.sbreset(1548090231)
        self.key_press("{RIGHT}", 0.3)
        self.key_press("a", 3)
        self.key_press("{RIGHT}", 0.2)
        self.key_press("a", 2)

    def prasad3_vendor(self):
        log("prasad3")
        with self.with_focus("Prasad vendor approach"):
            self.sbreset(246654)
            self.key_press("a", 2)
            self.key_press("{RIGHT}", 0.3)
            self.key_press("a", 2)
            self.key_press("{RIGHT}", 0.9)
            x = 1006
            for _ in range(4):
                self.lclick(x, 340)
                self._yield(0.4)
                x -= 100
        self.wait_trade()
        self._yield(2)
        self.sell_all()
        self._yield(1)
        self.lclick(925, 325)
        self._yield(0.5)
        self.repeat_vendor_click(924, 720, 40, 0.1)
        self._yield(1)
        self.dismiss_trade()

    @focused
    def prasad3_2(self):
        log("prasado3")
        self.sbreset(1548090231)
        self.key_press("{RIGHT}", 0.2)
        self.key_press("a", 4)
        self.key_press("{RIGHT}", 0.1)
        self.key_press("a", 4)

    @focused
    def prasad2_1(self):
        log("prasado2")
        self.sbreset(0)
        self.key_press("{RIGHT}", 0.2)
        self.key_press("a", 3)

    def prasad1_space(self):
        log("prasado1")
        self.sbreset(0)
        self.key_press("{RIGHT}", 1)
        x = 563
        for _ in range(5):
            self.lclick(x, 57)
            x += 150
            self._yield(0.5)
        if not self.transition_space(expected="xipe"):
            raise GameUnresponsive("Prasad undocking did not reach Xipe")

    @focused
    def setup_kranken(self):
        if getattr(self, '_skip_next_travel_setup', False):
            self._skip_next_travel_setup = False
            log_event('Travel equipment already prepared before undocking departure')
            return
        log_event('Preparing Kraken and reactor before warp')
        self.key_press("m")
        self.key_press("i")
        self.moveTo(642, 584)
        self._yield(0.3)
        self.mouseDown()
        self._yield(0.3)
        self.moveTo(788, 719)
        self._yield(0.3)
        self.mouseUp()
        self._yield(0.3)
        self.key_press("`", 0.5)
        self.key_press("4", 1.5)
        self.key_press("1", 0.5)
        self.key_press("i")
        self._yield(1)

    def killenb(self):
        self._skip_next_travel_setup = False
        log_event(f"Closing owned game and proxy for {self.player.toon_name}.")
        if not self.instance:
            raise RuntimeError("Shutdown requires an explicitly configured game instance")
        self.instance.stop()
        if self.focus and not self.singleton:
            self.focus.station_blocker(None)
        self.window = None
        self._yield(3)

    def launchEnb(self):
        proxy = net7_proxy()
        instance = getattr(self, "instance", None)
        if instance and instance.automatic:
            blocked, preferred = instance.slot_availability()
            instance.assign_slot(self.focus.claim_available_slot(GAME_CLIENTS, blocked, preferred))
            configure_chat_path(instance.chat_log)
            log_event(f"Selected available game slot {instance.slot}: {instance.client}")
        client = instance.client if instance else GAME_CLIENT
        if not client.is_file():
            raise FileNotFoundError(f"Cannot find the game client: {client}. Check GAME_CLIENTS in config.py.")
        log_event("Launching game through Net-7.")
        process = subprocess.Popen(
            [str(proxy), f"/ADDRESS:{SERVER_IP}", f"/CLIENT:{client}", "/SSL:443", "/POPT", "/DML"],
            cwd=proxy.parent,
        )
        if instance:
            instance.launched(process)
        return process

    def init_read_chat(self):
        if not self.bring_to_front():
            self.require_window()
            raise RuntimeError("Cannot focus the game to initialize chat.")
        marker = begin_chat()
        self.say(f"/t {self.player.toon_name} {marker}")
        log_event(f"Chat session armed; waiting for marker {marker} in chat.log.")
        deadline = self.active_time() + 60
        while not chat_ready():
            self.check_chat_response()
            self._yield(0.5)
            if self.active_time() >= deadline and not chat_ready():
                raise GameUnresponsive("Chat startup marker not found within 60 seconds; game may be frozen.")

    def check_chat_response(self):
        log_every("chat_response", f"checking chat response thread={threading.current_thread().name}", interval=150.0)
        read_chat_internal()
        with _CHECK_PROGRESS_LOCK:
            if self._stopping or self._chat_stop or (self._terminated and not self._logging_in) or self._reply_pending:
                return
            reply = next_reply()
            if not reply:
                return
            decision = reply_decision(reply)
            if decision is None:
                return
            if decision.action == "STOP":
                self._chat_stop = (reply, decision)
                request_interrupt(self._stop_for_chat)
                return
            if self._logging_in or not chat_ready():
                return
            self._reply_pending = True

        def answer_fn():
            try:
                while not self._terminated and not self._logging_in and chat_ready():
                    reply = next_reply()
                    if not reply:
                        return
                    decision = reply_decision(reply)
                    if decision is None:
                        return
                    if decision.action == "STOP":
                        self._chat_stop = (reply, decision)
                        self._stop_for_chat()
                    if not self.bring_to_front():
                        return
                    sender = reply[0]
                    self.say(f"/t {sender} {decision.response}")
                    reply_sent(reply, decision.response)
                    log_interaction(self.player.toon_name, reply, decision, response_sent=True)
                    log_event(f"Chat reply to {sender}: action={decision.action}; response={decision.response}")
            finally:
                with _CHECK_PROGRESS_LOCK:
                    self._reply_pending = False
        request_interrupt(answer_fn)

    def _stop_for_chat(self):
        if self._stopping or not self._chat_stop:
            return
        self._stopping = True
        reply, decision = self._chat_stop
        log_event(f"Chat action STOP from {reply[0]}; response={decision.response}")
        self.terminate()
        stop_chat()
        sent, closed, errors = False, False, []
        try:
            if decision.response and not self._logging_in and not (self.focus and self.focus.waiting) and chat_ready() and self.bring_to_front():
                self.say(f"/t {reply[0]} {decision.response}")
                # Enter queues the reply in the client. Let it transmit before
                # closing either client, including a peer that sent this PM.
                time.sleep(1.0)
                sent = True
                reply_sent(reply, decision.response)
        except (Exception, KeyboardInterrupt) as exc:
            errors.append(f"Reply send failed: {type(exc).__name__}")
        finally:
            # Only locally received STOP broadcasts; HTTP logoff never does.
            # Sending failure must still stop the peers and this game.
            if self.focus and not self.singleton:
                try:
                    for toon, error in self.focus.broadcast_logoff():
                        log_event(f"STOP broadcast to {toon}: {'queued' if error is None else 'failed: ' + error}")
                except Exception as exc:
                    log_event(f"STOP broadcast failed: {exc}")
            try:
                self.killenb()
                closed = not self.instance.valid() if self.instance else not bool(find_window_exact(self.window_name))
                if not closed:
                    errors.append("Game window remains after close request")
            except (Exception, KeyboardInterrupt) as exc:
                errors.append(f"Game close failed: {type(exc).__name__}")
            try:
                log_interaction(self.player.toon_name, reply, decision, response_sent=sent,
                                game_closed=closed, error="; ".join(errors) or None)
            except OSError as exc:
                errors.append(f"Interaction log write failed: {type(exc).__name__}")
            reason = f"Chat action STOP from {reply[0]}; macro stopped."
            if errors:
                reason += " " + "; ".join(errors)
            raise StopMacro(reason)

    def relogin(self, initial=False):
        with _CHECK_PROGRESS_LOCK:
            if self._logging_in:
                return
            self._logging_in = True
        self.terminate()
        suspend_chat()
        try:
            self._login(initial)
            self.player.relogin_time = self.player.set_time = time.time()
        finally:
            with _CHECK_PROGRESS_LOCK:
                self._logging_in = False

    def _record_relogin(self):
        if self.player.progress_version != self._last_progress_version:
            self.relogin_attempts = 0
            self._last_progress_version = self.player.progress_version
        now = time.monotonic()
        while self._relogin_times and now - self._relogin_times[0] >= RELOGIN_WINDOW:
            self._relogin_times.popleft()
        if self.relogin_attempts >= MAX_RELOGINS or len(self._relogin_times) >= MAX_RELOGINS:
            reason = "since last progress" if self.relogin_attempts >= MAX_RELOGINS else "within 10 minutes"
            log_event(f"Relogin limit reached: {MAX_RELOGINS} attempts {reason}; closing game and stopping macro.")
            self.killenb()
            raise RuntimeError(f"Relogin limit reached ({MAX_RELOGINS} attempts {reason}). Game closed; Pluto aborted.")
        self.relogin_attempts += 1
        self._relogin_times.append(now)

    @focused
    def enter_login_credentials(self):
        if self.startup_popup:
            raise GameUnresponsive("Cannot enter credentials while EBPopup is still open")
        if not self.bring_to_front():
            self.require_window()
            raise RuntimeError("Cannot focus Earth & Beyond to enter login credentials.")
        self.lclick(300, 190, yield_time=1)
        self.typewrite(self.player.username)
        self.lclick(300, 245, yield_time=1)
        self.typewrite(self.player.password)
        self.key_press("enter")

    def _login(self, initial=False):
        log_event(f"{'Login' if initial else 'Relogin'} started for {self.player.toon_name}")
        attempt = 0
        if self.bring_to_front() and not self.startup_popup:
            self.say("/quit")
            self._yield(3)
        while True:
            if not (initial and attempt == 0):
                self._record_relogin()
            attempt += 1
            log_event(f"Login attempt {attempt}; relogins since progress {self.relogin_attempts}/{MAX_RELOGINS}; in 10 minutes {len(self._relogin_times)}/{MAX_RELOGINS}")
            self.killenb()
            self.launchEnb()
            self._yield(10)
            if self.instance:
                self.complete_startup()
            else:
                if not self.bring_to_front():
                    self.require_window()
                    raise RuntimeError("Cannot focus Earth & Beyond after launch.")
                self.key_press("space", 0.2)
                self._yield(10)
                if not self.bring_to_front():
                    self.require_window()
                    raise RuntimeError("Cannot focus Earth & Beyond during login.")
                self.key_press("space", 0.2)
            self._yield(12)
            self.enter_login_credentials()
            self._yield(10)
            self.lclick(220, SLOT_POS[self.player.toon_position])
            self._yield(7)
            self.lclick(1207, 713)
            found_inventory = self.wait_objects([INVENTORY_OBJ], yield_time=3, timeout=30.0)
            log(f"testing inv icon={found_inventory}")
            if found_inventory:
                self._yield(3)
                last_player_change = self.player.set_time
                if last_player_change + STUCK_TIMEOUT < time.time():
                    log("attempt getting unstuck")
                    self.key_press("x")
                    self._yield(1)
                    self.key_press("q")
                    self._yield(10)
                break
        self._yield(5)
        self.key_press("`")
        self._yield(3)
        self.lclick(646, 686)
        self._yield(3)
        if self.test_obj(TOW_ON_OBJ):
            self.lclick(319, 490)
            self._yield(8)
            self.say("/quit")
            self.killenb()
        else:
            self.player.relogin_time = time.time()
        log_event(f"Login completed for {self.player.toon_name}")

    def bring_to_front(self) -> bool:
        if self.focus:
            self.init_window()
            if not self.window:
                return False
            with self.input_action():
                return True
        focused = bring_to_front_exact(self.window_name)
        if focused:
            self.init_window()
        return focused

    def check_game_progress(self):
        control = getattr(self, '_pause_control', None)
        if getattr(self, '_paused', False) or (control is not None and control.requested()):
            return
        if not _CHECK_PROGRESS_LOCK.acquire(blocking=False):
            log("check_game_progress already running, skipping")
            return

        try:
            if self._terminated or self._logging_in or self._station_waiting or self._restart_pending or (self.focus and self.focus.waiting):
                return
            hwnd = self.window if self.instance and self.instance.valid() else (
                None if self.instance else find_window_exact(self.window_name))
            start = time.time()
            
            has_changed_pos = self.player.set_time + POS_CHANGE_TIME > start
            was_relog = self.player.relogin_time + RELOGIN_TIME > start
            since_set = start - self.player.set_time
            since_relog = start - self.player.relogin_time
            log_every(
                "pos_checker",
                f"{since_set:.1f}s since last pos set, {since_relog:.1f}s since last relog "
                f"[{self.player.previous_position}->{self.player.current_position} changed:{has_changed_pos} relog:{was_relog}]"
                f"[direction={PLAYER.current_dest}] [thread={threading.current_thread().name}]",
                interval=60.0,
            )
            
            if hwnd and (was_relog or has_changed_pos):
                return
            self._restart_pending = True
            generation = self._progress_generation

            def restart_handler():
                if self._station_waiting:
                    self._restart_pending = False
                    return
                if self._terminated or generation != self._progress_generation:
                    return
                if self.focus and self.instance.valid() and (
                    self.player.set_time + POS_CHANGE_TIME > time.time()
                    or self.player.relogin_time + RELOGIN_TIME > time.time()
                ):
                    self._restart_pending = False
                    return
                log_event("Restarting: game window missing or no progress detected.")
                self.relogin()
                raise RestartException("request restart after relogin")

            request_interrupt(restart_handler)
        finally:
            _CHECK_PROGRESS_LOCK.release()

    def schedule_check_game_progress(self) -> None:
        self.terminate()
        self._terminated = False
        self.schedule_event = run_every(30, self.check_game_progress)

    def terminate(self) -> None:
        with _CHECK_PROGRESS_LOCK:
            self._terminated = True
            self._progress_generation += 1
            self._restart_pending = False
            if self.schedule_event:
                self.schedule_event.set()
            

    @focused
    def activateCloack(self):
        for key in ("i", "u"):
            self.key_press(key)
            self._yield(0.1)
        self.lclick(55, 193)
        self.moveTo(323, 443)
        self._yield(0.3)
        for _ in range(20):
            pag.click()
        self.moveTo(163, 590)
        self._yield(0.5)
        self.mouseDown()
        self._yield(0.2)
        self.moveTo(942, 719)
        self._yield(1.0)
        self.mouseUp()
        self._yield(0.5)
        self.key_press("6")
        self._yield(0.5)


if __name__ == "__main__":
    controller = AcController("Earth & Beyond")
    controller.bring_to_front()
    controller._yield(2)
    controller.earth3_2()
