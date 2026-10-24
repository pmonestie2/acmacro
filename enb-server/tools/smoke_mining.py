"""Two disposable clients verify mining, shared claims and restart recovery.

Uses real public ore/item definitions in a reduced local fixture. It never drives
the graphical game, reads player credentials, or contacts the production server.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time

from build import ROOT
from smoke_multiplayer import pump
from smoke_support import start_server
from smoke_test import connect
from start_proxy import registered, sandbox_address
from enb_protocol.session import SpaceSession
from enb_protocol.wire import Frame


def exercise(binary: Path, host: str) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-mining-') as temporary:
        folder = Path(temporary)
        state, log = folder/'state', folder/'server.log'
        for name in ('alice', 'bob'):
            subprocess.run([str(binary), '--create-account', name, '--character', name.title(),
                '--state-dir', str(state)], input='example\n', text=True, capture_output=True, check=True)
        content = json.loads((ROOT/'data/asteroid_belt_alpha.json').read_text())
        node = next(node for node in content['resources'] if node['level'] == 6 and node['ore_items'])
        # Retain the real generated node identity, model and ore pool, but move
        # it near both starting ships and omit combat from this focused test.
        position = [0., 0., 0.]
        node['position'] = [100., 0., 0.]
        content['resources'], content['mobs'] = [node], []
        content_file = folder/'sector.json'
        content_file.write_text(json.dumps(content))
        for directory in (state, state/'accounts/bob'):
            (directory/'player.json').write_text(json.dumps({'version': 1, 'sector': content['id'],
                'position': position, 'home_station': 10761}))
            equipped: list[dict[str, int | float] | None] = [None]*20
            for item in json.loads((ROOT/'data/je_loadout.json').read_text()):
                equipped[item['slot']] = {'template': 3340 if item['slot'] == 1 else item['template'],
                                         'quantity': 1, 'quality': 1., 'structure': 1.}
            (directory/'inventory.json').write_text(json.dumps({'cargo': [None]*22, 'equipped': equipped}))
        process: subprocess.Popen[bytes] | None = None
        sessions: list[SpaceSession] = []
        observed: list[tuple[int, Frame]] = []

        def seen(viewer: int, opcode: int) -> bool:
            return any(index == viewer and frame.opcode == opcode for index, frame in observed)

        def harvested() -> bool:
            inventory = json.loads((state/'inventory.json').read_text())
            return str(node['id']) in inventory.get('harvests', {})

        try:
            process = start_server(binary, host, state, content_file, log)
            sessions = [connect(host, name, 'example', name.title()) for name in ('alice', 'bob')]
            for session in sessions:
                owner = session.ticket.player_id
                session.send(0x17, struct.pack('<II', owner, node['id']))
                session.send(0x2c, struct.pack('<4I', owner, 17, node['id'], 0))
            pump(sessions, lambda: seen(0, 0x66) and seen(1, 0x66), 5, observed)
            observed.clear()
            sessions[0].send(0x27, struct.pack('>6I', sessions[0].ticket.player_id, 18, 0, 0, 0, 0))
            pump(sessions, lambda: seen(0, 0x2012) and seen(1, 0x2012), 5, observed)
            sessions[1].send(0x27, struct.pack('>6I', sessions[1].ticket.player_id, 18, 0, 0, 0, 0))
            pump(sessions, lambda: any(index == 1 and frame.opcode == 0x1d and b'already being prospected' in frame.body.lower()
                 for index, frame in observed), 3, observed)
            assert not harvested()
            print('PASS: native prospect window opens; both clients see beam; concurrent claim rejected', flush=True)
            endpoints = [session.sector.getsockname() for session in sessions]
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            observed.clear()
            process = start_server(binary, host, state, content_file, log)
            pump(sessions, lambda: harvested() and seen(0, 0x2013) and seen(1, 0x2013), 35, observed)
            assert endpoints == [session.sector.getsockname() for session in sessions]
            inventory = json.loads((state/'inventory.json').read_text())
            receipt = inventory['harvests'][str(node['id'])]
            quantity = sum(receipt['quantities'])
            assert quantity > 0 and inventory['explore_xp'] > 0
            assert sum(item['quantity'] for item in inventory['cargo'] if item) == quantity
            bob = json.loads((state/'accounts/bob/inventory.json').read_text())
            assert not bob.get('harvests') and not any(bob['cargo'])
            print('PASS: in-flight mining survives replacement; tractor is shared; cargo, XP and receipt agree', flush=True)
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            process = start_server(binary, host, state, content_file, log)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                for session in sessions: session.poll()
            assert json.loads((state/'inventory.json').read_text()) == inventory
            print('PASS: completed cast is not awarded again after a second replacement', flush=True)
        except BaseException:
            print(log.read_text(errors='replace'))
            raise
        finally:
            for session in sessions: session.close()
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
