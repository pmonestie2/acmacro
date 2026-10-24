"""Native vendor packets against a disposable Rust server, including restart.

No graphical client, player configuration, production connection or live account
is touched. The temporary character starts inside ECS Dauntless with test funds.
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
from typing import TypedDict, cast

from build import ROOT
from smoke_test import connect, wait_for
from start_proxy import registered, sandbox_address
from enb_protocol.session import SpaceSession
from enb_protocol.wire import Frame


class CargoItem(TypedDict):
    template: int
    quantity: int
    quality: float
    structure: float
    builder_name: str


class OwnedInventory(TypedDict):
    cargo: list[CargoItem | None]
    equipped: list[CargoItem | None]
    credits: int


def saved_inventory(state: Path) -> OwnedInventory:
    return cast(OwnedInventory, json.loads((state/'inventory.json').read_text()))


def await_frame(session: SpaceSession, opcode: int, body: bytes | None = None) -> Frame:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        for frame in session.poll():
            if frame.opcode == opcode and (body is None or frame.body == body): return frame
    raise TimeoutError(f'Vendor response {opcode:#x} not received')


def transfer(session: SpaceSession, state: Path, values: tuple[int, int, int, int, int], credits: int) -> None:
    session.send(0x27, struct.pack('>6I', session.ticket.player_id, *values))
    # Unrelated AUX frames and delayed resume snapshots are not acknowledgments.
    wait_for(session, lambda: saved_inventory(state)['credits'] == credits, 5,
             'Vendor transfer did not durably publish the expected credit balance')


def start(binary: Path, host: str, state: Path, log: Path) -> subprocess.Popen[bytes]:
    with log.open('ab') as output:
        process = subprocess.Popen([str(binary), '--bind', host, '--state-dir', str(state),
            '--content', str(ROOT/'data/asteroid_belt_alpha.json')], stdout=output, stderr=output)
    deadline = time.monotonic() + 15
    while not registered(host):
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError('Disposable vendor server did not start')
        time.sleep(.1)
    return process


def exercise(binary: Path, host: str) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-vendor-') as temporary:
        state, log = Path(temporary)/'state', Path(temporary)/'server.log'
        subprocess.run([str(binary), '--create-account', 'local', '--character', 'SandboxPilot',
            '--state-dir', str(state)], input='local\n', text=True, capture_output=True, check=True)
        (state/'player.json').write_text(json.dumps({'version': 1, 'sector': 10761,
            'position': [0, 0, 0], 'home_station': 10761}))
        owned = OwnedInventory(cargo=[None]*22, equipped=[None]*20, credits=1_000_000)
        owned['cargo'][5] = CargoItem(template=2505, quantity=1, quality=1., structure=1., builder_name='FixtureMaker')
        for entry in json.loads((ROOT/'data/je_loadout.json').read_text()):
            owned['equipped'][entry['slot']] = CargoItem(template=entry['template'], quantity=1, quality=1., structure=1., builder_name='')
        (state/'inventory.json').write_text(json.dumps(owned))
        process: subprocess.Popen[bytes] | None = None
        session: SpaceSession | None = None
        try:
            process = start(binary, host, state, log)
            session = connect(host, allow_station=True)
            owner = session.ticket.player_id
            session.send(0x4e, struct.pack('<IIB', owner, 16, 4))
            greeting = await_frame(session, 0x54)
            assert b'@name' not in greeting.body and b'SandboxPilot' in greeting.body
            session.send(0x55, struct.pack('<IB', owner, 2))
            await_frame(session, 0x56, struct.pack('<i', 6))
            print('PASS: placed NPC conversation, token substitution and native vendor opening', flush=True)
            transfer(session, state, (4, 0, 1, 0xffffffff, 3), 1_000_000 - 20_655*3)
            bought = saved_inventory(state)
            assert [item['template'] if item else None for item in bought['cargo'][:3]] == [3661]*3
            assert bought['credits'] == 1_000_000 - 20_655*3
            assert bought['cargo'][5] == owned['cargo'][5]
            print('PASS: vendor slot purchase uses server quote and persists cargo/credits together', flush=True)
            # A complete catalog can exceed the MTU; the real shared protocol
            # stream reassembles it before delivery. Keep the exact UDP socket.
            endpoint, sequence = session.sector.getsockname(), session.stream.expected
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            process = start(binary, host, state, log)
            deadline = time.monotonic() + 5
            while session.stream.expected <= sequence:
                if time.monotonic() > deadline: raise TimeoutError('Vendor session did not resume')
                session.poll()
            assert endpoint == session.sector.getsockname()
            transfer(session, state, (1, 0, 4, 0, 1), bought['credits'] + 12_240)
            sold = saved_inventory(state)
            assert sold['cargo'][0] is None and sold['credits'] == bought['credits'] + 12_240
            assert sold['cargo'][5] == owned['cargo'][5]
            print('PASS: open vendor and unchanged UDP client survive replacement; sale credits are correct', flush=True)
            session.send(0x55, struct.pack('<IB', owner, 230))
            await_frame(session, 0x56, struct.pack('<i', -32))
            session.send(0x27, struct.pack('>6I', owner, 4, 0, 1, 0xffffffff, 1))
            deadline = time.monotonic() + 5
            while True:
                if any(frame.opcode == 0x1d and b'Open a vendor before trading' in frame.body for frame in session.poll()): break
                if time.monotonic() > deadline: raise TimeoutError('Closed vendor rejection was not received')
            assert saved_inventory(state) == sold
            print('PASS: closed vendor rejects further purchases without changing owned state', flush=True)
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
