"""Run one character's fast packet vendor route using a Pluto player config."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from ac import AcController
from chatproc import require_admin
from config import WINDOW_TITLE
from player import PLAYER, PlayerSettings, load_player_config
from protocol.vendor_trading import ROUTES, VendorPrivateMessage, VendorSession, VendorTrader
from util import configure_event_log, log_event
from watchdog import StopMacro


class VendorRunner:
    def __init__(self, settings: PlayerSettings) -> None:
        self.settings = settings

    def run(self) -> None:
        settings = self.settings
        PLAYER.configure(settings)
        configure_event_log(Path(__file__).parent / f'macro_{settings.name}.log', player=settings.name)
        require_admin()
        controller: AcController | None = None
        try:
            log_event(f'Vendor macro started for {settings.name}; one character, shared game installation')
            controller = AcController(WINDOW_TITLE, settings=settings, shared_install=True)
            controller.station_lock = False
            if controller.window is None or controller.startup_popup:
                controller.relogin(initial=True)
            instance = controller.instance
            if instance is None:
                raise RuntimeError('Vendor macro requires a configured game instance')
            session = VendorSession.from_instance(instance)
            VendorTrader(controller, session, ROUTES[settings.vendor_route],
                         settings.vendor_quantity, settings.vendor_cycles).run()
        except VendorPrivateMessage as error:
            if controller is not None:
                log_event(f'Vendor private message from {error.sender}: logging off this character and peer instances')
                self.logoff_instances(controller)
            raise
        finally:
            if controller is not None:
                try:
                    controller.terminate()
                finally:
                    controller.close()
            log_event(f'Vendor macro stopped for {settings.name}')

    def logoff_instances(self, controller: AcController) -> None:
        # VendorTrader has already closed its capture and inventory handles.
        # Notify peers before closing this game's pinned client/proxy session.
        controller.terminate()
        try:
            if controller.focus is not None:
                for toon, error in controller.focus.broadcast_logoff():
                    log_event(f'Vendor PM logoff to {toon}: '
                              f'{"queued" if error is None else "failed: " + error}')
        except Exception as error:
            log_event(f'Vendor PM peer logoff failed: {type(error).__name__}: {error}')
        finally:
            if controller.instance is None:
                raise RuntimeError('Vendor PM logoff requires an owned game instance')
            controller.instance.stop()
            controller.window = None
            log_event(f'Vendor PM logoff complete for {self.settings.name}: owned client and proxy stopped')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('player_config', type=Path)
    parser.add_argument('--slots', type=int, help='Maximum items bought per round (1-40; config default: 22)')
    parser.add_argument('--check', action='store_true', help='Validate config without starting a game')
    args = parser.parse_args(argv)
    try:
        settings = load_player_config(args.player_config)
        if args.slots is not None:
            if not 1 <= args.slots <= 40:
                raise ValueError('--slots must be between 1 and 40')
            settings = replace(settings, vendor_quantity=args.slots)
        if args.check:
            print(f'{settings.name}: vendor_route={settings.vendor_route}, '
                  f'quantity={settings.vendor_quantity}, cycles={settings.vendor_cycles or "continuous"}; '
                  'one character per process; shared install')
            return 0
        VendorRunner(settings).run()
        return 0
    except (KeyboardInterrupt, InterruptedError, StopMacro) as error:
        log_event(f'Vendor stopped: {type(error).__name__}: {error}')
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        log_event(f'Vendor failed: {type(error).__name__}: {error}; no transfer retry')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
