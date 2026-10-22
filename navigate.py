"""One-time cooperative navigation of an already running game."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback

from ac import AcController
from player import PLAYER, load_player_config
from navigation_route import CapturedNavigator
from watchdog import GameUnresponsive, GameWindowLost
from watchdog import run_every
from chatproc import reset_chat, stop_chat
import chat_audit

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('destination')
    parser.add_argument('--restart-game', action='store_true', help='Restart only the configured game before routing')
    parser.add_argument('--resume-gate', help='Finish an already submitted gate approach without sending another route')
    args = parser.parse_args()
    settings = load_player_config(args.config)
    folder = ROOT / 'diagnostics'
    result = folder / f'navigate_{settings.name}.json'
    output = (folder / f'navigate_{settings.name}.log').open('a', buffering=1)
    previous_streams = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = output
    controller = None
    chat_event = None
    try:
        PLAYER.configure(settings)
        chat_audit.CHAT_INTERACTION_LOG = ROOT / f'chat_interactions_{settings.name}.log'
        controller = AcController(f'ENB_{settings.name}', settings=settings, singleton=False)
        # Automatic slot selection configures the actual per-client chat path.
        # Take the history cursor only afterward, or slot 2 replays old PMs.
        reset_chat(settings.name)
        if args.restart_game:
            controller.relogin(initial=True)
        if not controller.instance.valid():
            raise RuntimeError('Game is not running; navigation will not launch or log in')
        controller._terminated = False
        chat_event = run_every(20, controller.check_chat_response)
        controller.init_read_chat()
        print(f'PM replies armed for {settings.name}; cooperative focus enabled',flush=True)
        for attempt in range(4):
            try:
                position = controller.get_current_position()
                if args.resume_gate and position != args.resume_gate:
                    from objects import GATE_OBJ
                    if not controller.wait_gate_objects([GATE_OBJ],timeout=240,
                            on_found=lambda: controller.lclick(1236,515)):
                        raise GameUnresponsive('Resumed gate approach did not become available')
                    if not controller.transition_space(expected=args.resume_gate):
                        raise GameUnresponsive('Resumed gate transition did not finish')
                    position = controller.get_current_position()
                args.resume_gate = None
                if position == 'earthS':
                    controller.earth1_space()
                    position = controller.get_current_position()
                elif position == 'charonS':
                    controller.charon_space()
                    position = controller.get_current_position()
                if not position:
                    raise GameUnresponsive('Cannot identify current position after loading/transition')
                result.write_text(json.dumps({'state':'running','from':position,'destination':args.destination,'time':time.time()}))
                actual = CapturedNavigator(controller).route(position,args.destination)
                if args.destination != 'orsini':
                    actual = controller.get_current_position()
                break
            except (GameUnresponsive, GameWindowLost) as error:
                # Recover a genuinely stuck loading/transition screen, not a
                # rejected unsafe endpoint or uncertain native command.
                if attempt == 3 or not any(word in str(error).lower() for word in ('transition','loading','undocking','window disappeared')):
                    raise
                print(f'Restarting owned game after stuck transition: {error}',flush=True)
                controller.relogin()
                controller._terminated = False
                controller.init_read_chat()
        if actual != args.destination:
            raise RuntimeError(f'Unexpected arrival: {actual}')
        controller.screenshot().save(folder / f'navigate_{settings.name}_arrival.png')
        result.write_text(json.dumps({'state':'arrived','position':actual,'time':time.time()}))
    except BaseException as error:
        result.write_text(json.dumps({'state':'error','error':str(error),'time':time.time()}))
        traceback.print_exc()
        raise
    finally:
        if chat_event:
            chat_event.set()
        stop_chat()
        if chat_event:
            chat_event.join()
        if controller:
            controller.close()
        sys.stdout, sys.stderr = previous_streams
        output.close()


if __name__ == '__main__':
    main()
