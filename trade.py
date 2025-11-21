import threading
from ac import AcController, PLAYER
from macro_context import key_press
from objects import PRASAD_TRADE_OBJ
from util import log
from watchdog import RestartException, run_every


class TradeRunner:
    def __init__(self, controller: AcController) -> None:
        self.controller = controller

    def _test(self, obj, **kwargs) -> bool:
        return obj.test(offset=(self.controller.offset_x, self.controller.offset_y), **kwargs)

    def all_somer(self):
        self.controller._yield(5)
        self.controller.somer1_2()
        self.controller.somer2_3()
        self.controller.somer3_vendor()
        self.controller.somer3_2()
        self.controller.somer2_1()
        self.controller.somer1_space()
        PLAYER.current_dest = "prasadS"

    def all_prasad(self):
        self.controller._yield(5)
        self.controller.prasad1_2()
        self.controller.prasad2_3()
        self.controller.prasad3_vendor()
        self.controller.prasad3_2()
        self.controller.prasad2_1()
        self.controller.prasad1_space()
        PLAYER.current_dest = "somerS"

    def find_dir(self):
        key_press("m")
        self.controller._yield(0.1)
        key_press("i")
        self.controller._yield(0.5)
        if self._test(PRASAD_TRADE_OBJ):
            PLAYER.current_dest = "prasadS"
        else:
            PLAYER.current_dest = "somerS"
        log(f"found dir to {PLAYER.current_dest}")

    def route(self):
        while True:
            log(f"ROUTE {PLAYER.current_position} {PLAYER.current_dest}")
            dest = PLAYER.current_dest
            if PLAYER.current_position == "somerS":
                self.all_somer()
                PLAYER.current_dest = "prasadS"
            elif PLAYER.current_position == "prasadS":
                self.all_prasad()
                PLAYER.current_dest = "somerS"
            elif dest in ("somerS", "prasadS"):
                self.controller.setup_kranken()
                path = self.controller.shortest_path(PLAYER.current_position, dest)
                log(f"travel path: {path}")
                for p in path[1:]:
                    self.controller.travel_one(PLAYER.current_position, p)
            else:
                log(f"Unknown pos destination {PLAYER.current_position} {PLAYER.current_dest}")
            self.controller._yield(2)

    def run(self):
        PLAYER.toon_name = "lightt"
        PLAYER.toon_position = 2
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
    TradeRunner().run()
