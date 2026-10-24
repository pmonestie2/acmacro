"""Exercise only the local Rust server using the existing shared protocol client.

This starts an owned sandbox process with disposable state. It does not launch a
graphical game, read a player config, or contact the production game server.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time

from build import ROOT, build
from start_proxy import registered, sandbox_address
from enb_protocol.connection import GameConnection, receive_exact
from enb_protocol.login import GlobalLogin, decode_characters, register_comms, ticket_frame
from enb_protocol.session import SpaceSession
from enb_protocol.wire import Frame


def read_plain(connection: socket.socket) -> Frame:
    header = receive_exact(connection, 4)
    size = struct.unpack_from('<H', header)[0]
    if not 4 <= size <= 32767:
        raise ValueError('Invalid proxy bridge frame length')
    return Frame(struct.unpack_from('<H', header, 2)[0], receive_exact(connection, size - 4))


def connect(server: str, username: str = 'local', password: str = 'local',
            character_name: str = 'SandboxPilot', *, allow_station: bool = False) -> SpaceSession:
    comms = register_comms(server)
    global_connection: GameConnection | None = None
    try:
        with socket.create_connection((server, 3807), timeout=10) as bridge:
            bridge.sendall(Frame(0x3002, bytes(4)).encode() + Frame(0x2010, bytes(4)).encode())
            user, secret = username.encode('utf8'), password.encode('utf8')
            credentials = struct.pack('<H', len(user)) + user + struct.pack('<H', len(secret)) + secret
            bridge.sendall(Frame(0x2000, credentials).encode())
            response = read_plain(bridge)
            if response.opcode != 0x2001:
                raise ValueError('Local proxy account authentication did not return 0x2001')
            fields = dict(line.split('=', 1) for line in response.body.decode('ascii').splitlines() if '=' in line)
            if fields.get('Valid') != 'TRUE':
                raise ValueError('Local sandbox account authentication was rejected')
            ticket = fields['Ticket']
        print('PASS: proxy TCP bridge account authentication', flush=True)
        global_connection = GameConnection.connect(server)
        if global_connection.check_version() != 0:
            raise ValueError('Local global server rejected game version')
        global_connection.send(ticket_frame(ticket))
        reply = global_connection.receive()
        if reply.opcode != 0x70:
            raise ValueError(f'Local character list reply: {reply.opcode:#x}')
        characters = decode_characters(reply.body)
        if len(characters) != 1 or characters[0].name != character_name:
            raise ValueError('Local character list did not identify the expected character')
        print('PASS: RSA/RC4 version exchange and character list', flush=True)
        login = GlobalLogin(global_connection, comms)
        # enter() takes ownership of both the global TCP and registered UDP
        # connections; its finally path closes them if sector login fails.
        global_connection = None
        return SpaceSession.enter(server, login, characters, 0, allow_station=allow_station)
    except BaseException:
        if global_connection is not None:
            global_connection.close()
        comms.close()
        raise


def wait_for(session: SpaceSession, predicate: Callable[[], bool], timeout: float, reason: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session.poll()
        if predicate():
            return
    raise TimeoutError(reason)


def exercise_impulse(session: SpaceSession) -> None:
    if session.position is None:
        raise ValueError('Missing starting position for impulse test')
    initial = session.position
    owner = session.ticket.player_id
    session.send(0x14, struct.pack('<IB', owner, 2))
    wait_for(session, lambda: session.position is not None and session.position[0] > initial[0] + 20,
             4, 'Forward impulse did not change server position')
    session.send(0x14, struct.pack('<IB', owner, 4))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        session.poll()
    stopped = session.position
    deadline = time.monotonic() + .6
    while time.monotonic() < deadline:
        session.poll()
    if stopped is None or session.position is None or math.dist(stopped, session.position) > 1:
        raise ValueError('Brake command did not stop impulse motion')
    print('PASS: impulse command, acceleration, position updates and braking', flush=True)


def inventory_slot_matches(state: Path, container: str, slot: int, template: int | None) -> bool:
    path = state / 'inventory.json'
    if not path.exists():
        return False
    saved = json.loads(path.read_text())
    slots = saved.get(container)
    if not isinstance(slots,list) or slot >= len(slots):
        raise ValueError('Invalid persisted inventory in disposable server')
    item = slots[slot]
    return item is None if template is None else isinstance(item,dict) and item.get('template') == template


def exercise_inventory(session: SpaceSession, state: Path) -> None:
    # This touches only the disposable server's account and private temp files.
    # Its original engine is restored before movement/warp checks continue.
    for transfer, container, slot, template in [
        ((2,2,1,0,1),'cargo',0,2505),
        ((1,0,3,0,1),'vault',0,2505),
        ((3,0,1,0,1),'vault',0,None),
        ((1,0,2,2,1),'equipped',2,2505),
    ]:
        session.send(0x27,struct.pack('>6I',session.ticket.player_id,*transfer))
        wait_for(session,lambda: inventory_slot_matches(state,container,slot,template),5,
                 'Inventory request was not durably accepted')
    if not inventory_slot_matches(state,'cargo',0,None):
        raise ValueError('Inventory round trip duplicated the engine')
    print('PASS: native cargo/equipment/vault transfer packets and durable inventory round trip',flush=True)


def exercise_free_warp(session: SpaceSession) -> None:
    if session.position is None:
        raise ValueError('Missing starting position for free warp test')
    initial = session.position
    request = struct.pack('<IH', session.ticket.player_id, 0)
    session.send(0x9b, request)
    wait_for(session, lambda: session.position is not None and math.dist(initial, session.position) > 3500,
             7, 'Free warp did not charge and start traveling')
    session.send(0x9b, request)
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        session.poll()
    stopped = session.position
    deadline = time.monotonic() + .5
    while time.monotonic() < deadline:
        session.poll()
    if stopped is None or session.position is None or math.dist(stopped, session.position) > 1:
        raise ValueError('Second warp request did not cancel free flight')
    print('PASS: free warp charge, travel, cancellation and recovery', flush=True)


def exercise(server: str, state: Path, gate: bool, content: Path, movement: bool, warp: bool) -> None:
    expected = json.loads(content.read_text())
    session = connect(server)
    try:
        static_ids = {node['id'] for node in expected['navpoints']}
        resource_ids = {node['id'] for node in expected.get('resources', [])}
        actual_ids = set(session.world.objects)
        if (session.world.sector != expected['name'] or not static_ids <= actual_ids
                or not actual_ids <= static_ids | resource_ids):
            raise ValueError('Sector map does not match the configured static objects')
        print('PASS: UDP handoff, AUX reassembly, one-sector login and navigation map', flush=True)
        exercise_inventory(session,state)
        if movement:
            exercise_impulse(session)
        if warp:
            exercise_free_warp(session)
        sandbox = expected['name'] == 'Rust Sandbox'
        if sandbox:
            session.navigate(100000, 'warp')
            wait_for(session, lambda: session.position is not None and abs(session.position[0] - 18500) < 10, 15, 'Warp position did not reach the gate stand-off')
            print('PASS: server-authoritative warp and position updates', flush=True)
        else:
            names = {item.name for item in session.world.navigable()}
            if not {'ECS Dauntless', 'Sector Gate to Earth', 'Sector Gate to Saturn'}.issubset(names):
                raise ValueError('Real sector map lacks its expected station/gates')
            print(f'PASS: Asteroid Belt Alpha map, {len(static_ids)} static objects, '
                  f'{len(set(session.world.objects) & resource_ids)} streamed resources, station and gate names', flush=True)
        if gate:
            previous_stream = session.stream
            session.navigate(100000, 'gate')
            wait_for(session, lambda: session.stream is not previous_stream and session.started, 25, 'Loop gate did not complete its handoff')
            print('PASS: target acknowledgment, gate camera/delay, handoff and sector re-entry', flush=True)
        wait_for(session, lambda: (state / 'player.json').exists(), 5, 'Position was not persisted')
    finally:
        session.close()
    saved = json.loads((state / 'player.json').read_text())
    if sandbox and abs(saved['position'][0] - 18500) > 10:
        raise ValueError('Saved player position does not match arrival')
    print('PASS: logout and saved position', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', default='127.0.0.2')
    parser.add_argument('--gate', action='store_true', help='Also exercise the approximately 15-second gate sequence')
    parser.add_argument('--no-build', action='store_true')
    parser.add_argument('--movement', action='store_true', help='Verify forward impulse and braking')
    parser.add_argument('--warp', action='store_true', help='Verify free warp, cancellation and recovery')
    parser.add_argument('--real-sector', action='store_true', help='Verify Asteroid Belt Alpha map and impulse movement')
    args = parser.parse_args()
    server = sandbox_address(args.server)
    if args.real_sector and args.gate:
        parser.error('--gate exercises the sandbox loop; other real sectors are not hosted yet')
    if registered(server):
        raise SystemExit('A sandbox is already running at this address; use another loopback address for the disposable test')
    binary = ROOT / 'target/debug' / ('enb-server.exe' if os.name == 'nt' else 'enb-server') if args.no_build else build()
    content = ROOT / 'data' / ('asteroid_belt_alpha.json' if args.real_sector else 'sector.json')
    with tempfile.TemporaryDirectory(prefix='enb-rust-smoke-') as temporary:
        state = Path(temporary) / 'state'
        with (Path(temporary) / 'server.log').open('wb') as log:
            process = subprocess.Popen([str(binary), '--bind', server, '--content', str(content), '--state-dir', str(state)], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while not registered(server):
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Disposable Rust server did not become ready')
                time.sleep(.1)
            exercise(server, state, args.gate, content, args.movement or args.real_sector, args.warp)
        except BaseException:
            print((Path(temporary) / 'server.log').read_text(errors='replace'))
            raise
        finally:
            process.terminate()
            process.wait(timeout=5)


if __name__ == '__main__':
    main()
