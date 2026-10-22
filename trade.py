import threading
import time
import argparse
from ac import AcController, PLAYER
from chatproc import reset_chat, stop_chat, require_admin, configure_chat_path
from game_instance import GameInstance
from pathlib import Path
import chat_audit
from player import load_player_config
from objects import PRASAD_TRADE_OBJ
from util import log, log_event, configure_event_log
from watchdog import RestartException, StopMacro, GameWindowLost, GameUnresponsive, run_every


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
        self.controller.key_press("m")
        self.controller._yield(0.1)
        self.controller.key_press("i")
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

    def run(self, player_settings, singleton=False):
        require_admin()
        self.player_settings = player_settings
        self.singleton = singleton
        PLAYER.configure(player_settings)
        instance = GameInstance(player_settings)
        configure_chat_path(instance.chat_log)
        configure_event_log(Path(__file__).parent / f"macro_{PLAYER.toon_name}.log")
        chat_audit.CHAT_INTERACTION_LOG = Path(__file__).parent / f"chat_interactions_{PLAYER.toon_name}.log"
        log_event(f"Trade macro starting for player {PLAYER.toon_name}.")
        log_event(f"Focus mode: {'SINGLETON (--singleton); cooperative handoffs disabled' if self.singleton else 'COOPERATIVE; peer handoffs enabled'}")
        log_event("Starting in 4 seconds; launch the other macro now.")
        time.sleep(4)
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
            if self.controller:
                self.controller.close()
            log_event("Trade macro shutdown complete.")

    def _run_loop(self):
        if not self.controller:
            self.controller = AcController("Earth & Beyond", settings=self.player_settings, singleton=self.singleton)
        recover = False
        initial = not self.controller.window or self.controller.startup_popup is True
        while True:
            try:
                log(f"start - thread:{threading.current_thread().name}")
                if recover or not self.controller.window or self.controller.startup_popup is True:
                    self.controller.relogin(initial=initial)
                    recover = False
                initial = False
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
            except (GameWindowLost, GameUnresponsive) as error:
                self.controller.terminate()
                log_event(f"{error}; recovering owned game")
                recover, initial = True, False


if __name__ == "__main__":
    configure_event_log()
    parser = argparse.ArgumentParser(description="Earth & Beyond trade macro.")
    parser.add_argument("player_config", help="Player settings file.")
    parser.add_argument("--singleton", action="store_true", help="Run as the only macro, retaining focus during waits.")
    args = parser.parse_args()
    try:
        settings = load_player_config(args.player_config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    try:
        TradeRunner().run(settings, singleton=args.singleton)
    except StopMacro as exc:
        log_event(str(exc))
