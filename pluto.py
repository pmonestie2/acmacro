import threading
import time
from typing import Callable, Optional

import pyautogui as pag

from ac import AcController, PLAYER
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
from util import log
from watchdog import RestartException, run_every


class PlutoRunner:
    def __init__(self, low_job: bool = False, avoid_pit: bool = False) -> None:
        self.controller:Optional[AcController] = None
        self.low_job = low_job
        self.avoid_pit = avoid_pit

    def _test(self, obj, **kwargs) -> bool:
        return obj.test(offset=(self.controller.offset_x, self.controller.offset_y), **kwargs)

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
        sc = pag.screenshot()
        return (
            self._test(LOWJOB1_OBJ, screenshot=sc)
            or self._test(LOWJOB2_OBJ, screenshot=sc)
            or self._test(LOWJOB3_OBJ, screenshot=sc)
            or self._test(LOWJOB4_OBJ, screenshot=sc)
        )

    def is_good_job(self) -> bool:
        self.controller._yield(2)
        sc = pag.screenshot()
        return self._test(JOB1_OBJ, screenshot=sc) or self._test(JOB2_OBJ, screenshot=sc)

    def is_pit_job(self) -> bool:
        self.controller._yield(1)
        sc = pag.screenshot()
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

    def pickBatchJob(self, check_job_fn: Callable[[], None]):
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
        PLAYER.toon_name = "lightt"
        PLAYER.toon_position = 2
        PLAYER.mission_count = 6
        PLAYER.type_ = "te"
        run_every(20, AcController("Earth & Beyond").check_chat_response)
        while True:
            try:
                log(f"start - thread:{threading.current_thread().name}")
                if self.controller:
                    log("Terminate previous controller")
                    self.controller.terminate()
                self.controller = AcController("Earth & Beyond")
                self.controller.bring_to_front()
                self.controller._yield(7)
                self.controller.check_game_progress()
                self.controller.init_read_chat()
                self.controller.schedule_check_game_progress()
                self.find_dir()
                PLAYER.set_pos(self.controller.get_current_position())
                self.route()
            except RestartException as re:
                log(f"main caught RESTART exception: {re}")
                continue


if __name__ == "__main__":
    PlutoRunner().run()
    
