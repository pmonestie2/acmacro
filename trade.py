import threading
import argparse
from ac import AcController, PLAYER
from chatproc import reset_chat, stop_chat
from player import load_player_config
from gui import key_press
from objects import PRASAD_TRADE_OBJ
from util import log, log_event, configure_event_log
from watchdog import RestartException, StopMacro, run_every


class TradeRunner:
    def __init__(self, controller: AcController = None) -> None:
        self.controller = controller

    def _test(self, obj, **kwargs) -> bool:
        return self.controller.test_obj(obj, **kwargs)

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

    def run(self, player_settings):
        PLAYER.configure(player_settings)
        log_event(f"Trade macro starting for player {PLAYER.toon_name}.")
        reset_chat(PLAYER.toon_name)
        chat_event = run_every(20, lambda: self.controller.check_chat_response() if self.controller else None)
        try:
            self._run_loop()
        finally:
            chat_event.set()
            stop_chat()
            if self.controller:
                self.controller.terminate()
            chat_event.join()
            log_event("Trade macro shutdown complete.")

    def _run_loop(self):
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
    configure_event_log()
    parser = argparse.ArgumentParser(description="Earth & Beyond trade macro.")
    parser.add_argument("player_config", help="Player settings file.")
    args = parser.parse_args()
    try:
        settings = load_player_config(args.player_config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    try:
        TradeRunner().run(settings)
    except StopMacro as exc:
        log_event(str(exc))
