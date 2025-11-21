"""ac.py

Controller-centric automation utilities.

All automation is exposed through `AcController(window)`; mouse positions are
translated relative to the target window so multiple controllers can operate
independently.
"""

from collections import deque
import random
import subprocess
import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

import pyautogui as pag
import win32gui

from chatproc import read_chat_internal
from gui import bring_to_front_exact, find_window_exact, key_press
from objects import *
from util import log, log_every
from watchdog import RestartException, request_interrupt, run_every, run_interrupts
import memory

pag.PAUSE = 0.03

# Constants
SLOT_POS = [50, 90, 130, 170, 196]
POS_CHANGE_TIME = 300
STUCK_TIMEOUT = 1500
RELOGIN_TIME = 90


class Player:
    def __init__(self) -> None:
        self.toon_name = "lightje"
        self.type_ = "je"
        self.toon_position = 0
        self.password = "tolp101"
        self.current_position = "earth"
        self.previous_position = "earthP"
        self.current_dest = "earth"
        self.relogin_time = time.time()
        self.mission_count = 6
        self.set_time = time.time()

    def set_pos(self, pos: Optional[str]) -> None:
        if not pos:
            return
        self.previous_position = self.current_position
        self.current_position = pos
        if self.previous_position != pos:
            self.set_time = time.time()


PLAYER = Player()
_CHECK_PROGRESS_LOCK = threading.Lock()


class AcController:
    """Controller that maps ACTool-style coordinates to a given window."""

    def __init__(self, window_name: str) -> None:
        self.window_name = window_name
        self.player = PLAYER
        self.offset_x = 0
        self.offset_y = 0
        self.init_window()
        self.schedule_event:Optional[threading.Event]

    def init_window(self) -> None:
        self.window = find_window_exact(self.window_name)
        hwnd = self.window

        if hwnd:
            try:
                left, top = win32gui.ClientToScreen(hwnd, (0, 0))
                self.offset_x, self.offset_y = left, top
                log(f"rect:{left, top}")
            except Exception:
                self.offset_x = 0
                self.offset_y = 0
        log(f"AcController initialized for window '{self.window_name}' with offset ({self.offset_x}, {self.offset_y})")

    # --- coordinate translation ---
    def translate(self, x: int, y: int) -> Tuple[int, int]:
        return x + self.offset_x, y + self.offset_y

    def test_obj(self, obj:ActoolObject, screenshot=None) -> bool:
        """Run ActoolObject.test with controller offset applied."""
        return obj.test(offset=(self.offset_x, self.offset_y), screenshot=screenshot)

    # --- pyautogui wrappers ---

    def moveTo(self, x: int, y: int, duration: Optional[float] = None) -> None:
        tx, ty = self.translate(x, y)
        pag.moveTo(tx, ty, duration=0 if duration is None else duration)

    def mouseDown(self) -> None:
        pag.mouseDown()

    def mouseUp(self) -> None:
        pag.mouseUp()

    def click(self) -> None:
        pag.click()

    def screenshot(self):
        return pag.screenshot()

    def typewrite(self, text: str, interval: float = 0.02) -> None:
        pag.typewrite(text, interval=interval)

    def keyDown(self, key: str) -> None:
        pag.keyDown(key)

    def keyUp(self, key: str) -> None:
        pag.keyUp(key)

    # --- helpers ---
    def _yield(self, seconds: float, incr: float = 0.3) -> None:
        _incr = min(incr, seconds)
        if seconds > 0.0:
            current = time.time()
            target = current + seconds
            while current < target:
                time.sleep(_incr)
                run_interrupts()
                current += _incr

    def press(self, key: str) -> None:
        pag.press(key)
        self._yield(0.1)

    def say(self, text: str) -> None:
        self.typewrite(text, interval=0.02)
        pag.press("enter")

    def lclick(self, x: int, y: int, click: int = 1, shift: bool = False, yield_time: float = 0) -> None:
        self.moveTo(x, y)
        if shift:
            pag.keyDown("shift")
        if click != 0:
            pag.click()
        if shift:
            pag.keyUp("shift")
        self._yield(yield_time)

    # --- game helpers ---
    def sbreset(self, value: int) -> None:
        memory.setmemory_u32_active(0x00BDBB80, 0)
        memory.setmemory_u32_active(0x00BDB1C0, value)
        self.say("/starbase reset")
        self._yield(1)

    def close_job(self) -> None:
        log("close1job: confirm #1")
        self.lclick(598, 269)
        self._yield(0.5)
        log("close1job: confirm #2")
        self.lclick(543, 298)
        self._yield(0.5)

    def clear_jobs(self) -> None:
        key_press("m")
        self._yield(0.3)
        key_press("u")
        self._yield(0.3)
        for _ in range(6):
            self.lclick(110, 280)
            self.close_job()
            self.lclick(110, 380)
            self.close_job()
            self.lclick(323, 587, yield_time=0.3)

    def count_mission(self) -> int:
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
        for _ in range(2):
            screenshot = self.screenshot()
            if self.test_obj(MISSION_4_OBJ, screenshot=screenshot):
                jobs_below_3 = max(jobs_below_3, 4)
            if self.test_obj(MISSION_5_OBJ, screenshot=screenshot):
                jobs_below_3 = max(jobs_below_3, 5)
            if self.test_obj(MISSION_6_OBJ, screenshot=screenshot):
                jobs_below_3 = max(jobs_below_3, 6)
            if self.test_obj(MISSION_7_OBJ, screenshot=screenshot):
                jobs_below_3 = max(jobs_below_3, 7)
        max_job = max(ret, jobs_below_3)
        self.press("m")
        return max_job

    def empty_one(self, x: int, y: int) -> None:
        self.moveTo(x, y)
        self._yield(0.1)
        pag.mouseDown()
        self._yield(0.2)
        self.moveTo(99, 128)
        self._yield(0.2)
        pag.mouseUp()
        self._yield(0.1)
        self.lclick(240, 165)
        self._yield(0.2)

    def empty_inventory(self) -> None:
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
        for x, y in slots:
            self.empty_one(x, y)

    def get_map(self) -> None:
        self.press("u")
        self._yield(0.1)
        self.press("m")
        if self.test_obj(LARGE_MAP_OBJ, screenshot=self.screenshot()):
            self.lclick(711, 187)
            self._yield(0.2)
        key_press("-", 3.0)
        for sx, sy, ex, ey in [
            (396, 397, 396, 600),
            (396, 397, 396, 600),
            (90, 416, 400, 416),
            (90, 416, 400, 416),
        ]:
            tsx, tsy = self.translate(sx, sy)
            tex, tey = self.translate(ex, ey)
            pag.moveTo(tsx, tsy)
            pag.mouseDown()
            pag.moveTo(tex, tey, duration=0.1)
            pag.mouseUp()
        self._yield(0.2)

    def close_map(self) -> None:
        key_press("i")
        self._yield(0.1)
        key_press("m")
        self._yield(0.1)
        key_press("m")
        self._yield(0.1)

    def wait_objects(self, objs: List[object], yield_time: float = 0.8, timeout: float = 160.0, yield_fn: Optional[Callable[[], None]] = None) -> bool:
        start = time.time()
        max_x = 0
        max_y = 0
        for o in objs:
            try:
                ax, ay = o.anchor
            except Exception:
                ax, ay = 0, 0
            max_x = max(max_x, ax)
            max_y = max(max_y, ay)
        self.moveTo(max_x + 30, max_y + 30)
        while time.time() - start < timeout:
            sc = self.screenshot()
            for o in objs:
                if self.test_obj(o, screenshot=sc):
                    log(f"object {getattr(o,'name',str(o))} found")
                    return True
            if yield_fn:
                yield_fn()
            self._yield(yield_time)
        return False

    def transition_space(self) -> bool:
        log("transition_space")
        sector_res = self.wait_objects([WAIT_SECTOR_OBJ], yield_time=2, timeout=30)
        if not sector_res:
            return False
        inv_res = self.wait_objects([INVENTORY_OBJ], yield_time=2, timeout=30)
        return inv_res

    def gate_internal(self, x: int, y: int, no_gate: bool = False) -> bool:
        self.press("`")
        time.sleep(0.2)
        log(f"gate::{x} -- {y}")
        self.get_map()
        self._yield(0.2)
        self.lclick(x, y)
        self._yield(3.0)
        ret_gate = self.test_obj(GATE_OBJ)
        ret_station = self.test_obj(STATION_OBJ)
        if ret_station:
            log("station found")
        ui_seen = ret_gate or ret_station
        if not ui_seen:
            log("waiting for igate obj")
            if not self.wait_objects([IGATE_OBJ, ISTATION_OBJ]):
                return False
            self._yield(0.1)
            self.press("q")
        log("waiting for station")
        if not self.wait_objects([GATE_OBJ, STATION_OBJ]):
            return False
        self._yield(1.0)
        if not no_gate:
            self.lclick(1236, 515)
            self.transition_space()
        return True

    def gate_sat(self, x: int, y: int) -> None:
        log(f"gate::{x} -- {y}")
        self.get_map()
        self.lclick(x, y)
        self._yield(3)
        if not self.test_obj(SAT_OBJ):
            log("waiting for isat obj")
            if not self.wait_objects([ISAT_OBJ], timeout=12):
                return
            key_press("q")
            self._yield(3)
        log("waiting for sat")
        self.wait_objects([SAT_OBJ], timeout=30, yield_fn=lambda: key_press("a", 1), yield_time=2)
        self._yield(1)
        key_press("a", 2.0)
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
        self.gate(x, y)
        self.player.set_pos(target)

    def travel_all(self, src: str, dest: str) -> None:
        log(f"travel_all from {src} to {dest}")
        path = self.shortest_path(src, dest)
        log(f"travel path: {path}")
        self.player.current_position = src
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
        for _ in range(2):
            for _ in range(9):
                self.lclick(*LEFT_ARR)
            for _ in range(5):
                for pos in INVENTORY_SLOTS:
                    self.lclick(*pos, shift=True)
                for _ in range(3):
                    self.lclick(*RIGHT_ARR)

    def wait_trade(self) -> None:
        self.wait_objects([TRADE_OPEN_OBJ], yield_time=0.5, timeout=10.0)
        self.lclick(507, 663)
        self.wait_objects([TRADE2_OPEN_OBJ], yield_time=0.5, timeout=10.0)
        log("trade2 is open")

    def dismiss_trade(self) -> None:
        self.lclick(1255, 253)
        self._yield(0.5)
        self.lclick(392, 626)
        self.wait_objects([TRADE_CLOSE_OBJ], yield_time=0.5, timeout=30.0)
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
                    return name
            self._yield(0.5)
        log("no position obj found")
        return None

    # --- sector-specific procedures ---
    def charon1_2(self):
        self.sbreset(805890)
        key_press("a", 4.0)

    def charon2_3(self):
        self.sbreset(329497)
        key_press("a", 4.0)
        key_press("left", 0.2)
        key_press("a", 3.0)

    def charon3_2(self):
        self.sbreset(589281)
        key_press("a", 5.0)

    def charon2_1(self):
        self.sbreset(93644)
        key_press("a", 5.0)
        key_press("right", 0.5)
        key_press("a", 4.0)

    def charon_space(self):
        self.sbreset(739690)
        key_press("a", 3.0)
        self.lclick(867, 95)
        self._yield(0.4)
        self.lclick(887, 95)
        self.transition_space()
        self.player.set_pos("pluto")

    def charon3_vendor(self):
        self.sbreset(772754)
        key_press("a", 3.0)
        key_press("right", 1)
        self.lclick(924, 620)
        self._yield(0.3)
        self.lclick(952, 653)
        self._yield(0.3)
        self.lclick(999, 681)
        self.wait_trade()
        log("tradeopen - will click")
        self._yield(0.5)
        self.lclick(1000, 322)
        self._yield(0.5)
        self.lclick(1000, 322)
        self._yield(0.5)
        for _ in range(40):
            self.lclick(993, 718)
            self._yield(0.1)
        self.dismiss_trade()

    def earth1_2(self):
        log("earth1")
        self.sbreset(143648)
        self._yield(0.2)
        key_press("a", 5.0)

    def earth2_3(self):
        self.sbreset(478678)
        key_press("a", 4.0)
        key_press("left", 0.4)
        key_press("a", 3.0)

    def earth3_vendor(self):
        self.sbreset(411668)
        key_press("a", 3.0)
        self._yield(1)
        key_press("left", 0.2)
        self._yield(1)
        for _ in range(2):
            key_press("a", 0.2)
            self._yield(0.5)
            self.lclick(423, 229)
            self.lclick(474, 186)
            self.lclick(508, 182)
            self.lclick(514, 206)
            self.lclick(506, 367)
        self.wait_trade()
        self.sell_all()
        self.dismiss_trade()
        self._yield(3)

    def earth2_j(self):
        self.sbreset(60293)
        key_press("a", 5.0)

    def earthj_2(self):
        self.sbreset(207543)
        key_press("a", 2.0)
        key_press("left", 0.4)
        key_press("a", 3.0)

    def earth3_2(self):
        for _ in range(2):
            self.sbreset(411668)
            key_press("a", 3.8)
            self._yield(0.4)
            key_press("left", 0.6)
            key_press("a", 2.8)
            self._yield(0.4)
            key_press("left", 0.55)
            key_press("a", 3.0)

    def earth2_1(self):
        self.sbreset(866689)
        key_press("a", 3.0)
        key_press("right", 0.2)
        key_press("a", 3.0)

    def earth1_space(self):
        self.sbreset(904300)
        key_press("a", 1.0)
        self.lclick(1171, 105)
        self.transition_space()
        self.player.set_pos("earth")

    def somer1_2(self):
        self.sbreset(0)
        key_press("a", 5)

    def somer2_3(self):
        self.sbreset(82013)
        key_press("{LEFT}", 0.1)
        key_press("a", 5)

    def somer3_vendor(self):
        log("somer3")
        self.sbreset(0)
        key_press("a", 3.4)
        key_press("{RIGHT}", 0.2)
        key_press("a", 3)
        x = 26
        for _ in range(4):
            self.lclick(x, 278)
            self._yield(0.4)
            x += 100
        self.wait_trade()
        self._yield(2)
        self.sell_all()
        self._yield(1)
        for _ in range(35):
            self.lclick(1256, 698, yield_time=0.05)
        self._yield(1)
        self.lclick(1042, 498, yield_time=0.5)
        for _ in range(44):
            self.lclick(988, 717, yield_time=0.1)
        self._yield(2)
        self.dismiss_trade()

    def somer3_2(self):
        self.sbreset(0)
        key_press("a", 4)
        key_press("{LEFT}", 0.7)
        key_press("a", 3)
        key_press("{LEFT}", 0.4)
        key_press("a", 2)

    def somer2_1(self):
        self.sbreset(202581)
        key_press("{RIGHT}", 0.1)
        key_press("a", 4)
        key_press("{RIGHT}", 0.1)
        key_press("a", 1)

    def somer1_space(self):
        log("somero1")
        self.sbreset(70570)
        self.lclick(1087, 165)
        self._yield(3)
        self.transition_space()
        self.player.set_pos("newed")

    def prasad1_2(self):
        self.sbreset(0)
        key_press("a", 3)
        key_press("{RIGHT}", 0.2)
        key_press("a", 3)

    def prasad2_3(self):
        log("prasad2")
        self.sbreset(1548090231)
        key_press("{RIGHT}", 0.3)
        key_press("a", 3)
        key_press("{RIGHT}", 0.2)
        key_press("a", 2)

    def prasad3_vendor(self):
        log("prasad3")
        self.sbreset(246654)
        key_press("a", 2)
        key_press("{RIGHT}", 0.3)
        key_press("a", 2)
        key_press("{RIGHT}", 0.9)
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
        for _ in range(40):
            self.lclick(924, 720, yield_time=0.1)
        self._yield(1)
        self.dismiss_trade()

    def prasad3_2(self):
        log("prasado3")
        self.sbreset(1548090231)
        key_press("{RIGHT}", 0.2)
        key_press("a", 4)
        key_press("{RIGHT}", 0.1)
        key_press("a", 4)

    def prasad2_1(self):
        log("prasado2")
        self.sbreset(0)
        key_press("{RIGHT}", 0.2)
        key_press("a", 3)

    def prasad1_space(self):
        log("prasado1")
        self.sbreset(0)
        key_press("{RIGHT}", 1)
        x = 563
        for _ in range(5):
            self.lclick(x, 57)
            x += 150
            self._yield(0.5)
        self.transition_space()
        self.player.set_pos("xipe")

    def setup_kranken(self):
        key_press("m")
        key_press("i")
        self.moveTo(642, 584)
        self._yield(0.3)
        pag.mouseDown()
        self._yield(0.3)
        self.moveTo(788, 719)
        self._yield(0.3)
        pag.mouseUp()
        self._yield(0.3)
        key_press("`", 0.5)
        key_press("4", 1.5)
        key_press("1", 0.5)
        key_press("i")
        self._yield(1)

    def killenb(self):
        subprocess.run(["taskkill", "/F", "/IM", "net7proxy.exe"], check=False)
        subprocess.run(["taskkill", "/F", "/IM", "client.exe"], check=False)
        subprocess.run(["taskkill", "/F", "/IM", "LaunchNet7.exe"], check=False)
        self._yield(3)

    def launchEnb(self):
        subprocess.Popen(r"C:\\MACROS\\startenb.bat", shell=True)

    def init_read_chat(self):
        if self.bring_to_front():
            txt = f"/t {self.player.toon_name} hello_{random.randint(1000,9999)}_{random.randint(200,9999)}"
            self.say(txt)
            log(f"init read chat: sent {txt}")
        else:
            log("init read chat: bring to front failed")

    def check_chat_response(self):
        log_every("chat_response", f"checking chat response thread={threading.current_thread().name}", interval=150.0)
        response = read_chat_internal()
        if response:
            sp = response.split("__")
            if len(sp) >= 2 and (len(sp[1]) > 1 or sp[0].strip() == "1"):
                def answer_fn():
                    log(f"chat response: {response} thread={threading.current_thread().name}")
                    if len(sp) >= 2:
                        msg = sp[1]
                        cmd = sp[0].strip()
                        if len(msg) > 1:
                            key_press("r")
                            self._yield(0.2)
                            self.say(msg)
                        if cmd == "1":
                            self.killenb()
                            sys.exit(0)
                request_interrupt(answer_fn)

    def relogin(self):
        log("---------------LOGIN CALLED----------------")
        attempt = 0
        self.say("/quit")
        self._yield(3)
        while True:
            attempt += 1
            log(f"logging attempt:{attempt}")
            self.killenb()
            self.launchEnb()
            self._yield(10)
            self.bring_to_front()
            key_press("space")
            self._yield(10)
            self.bring_to_front()
            key_press("space")
            self._yield(12)
            self.init_window()
            for ch in self.player.password:
                key_press(ch)
            key_press("enter")
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
                    key_press("x")
                    time.sleep(1)
                    key_press("q")
                    time.sleep(10)
                break
        self._yield(5)
        key_press("`")
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
        log("relogin done")

    def bring_to_front(self) -> bool:
        return bring_to_front_exact("Earth & Beyond")

    def check_game_progress(self):
        if not _CHECK_PROGRESS_LOCK.acquire(blocking=False):
            log("check_game_progress already running, skipping")
            return

        def restart_handler():
            log(f"RESTART ALL due to pos not changed - thread:{threading.current_thread().name} - PLAYER:{PLAYER}")
            self.relogin()
            log("relogin done, raising RestartException")
            raise RestartException("request restart as relogin")

        try:
            hwnd = find_window_exact(self.window_name)
            start = time.time()
            
            has_changed_pos = self.player.set_time + POS_CHANGE_TIME > start
            was_relog = self.player.relogin_time + RELOGIN_TIME > start
            self.player.relogin = False
            since_set = start - self.player.set_time
            since_relog = start - self.player.relogin_time
            log_every(
                "pos_checker",
                f"{since_set:.1f}s since last pos set, {since_relog:.1f}s since last relog "
                f"[{self.player.previous_position}->{self.player.current_position} changed:{has_changed_pos} relog:{was_relog}]"
                f"[direction={PLAYER.current_dest}] [thread={threading.current_thread().name}]",
                interval=60.0,
            )
            
            if not hwnd:
                log("no hwnd found - relogin")
                request_interrupt(restart_handler)
            if was_relog:
                return
            if not has_changed_pos:
                log(f"position not changed or no window- relogin: win={hwnd} change_pos={has_changed_pos} relogin={was_relog}")
                request_interrupt(restart_handler)
        finally:
            _CHECK_PROGRESS_LOCK.release()

    def schedule_check_game_progress(self) -> None:
        self.schedule_event = run_every(30, self.check_game_progress)

    def terminate(self) -> None:
        if self.schedule_event:
            self.schedule_event.set()
            

    def activateCloack(self):
        for key in ("i", "u"):
            key_press(key)
            self._yield(0.1)
        self.lclick(55, 193)
        self.moveTo(323, 443)
        self._yield(0.3)
        for _ in range(20):
            pag.click()
        self.moveTo(163, 590)
        self._yield(0.5)
        pag.mouseDown()
        self._yield(0.2)
        self.moveTo(942, 719)
        self._yield(1.0)
        pag.mouseUp()
        self._yield(0.5)
        key_press("6")
        self._yield(0.5)


if __name__ == "__main__":
    controller = AcController("Earth & Beyond")
    controller.bring_to_front()
    controller._yield(2)
    controller.earth3_2()
