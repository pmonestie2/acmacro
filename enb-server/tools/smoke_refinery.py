"""Disposable native-protocol refinery: single/stack, cancellation and restart.

Uses the real Magnetite five-ore recipe at ECS Dauntless. No graphical client,
private credentials or production game is used by this check.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
from typing import TypedDict, cast

from build import ROOT
from smoke_support import start_server
from smoke_test import connect
from start_proxy import registered, sandbox_address
from enb_protocol.session import SpaceSession
from enb_protocol.wire import encode_udp


class CargoItem(TypedDict):
    template: int
    quantity: int
    quality: float
    structure: float


class InventorySnapshot(TypedDict):
    cargo: list[CargoItem | None]
    equipped: list[CargoItem | None]
    credits: int
    refine_job: dict[str, object] | None
    trade_xp: int


def exercise(binary: Path, host: str) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-refinery-') as temporary:
        folder = Path(temporary)
        state, log = folder/'state', folder/'server.log'
        subprocess.run([str(binary), '--create-account', 'local', '--character', 'SandboxPilot',
            '--state-dir', str(state)], input='local\n', text=True, capture_output=True, check=True)
        (state/'player.json').write_text(json.dumps({'version': 1, 'sector': 10761,
            'position': [0., 0., 0.], 'home_station': 10761}))
        cargo: list[CargoItem | None] = [None]*22
        cargo[0] = CargoItem(template=2845, quantity=10, quality=1., structure=1.)
        cargo[1] = CargoItem(template=2845, quantity=5, quality=1., structure=1.)
        equipped: list[CargoItem | None] = [None]*20
        for item in json.loads((ROOT/'data/je_loadout.json').read_text()):
            equipped[item['slot']] = CargoItem(template=item['template'], quantity=1, quality=1., structure=1.)
        (state/'inventory.json').write_text(json.dumps({'cargo': cargo, 'equipped': equipped, 'credits': 10000}))
        process: subprocess.Popen[bytes] | None = None
        session: SpaceSession | None = None

        def inventory() -> InventorySnapshot:
            return cast(InventorySnapshot, json.loads((state/'inventory.json').read_text()))

        def count(template: int) -> int:
            return sum(item['quantity'] for item in inventory()['cargo'] if item and item['template'] == template)

        def wait(until: Callable[[], bool], seconds: float = 8.) -> None:
            assert session is not None
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                session.poll()
                if until(): return
            raise TimeoutError('Refinery condition was not confirmed')

        def request(opcode: int, value: int) -> None:
            assert session is not None
            session.send(opcode, struct.pack('>II', session.ticket.player_id | 0x80000000, value))

        try:
            process = start_server(binary, host, state, ROOT/'data/asteroid_belt_alpha.json', log)
            session = connect(host, allow_station=True)
            endpoint = session.sector.getsockname()
            request(0x79, 4)
            request(0x7c, 3381)
            request(0x7e, 2)
            wait(lambda: inventory().get('refine_job') is not None)
            assert inventory()['credits'] == 10000 and count(2845) == 15 and count(3381) == 0
            request(0x7e, 0)
            wait(lambda: inventory().get('refine_job') is None)
            assert inventory()['credits'] == 10000 and count(2845) == 15
            print('PASS: real native terminal/recipe/job packets; timer reserves work; closing cancels without charge', flush=True)
            request(0x79, 4)
            request(0x7c, 3381)
            body = struct.pack('>II', session.ticket.player_id | 0x80000000, 2)
            repeated = encode_udp(0x7e, session.ticket.player_id, body, session.sequence)
            session.send(0x7e, body)
            wait(lambda: inventory().get('refine_job') is not None)
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            process = start_server(binary, host, state, ROOT/'data/asteroid_belt_alpha.json', log)
            wait(lambda: inventory().get('refine_job') is None and count(3381) == 1)
            assert session.sector.getsockname() == endpoint
            assert inventory()['credits'] == 9850 and inventory()['trade_xp'] == 50 and count(2845) == 10
            session.sector.send(repeated)
            request(0x7c, 3381)
            request(0x7e, 3)
            wait(lambda: inventory().get('refine_job') is not None)
            wait(lambda: inventory().get('refine_job') is None and count(3381) == 3)
            assert inventory()['credits'] == 9550 and inventory()['trade_xp'] == 150 and count(2845) == 0
            print('PASS: pending single survives replacement on same UDP socket; stack consumes exact repeated components and awards matching credits/XP', flush=True)
            complete = inventory()
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            process = start_server(binary, host, state, ROOT/'data/asteroid_belt_alpha.json', log)
            session.sector.send(repeated)
            request(0x7e, 2) # Missing ore cannot create another item or debit credits.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline: session.poll()
            assert inventory() == complete
            print('PASS: completed job and duplicate old datagram do not award again; missing materials leave inventory unchanged', flush=True)
        except BaseException:
            print(log.read_text(errors='replace'))
            raise
        finally:
            if session is not None: session.close()
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT/'target/debug'/('enb-server.exe' if os.name == 'nt' else 'enb-server'))
    parser.add_argument('--server', default='127.0.0.10')
    args = parser.parse_args()
    host = sandbox_address(args.server)
    if registered(host): raise SystemExit('Use an unused disposable server address')
    exercise(args.binary.resolve(), host)


if __name__ == '__main__': main()
