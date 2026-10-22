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
from chatproc import require_admin, reset_chat, stop_chat, suspend_chat, configure_chat_path
from game_instance import GameInstance
import chat_audit
from chat_reply import codex_executable
from config import CHAT_LOG_PATH, CHAT_PROMPT_PATH, CHAT_INTERACTION_LOG, WINDOW_TITLE, net7_proxy
from player import load_player_config
import pyautogui as pag
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
from job_policy import JobPolicy
from job_sender import JobPacketUnavailable
from packet_jobs import PacketJobPicker
from background_jobs import BackgroundJobPickup
from watchdog import GameWindowLost, GameUnresponsive, RestartException, StopMacro, run_every


class PlutoRunner:
    def __init__(self, player_settings=None, low_job=None, avoid_pit=None, singleton=False,
                 station_lock=False) -> None:
        self.controller:Optional[AcController] = None
        self.player_settings = player_settings
        self.singleton = singleton
        self.station_lock = station_lock
        self.low_job = low_job if low_job is not None else bool(player_settings and player_settings.low_job)
        self._low_jobs_delivered = False
        self.avoid_pit = avoid_pit if avoid_pit is not None else bool(player_settings and player_settings.avoid_pit)

    @property
    def job_policy(self):
        return JobPolicy.from_settings(self.player_settings, self.low_job, self.avoid_pit)

    @property
    def uranus_jobs(self):
        return 'uranus' in self.job_policy.destinations

    @property
    def charon_jobs(self):
        return bool(self.job_policy.destinations & {'charon', 'monster_pit'})

    def _test(self, obj, **kwargs) -> bool:
        return self.controller.test_obj(obj, **kwargs)

    def walk_job(self):
        with self.controller.with_focus("job terminal approach"):
            self.controller.sbreset(739690)
            self.controller.key_press("a", 4)
            self.controller.lclick(1102, 88)
        self.controller._yield(3)
        self.controller.lclick(1258, 250)
        self.controller._yield(2)

    def sort_job(self):
        with self.controller.with_focus("job sorting"):
            self.controller.lclick(95, 342)
            self.controller.lclick(607, 295)
            if self.charon_jobs:
                self.controller.lclick(607, 295)

    def open_job(self):
        deadline = self.controller.polling_time() + 30
        while True:
            with self.controller.with_focus("job panel opening"):
                if self._test(JOB_OPEN_OBJ):
                    return
                if self.controller.polling_time() >= deadline:
                    raise GameUnresponsive("Job panel did not open within 30 seconds")
                self.controller.lclick(1052, 52)
            self.controller._yield(1.5)

    def is_good_low_job(self) -> bool:
        sc = self.controller.screenshot()
        self._job_screenshot = sc
        return (
            self._test(LOWJOB1_OBJ, screenshot=sc)
            or self._test(LOWJOB2_OBJ, screenshot=sc)
            or self._test(LOWJOB3_OBJ, screenshot=sc)
            or self._test(LOWJOB4_OBJ, screenshot=sc)
        )

    def is_good_job(self) -> bool:
        sc = self.controller.screenshot()
        self._job_screenshot = sc
        return self._test(JOB1_OBJ, screenshot=sc) or self._test(JOB2_OBJ, screenshot=sc)

    def is_pit_job(self) -> bool:
        sc = self._job_screenshot
        return (
            self._test(CHARON_PIT_OBJ, screenshot=sc)
            or self._test(CHARON_PIT1_OBJ, screenshot=sc)
            or self._test(CHARON_PIT2_OBJ, screenshot=sc)
        )

    def close_job(self):
        deadline = self.controller.polling_time() + 30
        while True:
            with self.controller.with_focus("job panel closing"):
                if not self._test(JOB_OPEN_OBJ):
                    break
                if self.controller.polling_time() >= deadline:
                    raise GameUnresponsive("Job panel did not close within 30 seconds")
                self.controller.lclick(1255, 254)
            self.controller._yield(2)
        self.controller._yield(2)

    def pick_job(self):
        mode = getattr(self.player_settings, 'job_pickup', 'ui')
        if mode == 'packet':
            PLAYER.set_pos('earthJOB')
            log_event('Job pickup mode=packet; UI alternative is job_pickup=ui')
            try:
                return PacketJobPicker(self.controller, self.job_policy, PLAYER.mission_count).pick()
            except JobPacketUnavailable as error:
                raise StopMacro(f'Packet job pickup unavailable: {error}; stopped with UI pickup disabled') from error
        return self._pick_job_ui()

    def is_configured_job(self):
        if self.uranus_jobs and self.is_good_low_job():
            return True
        if self.charon_jobs and self.is_good_job():
            destination = 'monster_pit' if self.is_pit_job() else 'charon'
            return destination in self.job_policy.destinations
        return False

    def _pick_job_ui(self):
        PLAYER.set_pos("earthJOB")
        start = self.controller.polling_time()

        self.open_job()
        self.sort_job()

        mc = self.controller.count_mission()
        mc_real = mc
        log_event(f"Job pickup started: count={mc}, target={PLAYER.mission_count}, low_job={self.low_job}, avoid_pit={self.avoid_pit}")
        batch = 0

        while mc < PLAYER.mission_count:
            batch += 1
            self.controller.repeat_vendor_click(1256, 326, 45, 0.1)

            if getattr(self.player_settings, 'job_destinations', 'auto') != 'auto':
                self.pickBatchJob(self.is_configured_job)
            elif not self.low_job:
                self.pickBatchJob(self.is_good_job)
            else:
                self.pickBatchJob(self.is_good_low_job)

            self.controller.close_map()
            self.open_job()
            # The level header toggles direction. Recounting must preserve
            # the order selected once at the start of this job pickup.
            mc_new = self.controller.count_mission()
            log_event(f"Job pickup batch {batch}: before={mc}, after={mc_new}, change={mc_new - mc}, target={PLAYER.mission_count}")
            self.controller.close_map()

            if mc_new > mc:
                PLAYER.set_pos(f"earthJOB_jc_{mc_new}")

            mc = mc_new
            mc_real = mc

            elapsed = self.controller.polling_time() - start
            log_event(f"Job pickup elapsed={int(elapsed)}s, count={mc_real}/{PLAYER.mission_count}")

            if elapsed > 180:
                PLAYER.set_pos("earthJOB2")
            if elapsed > 450:
                log_event(f"Job pickup timed out after {int(elapsed)}s: count={mc_real}, target={PLAYER.mission_count}")
                break

        log_event(f"Job pickup finished: count={mc_real}, target={PLAYER.mission_count}, target_met={mc_real >= PLAYER.mission_count}, batches={batch}")

    def pickBatchJob(self, check_job_fn: Callable[[], bool]):
        pc_start = 327
        matched = accepted = pit_skipped = 0
        for page in range(2):
            # A small batch amortizes foreground/peer handoffs while keeping
            # every row's select/read/accept sequence together. Four rows take
            # about 9-13s; the controller still enforces its normal 20s limit.
            for start in range(0, 10, 4):
                with self.controller.with_focus("job rows"):
                    for row in range(start, min(start + 4, 10)):
                        self.controller.lclick(290, pc_start + row * 20)
                        self.controller._yield(2)
                        if check_job_fn():
                            matched += 1
                            if not self.avoid_pit or not self.is_pit_job():
                                self.controller.lclick(1089, 706)
                                self.controller._yield(1)
                                accepted += 1
                            else:
                                pit_skipped += 1

            with self.controller.with_focus("job page navigation"):
                self.controller.lclick(1254, 513)
                if page < 1:
                    for _ in range(10):
                        self.controller.lclick(1254, 513)
                        self.controller._yield(0.3)
        log_event(f"Job scan: rows=20, matches={matched}, pit_skipped={pit_skipped}, "
                  f"accept_clicks={accepted}; accepted count verified by subsequent recount")

    def earth_pluto(self):
        self.controller.setup_kranken()
        PLAYER.current_position = "earth"
        PLAYER.current_dest = self.outbound_destination()
        path = self.controller.shortest_path(PLAYER.current_position, PLAYER.current_dest)
        log(f"earth_pluto path: {path}")
        for p in path[1:]:
            self.controller.travel_one(PLAYER.current_position, p)

    def pluto_earthS(self):
        self.controller.setup_kranken()
        PLAYER.current_position = "pluto"
        PLAYER.current_dest = "earthS"
        path = self.controller.shortest_path(PLAYER.current_position, "earthS")
        log(f"pluto_earth path: {path}")
        for p in path[1:]:
            self.controller.travel_one(PLAYER.current_position, p)

    def find_dir(self):
        with self.controller.with_focus("route direction check"):
            self.controller.key_press("m")
            self.controller._yield(0.1)
            self.controller.key_press("i")
            self.controller._yield(0.5)
            if self._test(PLUTO_TRADE_OBJ):
                PLAYER.current_dest = "earthS"
            else:
                PLAYER.current_dest = self.outbound_destination()
        log(f"found dir to {PLAYER.current_dest}")

    def outbound_destination(self):
        if PLAYER.type_ == "new":
            return "charonS"
        return "uranus" if self.uranus_jobs else "pluto"

    def route(self):
        while True:
            log_event(f"ROUTE {PLAYER.current_position} {PLAYER.current_dest}")
            if (self.uranus_jobs and PLAYER.type_ != "new" and not self._low_jobs_delivered
                    and PLAYER.current_dest in ("pluto", "charonS")):
                PLAYER.current_dest = "uranus"
            self.dump_low_jobs()
            dest = PLAYER.current_dest
            if PLAYER.current_position == "earthS":
                self.all_earth()
                PLAYER.current_dest = self.outbound_destination()
            elif PLAYER.current_position == "charonS":
                self.all_charon()
                PLAYER.current_dest = "earthS"
            elif dest in ("pluto", "earthS", "uranus") or ((PLAYER.type_ == "new" or not self.charon_jobs) and dest == "charonS"):
                self.controller.setup_kranken()
                path = self.controller.shortest_path(PLAYER.current_position, dest)
                log_event(f"travel path: {path}")
                for p in path[1:]:
                    self.controller.travel_one(PLAYER.current_position, p)
                    self.dump_low_jobs()
                if dest == 'uranus' and self.charon_jobs:
                    PLAYER.current_dest = 'pluto'
                elif dest in ("pluto", "uranus"):
                    PLAYER.current_dest = "charonS"
            elif dest == "charonS":
                self.pluto_dump()
                PLAYER.current_dest = "earthS"
            else:
                log(f"Unknown pos destination {PLAYER.current_position} {PLAYER.current_dest}")
            self.controller._yield(2)

    def dump_low_jobs(self):
        if self.uranus_jobs and not self._low_jobs_delivered and PLAYER.current_position == "uranus":
            log_event("LOW JOB: completing deliveries at Ariel and Puck")
            self.controller.gate_sat(331, 620, approach_timeout=120, strict=True, skip_completed=True)
            self.controller.gate_sat(329, 490, approach_timeout=120, strict=True, skip_completed=True)
            self._low_jobs_delivered = True

    def pluto_dump(self):
        self.controller.gate_sat(427, 617, fast=True)

        if PLAYER.type_ == "te":
            self.controller.key_press("5")
            self.controller._yield(2)

        if 'monster_pit' not in self.job_policy.destinations:
            self.controller.travel_one('pluto', 'charonS')
            return

        found_monster = False
        for _ in range(3):
            self.controller.key_press("x")
            for _ in range(5):
                self.controller.key_press("c")
                self.controller._yield(0.5)
                if self.controller.wait_objects([CHARON_MONSTER_OBJ], yield_time=1, timeout=2):
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
                self.controller.key_press("q")
                self.controller._yield(5)
                # Original lines 263–275: satellite detection through final q.
                # One shared focus lock for this entire block, at most 45s.
                # Reserve 15s for deploy/stop/q/wait/q after detection. Singleton
                # bypasses the special lock/budget and keeps its original wait.
                with self.controller.exclusive_focus("Charon satellite deployment", timeout=45):
                    log("wait for sat obj")
                    sat_timeout = self.controller.exclusive_remaining(reserve=15, default=45)
                    found_sat = self.controller.wait_objects([SAT_OBJ], timeout=sat_timeout,
                        yield_fn=lambda: self.controller.key_press("a", 1.2), yield_time=3,
                        final_check=True)
                    if not found_sat:
                        raise GameUnresponsive("Satellite panel did not appear within deployment budget")
                    self.controller._yield(0.5)

                    for _ in range(7):
                        self.controller.lclick(1238, 516)
                        self.controller._yield(0.5)

                    self.controller.key_press("s")
                    self.controller._yield(0.3)
                    self.controller.key_press("q")
                    self.controller._yield(7.0)
                    self.controller.key_press("q")
        self.controller.travel_one("pluto", "charonS")

    def all_earth(self):
        self._low_jobs_delivered = False
        packet = PLAYER.type_ != 'new' and getattr(self.player_settings, 'job_pickup', 'ui') == 'packet'
        worker = None
        self.controller._yield(2)
        self.controller.close_map()
        self.controller._yield(2)
        if packet:
            self.controller.start_packet_capture()
            count = self.controller.count_mission()
            if self.controller.job_monitor is None:
                raise StopMacro('Background packet job pickup requires owned packet capture; UI pickup disabled')
            worker = BackgroundJobPickup(self.controller, self.job_policy, PLAYER.mission_count, count)
        try:
            if worker:
                worker.start()
            self.controller.earth1_2()
            self.controller.earth2_3()
            if worker:
                with worker.vendor():
                    self.controller.earth3_vendor()
                worker.allow_accepts()
            else:
                self.controller.earth3_vendor()
            log("SELLALL IN EARTH")
            self.controller.earth3_2()
            if worker:
                worker.finish()
            elif PLAYER.type_ != "new":
                self.controller.earth2_j()
                self.walk_job()
                self.pick_job()
                self.close_job()
                self.controller.earthj_2()
            self.controller.earth2_1()
            PLAYER.current_dest = self.outbound_destination()
            self.controller.earth1_space()
        finally:
            if worker:
                worker.close()

    def all_charon(self):
        self.controller._yield(5)

        if PLAYER.type_ != "new":
            mc = self.controller.count_mission()
            if mc > 1:
                self.controller.clear_jobs()

        self.controller.close_map()
        if PLAYER.type_ != "new":
            self.controller.empty_inventory()
            self.controller.close_map()

        self.controller.charon1_2()
        self.controller.charon2_3()
        self.controller.charon3_vendor()

        log("BUY IN CHARON" if PLAYER.type_ == "new" else "SELLALL IN CHARON")

        self.controller.charon3_2()
        self.controller.charon2_1()
        PLAYER.current_dest = "earthS"
        self.controller.charon_space()

    def run(self):
        if self.player_settings is None:
            raise ValueError("Pass a player configuration file to pluto.py")
        PLAYER.configure(self.player_settings)
        instance = GameInstance(self.player_settings)
        configure_chat_path(instance.chat_log)
        configure_event_log(Path(__file__).parent / f"macro_{PLAYER.toon_name}.log")
        chat_audit.CHAT_INTERACTION_LOG = Path(__file__).parent / f"chat_interactions_{PLAYER.toon_name}.log"
        log_event(f"Pluto starting for player {PLAYER.toon_name} (type {PLAYER.type_}).")
        log_event(f"Focus mode: {'SINGLETON (--singleton); cooperative handoffs disabled' if self.singleton else 'COOPERATIVE; peer handoffs enabled'}")
        log_event(f"Same-station lock: {'ENABLED' if self.station_lock else 'DISABLED'} (all stations)")
        require_admin()
        self.chat_event = None
        try:
            log_event("Starting in 4 seconds; launch the other macro now.")
            time.sleep(4)
            self._run_loop()
        except (StopMacro, pag.FailSafeException):
            raise
        except Exception as exc:
            if self.controller:
                capture_failure(exc, controller=self.controller)
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
            if self.controller:
                self.controller.close()
            log_event("Pluto shutdown complete; background chat polling stopped.")

    def _run_loop(self):
        self.controller = AcController(WINDOW_TITLE, settings=self.player_settings, singleton=self.singleton)
        self.controller.station_lock = self.station_lock
        self.controller.start_packet_capture()
        if self.uranus_jobs:
            self.controller.departure_stops.add("uranus")
        # The controller chooses its automatic game slot and chat.log path.
        # Ignore history from that file, not from the default installation.
        reset_chat(PLAYER.toon_name)
        self.chat_event = run_every(20, self.controller.check_chat_response)
        initial = not self.controller.window or self.controller.startup_popup is True
        recover = False
        while True:
            try:
                log(f"start - thread:{threading.current_thread().name}")
                self.controller.init_window()
                if recover or not self.controller.window or self.controller.startup_popup is True:
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
                capture_failure(exc, controller=self.controller)
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
    parser.add_argument("--singleton", action="store_true", help="Run as the only macro, retaining focus during waits.")
    parser.add_argument("--station-lock", "--earth-station-lock", dest="station_lock", action="store_true",
                        help="Wait when another toon occupies the same station, at any station (disabled by default).")
    parser.add_argument("--low-job", action="store_true", default=None, help="Override config to select low-level jobs.")
    parser.add_argument("--avoid-pit", action="store_true", default=None, help="Override config to skip Charon pit jobs.")
    args = parser.parse_args(argv)
    config_path = args.player_config.expanduser().resolve()
    try:
        settings = load_player_config(config_path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.check:
        instance = GameInstance(settings)
        print(f"Player config: {config_path}")
        print(f"Player: {settings.name}; type: {settings.player_type}; slot: {settings.character_slot}")
        print(f"Python: {sys.executable}")
        print(f"Net-7 proxy: {net7_proxy()}")
        print("Imports and dependencies: OK")
        print(f"Game client: {instance.client}")
        if instance.automatic:
            from config import GAME_CLIENTS
            print(f"Automatic client selection at startup: {', '.join(str(path) for path in GAME_CLIENTS)}")
        print(f"Window title: {instance.title}")
        print(f"Chat log: {instance.chat_log} (VSS reading requires administrator access)")
        print(f"Chat replies: {codex_executable()} (uses saved Codex login)")
        if not CHAT_PROMPT_PATH.read_text(encoding="utf-8-sig").strip():
            raise ValueError("chat_prompt.txt is empty")
        print(f"Editable chat prompt: {CHAT_PROMPT_PATH}")
        print(f"Interaction log: {Path(__file__).parent / f'chat_interactions_{settings.name}.log'}")
        controller = AcController(WINDOW_TITLE)
        controller.window = instance.discover(apply_layout=False) if ctypes.windll.shell32.IsUserAnAdmin() else None
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
        if args.singleton:
            elevated_args.append("--singleton")
        if args.station_lock:
            elevated_args.append("--station-lock")
        return request_elevation(elevated_args)
    try:
        PlutoRunner(settings, low_job=args.low_job, avoid_pit=args.avoid_pit, singleton=args.singleton,
                    station_lock=args.station_lock).run()
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
    
