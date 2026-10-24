"""Disposable native packets: analyzer escrow, learned recipe and manufacture.

Uses public Dragon's Horde recipe 1758 and its actual SQL components/cost.
Exercises cancellation, server replacement and duplicate/missing-input denial;
does not drive graphical clients or use production accounts.
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
    builder_name: str


class InventorySnapshot(TypedDict):
    cargo: list[CargoItem | None]
    production_target: list[CargoItem | None]
    credits: int
    craft_job: dict[str, object] | None
    known_formulas: list[int]
    trade_xp: int


def item(template: int, builder_name: str = '') -> CargoItem:
    return CargoItem(template=template, quantity=1, quality=1., structure=1., builder_name=builder_name)


def exercise(binary: Path, host: str) -> None:
    templates = {row['id']: row for row in json.loads((ROOT/'data/items.json').read_text())}
    product = 1758
    components: list[int] = templates[product]['components']
    cost: int = templates[product]['crafting']['cost']
    with tempfile.TemporaryDirectory(prefix='enb-manufacture-') as temporary:
        folder = Path(temporary)
        state, log = folder/'state', folder/'server.log'
        subprocess.run([str(binary), '--create-account', 'local', '--character', 'SandboxPilot',
            '--state-dir', str(state)], input='local\n', text=True, capture_output=True, check=True)
        (state/'player.json').write_text(json.dumps({'version': 1, 'sector': 10761,
            'position': [0., 0., 0.], 'home_station': 10761}))
        cargo: list[CargoItem | None] = [None]*22
        for index, template in enumerate([product, *components]): cargo[index] = item(template)
        equipped: list[CargoItem | None] = [None]*20
        for row in json.loads((ROOT/'data/je_loadout.json').read_text()): equipped[row['slot']] = item(row['template'])
        (state/'inventory.json').write_text(json.dumps({'cargo': cargo, 'equipped': equipped, 'credits': 1_000_000}))
        process: subprocess.Popen[bytes] | None = None
        session: SpaceSession | None = None

        def inventory() -> InventorySnapshot:
            return cast(InventorySnapshot, json.loads((state/'inventory.json').read_text()))

        def wait(until: Callable[[], bool], seconds: float = 8.) -> None:
            assert session is not None
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                session.poll()
                if until(): return
            raise TimeoutError('Manufacturing condition was not confirmed')

        def request(opcode: int, value: int) -> None:
            assert session is not None
            session.send(opcode, struct.pack('>II', session.ticket.player_id | 0x80000000, value))

        try:
            process = start_server(binary, host, state, ROOT/'data/asteroid_belt_alpha.json', log)
            session = connect(host, allow_station=True)
            endpoint = session.sector.getsockname()
            request(0x79, 1)
            request(0x7b, product)
            request(0x7e, 2)  # Naming an unknown recipe must not grant knowledge.
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline: session.poll()
            assert inventory().get('craft_job') is None and not inventory().get('known_formulas')
            request(0x79, 2)
            session.send(0x27, struct.pack('>6I', session.ticket.player_id, 1, 0, 12, 0, 1))
            wait(lambda: inventory()['production_target'][0] is not None)
            assert inventory()['cargo'][0] is None
            request(0x7e, 2)
            wait(lambda: inventory()['craft_job'] is not None)
            request(0x7e, 0)
            wait(lambda: inventory()['craft_job'] is None)
            assert inventory()['production_target'][0] == item(product) and inventory()['credits'] == 1_000_000
            print('PASS: unknown recipe denied; native drag owns analyzer sample; cancellation keeps sample and credits', flush=True)
            request(0x79, 2)
            body = struct.pack('>II', session.ticket.player_id | 0x80000000, 2)
            repeated = encode_udp(0x7e, session.ticket.player_id, body, session.sequence)
            session.send(0x7e, body)
            wait(lambda: inventory()['craft_job'] is not None)
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            process = start_server(binary, host, state, ROOT/'data/asteroid_belt_alpha.json', log)
            wait(lambda: inventory()['craft_job'] is None and product in inventory()['known_formulas'])
            assert session.sector.getsockname() == endpoint
            assert inventory()['production_target'][0] is None and inventory()['credits'] == 1_000_000 - cost
            session.sector.send(repeated)
            request(0x79, 1)
            request(0x7a, 110)
            request(0x7b, product)
            request(0x7e, 2)
            wait(lambda: inventory()['craft_job'] is not None)
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            process = start_server(binary, host, state, ROOT/'data/asteroid_belt_alpha.json', log)
            wait(lambda: inventory()['craft_job'] is None and inventory()['trade_xp'] == 50)
            assert inventory()['credits'] == 1_000_000 - 2*cost
            assert [owned for owned in inventory()['cargo'] if owned] == [item(product, 'SandboxPilot')]
            print('PASS: analysis and build each survive server replacement on same socket; exact SQL components/fees and one product/XP award', flush=True)
            complete = inventory()
            session.sector.send(repeated)
            request(0x7e, 2)  # Learned recipe still needs its consumed components.
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline: session.poll()
            assert inventory() == complete
            print('PASS: replay and missing components cannot debit or award again', flush=True)
            # Dismantling an owned crafted item freezes its recovery before replacement.
            request(0x79, 3)
            slot = next(index for index, owned in enumerate(inventory()['cargo']) if owned and owned['template'] == product)
            session.send(0x27, struct.pack('>6I', session.ticket.player_id, 1, slot, 12, 0, 1))
            wait(lambda: inventory()['production_target'][0] is not None)
            request(0x7e, 2)
            wait(lambda: inventory()['craft_job'] is not None)
            plan = inventory()['craft_job']
            assert plan is not None and plan['recovered_components'] == components
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            process = start_server(binary, host, state, ROOT/'data/asteroid_belt_alpha.json', log)
            wait(lambda: inventory()['craft_job'] is None and inventory()['production_target'][0] is None)
            assert sorted(owned['template'] for owned in inventory()['cargo'] if owned) == sorted(components)
            assert all(owned['builder_name'] == '' for owned in inventory()['cargo'] if owned)
            assert inventory()['credits'] == 1_000_000 - 3*cost and inventory()['trade_xp'] == 50
            print('PASS: crafted provenance persists; dismantling consumes owned item, returns frozen SQL components and fee across replacement', flush=True)
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
