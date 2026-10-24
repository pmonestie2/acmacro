"""Two disposable clients exercise native station avatars, rooms and restart.

This uses temporary accounts and local sockets only. It never launches or drives
a graphical game, reads player configuration, or contacts the production server.
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
from smoke_test import connect
from start_proxy import registered, sandbox_address
from enb_protocol.session import SpaceSession
from enb_protocol.wire import Frame


def report(session: SpaceSession, room: int, x: float) -> None:
    session.send(0x9d, struct.pack('<II4fI', session.ticket.player_id, room, .5, x, 2., 3., 1))


def collect(sessions: list[SpaceSession], observed: list[tuple[int, Frame]], duration: float) -> None:
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        for index, session in enumerate(sessions): observed.extend((index, frame) for frame in session.poll())


def matches(observed: list[tuple[int, Frame]], viewer: int, opcode: int, owner: int,
            *, x: float | None = None) -> bool:
    return any(index == viewer and frame.opcode == opcode and len(frame.body) >= 4
        and struct.unpack_from('<I', frame.body)[0] == owner
        and (x is None or (len(frame.body) == 28 and struct.unpack_from('<f', frame.body, 8)[0] == x))
        for index, frame in observed)


def start(binary: Path, host: str, state: Path, log: Path) -> subprocess.Popen[bytes]:
    with log.open('ab') as output:
        process = subprocess.Popen([str(binary), '--bind', host, '--state-dir', str(state),
            '--content', str(ROOT/'data/asteroid_belt_alpha.json')], stdout=output, stderr=output)
    try:
        deadline = time.monotonic() + 15
        while not registered(host):
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Disposable station server did not start')
            time.sleep(.1)
    except BaseException:
        # The caller has not received the process handle yet, so startup owns
        # cleanup too. A failed readiness observation must not leak a server.
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        raise
    return process


def exercise(binary: Path, host: str) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-station-') as temporary:
        state, log = Path(temporary)/'state', Path(temporary)/'server.log'
        for name in ('alice', 'bob'):
            subprocess.run([str(binary), '--create-account', name, '--character', name.title(),
                '--state-dir', str(state)], input='example\n', text=True, capture_output=True, check=True)
        for directory in (state, state/'accounts/bob'):
            (directory/'player.json').write_text(json.dumps({'version': 1, 'sector': 10761,
                'position': [0, 0, 0], 'home_station': 10761}))
        process: subprocess.Popen[bytes] | None = None
        sessions: list[SpaceSession] = []
        try:
            process = start(binary, host, state, log)
            sessions = [connect(host, name, 'example', name.title(), allow_station=True) for name in ('alice', 'bob')]
            a, b = (session.ticket.player_id for session in sessions)
            assert a != b
            report(sessions[0], 0, 10.)
            report(sessions[1], 0, 20.)
            observed: list[tuple[int, Frame]] = []
            pump(sessions, lambda: matches(observed, 0, 0x9e, b) and matches(observed, 1, 0x9e, a), 5, observed)
            assert matches(observed, 0, 0x61, b) and matches(observed, 1, 0x61, a)
            assert matches(observed, 0, 0xa0, b) and matches(observed, 1, 0xa0, a)
            text = b'/who\0'
            sessions[0].send(0x33, struct.pack('<IBH', a, 4, len(text)) + text)
            pump(sessions, lambda: any(index == 0 and frame.opcode == 0x1d and b'Online: Alice, Bob' in frame.body
                 for index, frame in observed), 5, observed)
            print('PASS: both station clients receive peer appearance, room entry and independent avatar pose', flush=True)
            collect(sessions, observed, .5)  # drain anything already queued
            observed.clear()
            collect(sessions, observed, .8)
            assert not any(frame.opcode in (0x61, 0x9e, 0xa0) for _, frame in observed)
            report(sessions[0], 0, 11.)
            pump(sessions, lambda: matches(observed, 1, 0x9e, a, x=11.), 5, observed)
            assert not matches(observed, 1, 0x61, a)
            print('PASS: idle avatars generate no repeats; movement updates position without appearance churn', flush=True)
            observed.clear()
            sessions[0].send(0x9f, struct.pack('<3I', a, 1, 0))
            pump(sessions, lambda: matches(observed, 1, 0xa0, a), 5, observed)
            assert any(index == 1 and frame.opcode == 0xa0 and frame.body == struct.pack('<3I', a, 0xffffffff, 0)
                       for index, frame in observed)
            observed.clear()
            report(sessions[0], 1, 100.)
            collect(sessions, observed, .8)
            assert not matches(observed, 1, 0x9e, a)
            sessions[1].send(0x9f, struct.pack('<3I', b, 1, 0))
            report(sessions[1], 1, 110.)
            pump(sessions, lambda: matches(observed, 0, 0x9e, b) and matches(observed, 1, 0x9e, a), 5, observed)
            print('PASS: room departure removes visibility; other rooms cannot observe movement; joining reveals idle peers', flush=True)
            collect(sessions, observed, .4)
            endpoints = [session.sector.getsockname() for session in sessions]
            sequences = [session.stream.expected for session in sessions]
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            observed.clear()
            process = start(binary, host, state, log)
            pump(sessions, lambda: all(session.stream.expected > before for session, before in zip(sessions, sequences)), 5, observed)
            assert endpoints == [session.sector.getsockname() for session in sessions]
            assert not any(frame.opcode in (0x61, 0x9e, 0xa0) for _, frame in observed)
            report(sessions[0], 1, 101.)
            pump(sessions, lambda: matches(observed, 1, 0x9e, a, x=101.), 5, observed)
            print('PASS: both station streams and visibility survive replacement without duplicate avatar spawns', flush=True)
            observed.clear()
            sessions[0].close()
            pump([sessions[1]], lambda: matches(observed, 0, 0xa0, a), 5, observed)
            print('PASS: logout removes the departed station avatar', flush=True)
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
