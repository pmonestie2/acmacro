"""Two disposable native-protocol clients exercise combat, corpse loot and restart.

Uses an actual public MOB/drop definition and owned high-level equipment in a
reduced map. Never drives graphical clients or contacts the production game.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from typing import TypedDict, cast

from build import ROOT
from smoke_support import start_server
from smoke_multiplayer import pump
from smoke_test import connect
from start_proxy import registered, sandbox_address
from enb_protocol.session import SpaceSession
from enb_protocol.wire import Frame


class CargoItem(TypedDict):
    template: int
    quantity: int


class InventorySnapshot(TypedDict):
    cargo: list[CargoItem | None]
    loot_receipts: dict[str, object]


def exercise(binary: Path, host: str) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-loot-') as temporary:
        folder = Path(temporary)
        state, log = folder/'state', folder/'server.log'
        for name in ('alice', 'bob'):
            subprocess.run([str(binary), '--create-account', name, '--character', name.title(),
                '--state-dir', str(state)], input='example\n', text=True, capture_output=True, check=True)
        universe = json.loads((ROOT/'data/universe.json').read_text())
        mob = next(mob for sector in universe for mob in sector['mobs'] if mob['template_id'] == 2010)
        # Relocate this loot fixture completely. Its public spawn region is in
        # another sector; retaining it would make the NPC drift away while the
        # second account logs in, turning a loot test into a moving-range race.
        mob.update(source_id=10, spawn=[1500., 0., 0.], spawn_region=None,
                   aggressive=False, respawn_seconds=120.)
        content = json.loads((ROOT/'data/sector.json').read_text())
        content['mobs'], content['resources'] = [mob], []
        content_file = folder/'sector.json'
        content_file.write_text(json.dumps(content))
        for directory in (state, state/'accounts/bob'):
            equipped: list[dict[str, int | float] | None] = [None]*20
            for item in json.loads((ROOT/'data/je_loadout.json').read_text()):
                template = 3340 if item['slot'] == 1 else 1158 if item['slot'] == 3 else item['template']
                equipped[item['slot']] = {'template': template, 'quantity': 1, 'quality': 1., 'structure': 1.}
            (directory/'inventory.json').write_text(json.dumps({'cargo': [None]*22, 'equipped': equipped}))
            (directory/'player.json').write_text(json.dumps({'version': 1, 'sector': content['id'],
                'position': [0., 0., 0.], 'home_station': 10761}))
        process: subprocess.Popen[bytes] | None = None
        sessions: list[SpaceSession] = []
        observed: list[tuple[int, Frame]] = []

        def corpse_ids(viewer: int) -> set[int]:
            return {struct.unpack_from('<I', frame.body)[0] for index, frame in observed
                if index == viewer and frame.opcode == 0x2018 and len(frame.body) >= 5 and frame.body[4] == 25}

        def seen(viewer: int, opcode: int) -> bool:
            return any(index == viewer and frame.opcode == opcode for index, frame in observed)

        def inventory(name: str) -> InventorySnapshot:
            path = state/'inventory.json' if name == 'alice' else state/'accounts'/name/'inventory.json'
            return cast(InventorySnapshot, json.loads(path.read_text()))

        def awarded() -> bool:
            return any(inventory('alice')['cargo'])

        try:
            process = start_server(binary, host, state, content_file, log)
            sessions = [connect(host, name, 'example', name.title()) for name in ('alice', 'bob')]
            a, b = (session.ticket.player_id for session in sessions)
            sessions[0].send(0x17, struct.pack('<II', a, 0x10000001))
            sessions[0].send(0x2c, struct.pack('<4I', a, 9, 0x10000001, 0))
            pump(sessions, lambda: bool(corpse_ids(0) & corpse_ids(1)), 8, observed)
            corpse = next(iter(corpse_ids(0) & corpse_ids(1)))
            stock = json.loads((state/f'loot/{content["id"]}.json').read_text())['corpses'][str(corpse)]['initial']['items']
            assert any(item is not None and item['template'] == 5931 for item in stock)
            for session in sessions: session.send(0x17, struct.pack('<II', session.ticket.player_id, corpse))
            sessions[1].send(0x51, struct.pack('<II', b, 0))
            pump(sessions, lambda: any(index == 1 and frame.opcode == 0x1d and b'reserved' in frame.body
                for index, frame in observed), 5, observed)
            assert not seen(1, 0x8c)
            print('PASS: real equipped beam kills MOB; both viewers receive one corpse; private ownership enforced', flush=True)
            observed.clear()
            sessions[0].send(0x51, struct.pack('<II', a, 0))
            pump(sessions, lambda: seen(0, 0x8c) and seen(0, 0x66), 5, observed)
            sessions[1].send(0x51, struct.pack('<II', b, 0))
            pump(sessions, lambda: seen(1, 0x8c), 5, observed)
            sessions[0].send(0x27, struct.pack('>6I', a, 6, 0, 0, 0, 0))
            pump(sessions, lambda: seen(0, 0x2014) and seen(1, 0x2014), 5, observed)
            sessions[1].send(0x27, struct.pack('>6I', b, 18, 0, 0, 0, 0))
            pump(sessions, lambda: any(index == 1 and frame.opcode == 0x1d and b'already being looted' in frame.body
                for index, frame in observed), 3, observed)
            endpoints = [session.sector.getsockname() for session in sessions]
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            observed.clear()
            process = start_server(binary, host, state, content_file, log)
            pump(sessions, lambda: awarded(), 20, observed)
            assert endpoints == [session.sector.getsockname() for session in sessions]
            assert not any(inventory('bob')['cargo'])
            first = inventory('alice')
            assert first['loot_receipts']
            print('PASS: timed tractor is shared; competing claim rejected; same UDP clients resume and award only the owner', flush=True)
            pump(sessions, lambda: all(any(index == viewer and frame.opcode == 7 and frame.body == struct.pack('<I', corpse)
                for index, frame in observed) for viewer in (0, 1)), 5, observed)
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            observed.clear()
            process = start_server(binary, host, state, content_file, log)
            sessions[0].send(0x51, struct.pack('<II', a, 0))
            pump(sessions, lambda: seen(0, 0x1d), 5, observed)
            assert inventory('alice') == first
            assert not corpse_ids(0) and not corpse_ids(1)
            world = json.loads((state/f'loot/{content["id"]}.json').read_text())
            assert len(world['corpses']) == 1 and world['deaths']['10']['generation'] == 1
            print('PASS: depletion removes corpse for both clients; repeated replacement neither revives MOB nor awards loot again', flush=True)
        except BaseException:
            for index, frame in observed:
                if frame.opcode == 0x1d:
                    print(f'Client {index} message: {frame.body!r}')
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
