#expect 1280x768

import argparse
import ctypes
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

from ac import AcController, PLAYER
from chatproc import require_admin, reset_chat, stop_chat, suspend_chat
from chat_reply import codex_executable
from config import CHAT_LOG_PATH, CHAT_PROMPT_PATH, CHAT_INTERACTION_LOG, WINDOW_TITLE, net7_proxy
from player import load_player_config
import pyautogui as pag
from gui import key_press
from objects import (
    CHARON_MONSTER_OBJ,
    CHARON_PIT1_OBJ,
    CHARON_PIT2_OBJ,
    CHARON_PIT_OBJ,
    ISAT_OBJ,
    JOB1_OBJ,
    JOB2_OBJ,
    JOB_OPEN_OBJ,
    LOWJOB1_OBJ,
    LOWJOB2_OBJ,
    LOWJOB3_OBJ,
    LOWJOB4_OBJ,
    SAT_OBJ,
    TRADE2_OPEN_OBJ,
    PLUTO_TRADE_OBJ,
    TRADE_OPEN_OBJ,
)
from util import log, log_event, configure_event_log
from failure_capture import capture_failure
from watchdog import GameWindowLost, GameUnresponsive, RestartException, StopMacro, run_every


class PlutoRunner:
    def __init__(self, player_settings=None, low_job=None, avoid_pit=None) -> None:
        self.controller:Optional[AcController] = None
        self.player_settings = player_settings
        self.low_job = low_job if low_job is not None else bool(player_settings and player_settings.low_job)
        self.avoid_pit = avoid_pit if avoid_pit is not None else bool(player_settings and player_settings.avoid_pit)

    def _test(self, obj, **kwargs) -> bool:
        return self.controller.test_obj(obj, **kwargs)

    def walk_job(self):
        self.controller.sbreset(739690)
        key_press("a", 4)
        self.controller.lclick(1102, 88)
        self.controller._yield(3)
        self.controller.lclick(1258, 250)
        self.controller._yield(2)

    def sort_job(self):
        self.controller.lclick(95, 342)
        self.controller.lclick(607, 295)
        if not self.low_job:
            self.controller.lclick(607, 295)

    def open_job(self):
        while not self._test(JOB_OPEN_OBJ):
            self.controller.lclick(1052, 52)
            self.controller._yield(0.5)

    def is_good_low_job(self) -> bool:
        self.controller._yield(2)
        sc = self.controller.screenshot()
        return (
            self._test(LOWJOB1_OBJ, screenshot=sc)
            or self._test(LOWJOB2_OBJ, screenshot=sc)
            or self._test(LOWJOB3_OBJ, screenshot=sc)
            or self._test(LOWJOB4_OBJ, screenshot=sc)
        )

    def is_good_job(self) -> bool:
        self.controller._yield(2)
        sc = self.controller.screenshot()
        return self._test(JOB1_OBJ, screenshot=sc) or self._test(JOB2_OBJ, screenshot=sc)

    def is_pit_job(self) -> bool:
        self.controller._yield(1)
        sc = self.controller.screenshot()
        return (
            self._test(CHARON_PIT_OBJ, screenshot=sc)
            or self._test(CHARON_PIT1_OBJ, screenshot=sc)
            or self._test(CHARON_PIT2_OBJ, screenshot=sc)
        )

    def close_job(self):
        while self._test(JOB_OPEN_OBJ):
            self.controller.lclick(1255, 254)
            self.controller._yield(2)
        self.controller._yield(2)

    def pick_job(self):
        PLAYER.set_pos("earthJOB")
        start = time.time()

        self.open_job()
        self.sort_job()

        mc = self.controller.count_mission()
        mc_real = mc

        while mc < PLAYER.mission_count:
            for _ in range(45):
                self.controller.lclick(1256, 326)
                self.controller._yield(0.1)

            if not self.low_job:
                self.pickBatchJob(self.is_good_job)
            else:
                self.pickBatchJob(self.is_good_low_job)

            self.controller.close_map()
            self.open_job()
            self.sort_job()
            mc_new = self.controller.count_mission()
            self.controller.close_map()

            if mc_new > mc:
                PLAYER.set_pos(f"earthJOB_jc_{mc_new}")

            mc = mc_new
            mc_real = mc

            elapsed = time.time() - start
            log(f"pick job ellapsed={int(elapsed)}")

            if elapsed > 180:
                PLAYER.set_pos("earthJOB2")
            if elapsed > 450:
                log(f"{mc} - GIVING UP TOOK TOO LONG")
                mc = PLAYER.mission_count + 2
                break

        log(f"pickJob finished with {mc_real} jobs")

    def pickBatchJob(self, check_job_fn: Callable[[], bool]):
        pc_start = 327
        for page in range(2):
            pc = pc_start
            for _ in range(10):
                self.controller.lclick(290, pc)
                self.controller._yield(0.05, incr=0.05)
                if check_job_fn():
                    if self.avoid_pit:
                        if not self.is_pit_job():
                            self.controller.lclick(1089, 706)
                            self.controller._yield(1)
                    else:
                        self.controller.lclick(1089, 706)
                        self.controller._yield(1)
                pc += 20

            self.controller.lclick(1254, 513)
            if page < 1:
                for _ in range(10):
                    self.controller.lclick(1254, 513)
                    self.controller._yield(0.3)

    def earth_pluto(self):
        self.controller.setup_kranken()
        PLAYER.current_position = "earth"
        path = self.controller.shortest_path(PLAYER.current_position, "pluto")
        log(f"earth_pluto path: {path}")
        for p in path[1:]:
            self.controller.travel_one(PLAYER.current_position, p)

    def pluto_earthS(self):
        self.controller.setup_kranken()
        PLAYER.current_position = "pluto"
        path = self.controller.shortest_path(PLAYER.current_position, "earthS")
        log(f"pluto_earth path: {path}")
        for p in path[1:]:
            self.controller.travel_one(PLAYER.current_position, p)

    def find_dir(self):
        key_press("m")
        self.controller._yield(0.1)
        key_press("i")
        self.controller._yield(0.5)
        if self._test(PLUTO_TRADE_OBJ):
            PLAYER.current_dest = "earthS"
        else:
            PLAYER.current_dest = "pluto"
        log(f"found dir to {PLAYER.current_dest}")

    def route(self):
        while True:
            log(f"ROUTE {PLAYER.current_position} {PLAYER.current_dest}")
            dest = PLAYER.current_dest
            if PLAYER.current_position == "earthS":
                self.all_earth()
                PLAYER.current_dest = "pluto"
            elif PLAYER.current_position == "charonS":
                self.all_charon()
                PLAYER.current_dest = "earthS"
            elif dest in ("pluto", "earthS"):
                self.controller.setup_kranken()
                path = self.controller.shortest_path(PLAYER.current_position, dest)
                log(f"travel path: {path}")
                for p in path[1:]:
                    self.controller.travel_one(PLAYER.current_position, p)
                if dest == "pluto":
                    PLAYER.current_dest = "charonS"
            elif dest == "charonS":
                self.pluto_dump()
                PLAYER.current_dest = "earthS"
            else:
                log(f"Unknown pos destination {PLAYER.current_position} {PLAYER.current_dest}")
            self.controller._yield(2)

    def pluto_dump(self):
        self.controller.gate_sat(427, 617)

        if PLAYER.type_ == "te":
            key_press("5")
            self.controller._yield(2)

        found_monster = False
        for _ in range(3):
            key_press("x")
            for _ in range(5):
                key_press("c")
                self.controller._yield(0.5)
                if self.controller.wait_objects([CHARON_MONSTER_OBJ], timeout=2):
                    found_monster = True
                    break
            if found_monster:
                break
        if found_monster:
            log("found charon monster")
            if self.controller.wait_objects([ISAT_OBJ], timeout=4):
                log("found isat obj")
                if PLAYER.type_ == "je":
                    log("cloack")
                    self.controller.activateCloack()
                self.controller._yield(2)
                key_press("q")
                self.controller._yield(5)
                log("wait for sat obj")
                self.controller.wait_objects([SAT_OBJ], timeout=45, yield_fn=lambda: key_press("a", 1.2), yield_time=1.5)
                self.controller._yield(0.5)

                for _ in range(7):
                    self.controller.lclick(1238, 516)
                    self.controller._yield(0.5)

                key_press("s")
                self.controller._yield(0.3)
                key_press("q")
                self.controller._yield(7.0)
                key_press("q")
        self.controller.travel_one("pluto", "charonS")

    def all_earth(self):
        self.controller._yield(2)
        self.controller.close_map()
        self.controller._yield(2)

        self.controller.earth1_2()
        self.controller.earth2_3()
        self.controller.earth3_vendor()

        log("SELLALL IN EARTH")

        self.controller.earth3_2()
        self.controller.earth2_j()
        self.walk_job()
        self.pick_job()
        self.close_job()
        self.controller.earthj_2()
        self.controller.earth2_1()
        self.controller.earth1_space()

        PLAYER.current_dest = "pluto"

    def all_charon(self):
        self.controller._yield(5)

        mc = self.controller.count_mission()
        if mc > 1:
            self.controller.clear_jobs()

        self.controller.close_map()
        self.controller.empty_inventory()
        self.controller.close_map()

        self.controller.charon1_2()
        self.controller.charon2_3()
        self.controller.charon3_vendor()

        log("SELLALL IN CHARON")

        self.controller.charon3_2()
        self.controller.charon2_1()
        self.controller.charon_space()

        PLAYER.current_dest = "earth"

    def run(self):
        if self.player_settings is None:
            raise ValueError("Pass a player configuration file to pluto.py")
        PLAYER.configure(self.player_settings)
        log_event(f"Pluto starting for player {PLAYER.toon_name} (type {PLAYER.type_}).")
        require_admin()
        self.chat_event = None
        try:
            reset_chat(PLAYER.toon_name)
            self._run_loop()
        except (StopMacro, pag.FailSafeException):
            raise
        except Exception as exc:
            capture_failure(exc)
            raise
        finally:
            # Prevent new COM reads, then drain any poll already in progress.
            if self.chat_event:
                self.chat_event.set()
            if self.controller:
                self.controller.terminate()
            stop_chat()
            if self.chat_event:
                self.chat_event.join()
            log_event("Pluto shutdown complete; background chat polling stopped.")

    def _run_loop(self):
        self.controller = AcController(WINDOW_TITLE)
        self.chat_event = run_every(20, self.controller.check_chat_response)
        initial = not self.controller.window
        recover = False
        while True:
            try:
                log(f"start - thread:{threading.current_thread().name}")
                self.controller.init_window()
                if recover or not self.controller.window:
                    log_event("Restarting game and logging in." if recover else "Game is not open; launching and logging in.")
                    cold_start, initial = initial, False
                    self.controller.relogin(initial=cold_start)
                    recover = False
                initial = False
                if not self.controller.bring_to_front():
                    self.controller.require_window()
                    raise RuntimeError("Cannot focus Earth & Beyond. Make the game's desktop available and try again.")
                self.controller.refresh_offset()
                log(f"Game resolution: {self.controller.client_size}; physical pixels: {self.controller.screen_size}")
                self.controller._terminated = False
                self.controller.init_read_chat()
                self.controller.check_chat_response()
                self.controller._yield(0.1)
                self.controller.schedule_check_game_progress()
                self.find_dir()
                position = self.controller.get_current_position()
                if position is None:
                    raise RuntimeError("Could not recognize the current sector/station. Check the game view and resolution.")
                # Recognizing our location after login is not route progress.
                PLAYER.set_pos(position, progress=False)
                self.route()
            except (GameWindowLost, GameUnresponsive) as exc:
                capture_failure(exc)
                self.controller.terminate()
                suspend_chat()
                log_event(f"{exc} Recovering through relogin.")
                # Keep this controller, its retry counters, and all queued chat/history.
                # Consume the cold-start exemption even if the first login crashed.
                recover, initial = True, False
                continue
            except RestartException as re:
                log_event(f"main caught RESTART exception: {re}")
                continue


def request_elevation(argv):
    """Relaunch this script with UAC, preserving the interpreter and arguments."""
    script = Path(__file__).resolve().with_name("pluto_console.py")
    execute = ctypes.windll.shell32.ShellExecuteW
    execute.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                       ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    execute.restype = ctypes.c_void_p
    result = execute(None, "runas", sys.executable,
                     subprocess.list2cmdline([str(script), *argv]), str(script.parent), 1)
    if not result or result <= 32:
        print("Administrator launch was cancelled or failed. Accept the Windows UAC prompt to run Pluto.", file=sys.stderr)
        return 1
    print("Administrator launch requested. The new console will stay open to show any startup error.")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Earth & Beyond Pluto job macro.")
    parser.add_argument("player_config", type=Path, help="Player settings file, e.g. config_player.txt.")
    parser.add_argument("--check", action="store_true", help="Check installation without sending game input.")
    parser.add_argument("--low-job", action="store_true", default=None, help="Override config to select low-level jobs.")
    parser.add_argument("--avoid-pit", action="store_true", default=None, help="Override config to skip Charon pit jobs.")
    args = parser.parse_args(argv)
    config_path = args.player_config.expanduser().resolve()
    try:
        settings = load_player_config(config_path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.check:
        print(f"Player config: {config_path}")
        print(f"Player: {settings.name}; type: {settings.player_type}; slot: {settings.character_slot}")
        print(f"Python: {sys.executable}")
        print(f"Net-7 proxy: {net7_proxy()}")
        print("Imports and dependencies: OK")
        print(f"Chat log: {CHAT_LOG_PATH} (VSS reading requires administrator access)")
        print(f"Chat replies: {codex_executable()} (uses saved Codex login)")
        if not CHAT_PROMPT_PATH.read_text(encoding="utf-8-sig").strip():
            raise ValueError("chat_prompt.txt is empty")
        print(f"Editable chat prompt: {CHAT_PROMPT_PATH}")
        print(f"Interaction log: {CHAT_INTERACTION_LOG}")
        controller = AcController(WINDOW_TITLE)
        if controller.window:
            controller.refresh_offset()
            print(f"Game resolution: {controller.client_size[0]} x {controller.client_size[1]}")
            print(f"Physical screen pixels: {controller.screen_size[0]} x {controller.screen_size[1]}")
            print(f"Client origin: {controller.offset_x}, {controller.offset_y}")
        else:
            print("Game window: not open. Pluto will launch it and log in when started.")
        return 0
    if not ctypes.windll.shell32.IsUserAnAdmin():
        # UAC changes the working directory. Preserve the selected file as an absolute path.
        elevated_args = [str(config_path)]
        if args.low_job:
            elevated_args.append("--low-job")
        if args.avoid_pit:
            elevated_args.append("--avoid-pit")
        return request_elevation(elevated_args)
    try:
        PlutoRunner(settings, low_job=args.low_job, avoid_pit=args.avoid_pit).run()
    except StopMacro as exc:
        log_event(str(exc))
    except (KeyboardInterrupt, pag.FailSafeException):
        log_event("Pluto stopped by user or mouse failsafe.")
    except (OSError, RuntimeError) as exc:
        log_event(f"Cannot run Pluto: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    configure_event_log()
    raise SystemExit(main())
    
