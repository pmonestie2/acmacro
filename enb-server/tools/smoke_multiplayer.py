"""Two real protocol clients against an owned disposable server; no live game UI."""
from __future__ import annotations

import argparse
from collections.abc import Callable
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import time

from build import ROOT
from smoke_test import connect
from start_proxy import registered
from enb_protocol.session import SpaceSession
from enb_protocol.wire import Frame


def group_action_enabled(frame: Frame, target: int) -> bool:
    """Native 0x5c has default and current attribute/verb lists, not actions."""
    if frame.opcode != 0x5c or len(frame.body) < 12:
        return False
    identity, defaults = struct.unpack_from('>II', frame.body)
    current = 8 + defaults * 4
    if identity != target or defaults > 4 or current + 4 > len(frame.body):
        return False
    count = struct.unpack_from('>I', frame.body, current)[0]
    if count > 4 or current + 4 + count * 4 != len(frame.body):
        return False
    return any(struct.unpack_from('<HH', frame.body, current + 4 + index * 4) == (0, 4)
               for index in range(count))


def pump(sessions: list[SpaceSession], until: Callable[[], bool], timeout: float,
         observed: list[tuple[int, Frame]]) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for index, session in enumerate(sessions):
            observed.extend((index, frame) for frame in session.poll())
        if until():
            return
    raise TimeoutError('Multiplayer condition was not confirmed')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/debug/enb-server.exe')
    parser.add_argument('--server', default='127.0.0.5')
    args = parser.parse_args()
    binary, host = args.binary.resolve(), args.server
    if registered(host):
        raise SystemExit('Use an unused disposable server address')
    with tempfile.TemporaryDirectory(prefix='enb-multiplayer-') as folder:
        state = Path(folder) / 'state'
        for name in ('alice', 'bob'):
            subprocess.run([str(binary), '--create-account', name, '--character', name.title(),
                            '--state-dir', str(state)], input='example\n', text=True, check=True, capture_output=True)
        # The opt-in sandbox grant also funds high-rank group skills. Native
        # startup must persist it independently for both authenticated accounts.
        (state/'sandbox_loadout.json').write_bytes((ROOT/'data/sandbox_loadout.json').read_bytes())
        (state/'accounts/bob/player.json').write_text(json.dumps({
            'version':1, 'sector':1076, 'position':[4000.,0.,0.]}))
        content = json.loads((ROOT / 'data/sector.json').read_text())
        content['mobs'][0]['spawn'] = [300, 0, 0]
        content_file = Path(folder) / 'sector.json'
        content_file.write_text(json.dumps(content))
        command = [str(binary), '--bind', host, '--content', str(content_file), '--state-dir', str(state)]
        sessions: list[SpaceSession] = []
        process: subprocess.Popen[bytes] | None = None
        with (Path(folder) / 'server.log').open('wb') as log:
            try:
                process = subprocess.Popen(command, stdout=log, stderr=log)
                deadline = time.monotonic() + 15
                while not registered(host):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Disposable server did not start')
                    time.sleep(.1)
                sessions.append(connect(host, 'alice', 'example', 'Alice'))
                sessions.append(connect(host, 'bob', 'example', 'Bob'))
                a, b = (session.ticket.player_id for session in sessions)
                assert a != b
                observed: list[tuple[int, Frame]] = []
                for directory in (state, state/'accounts/bob'):
                    owned = json.loads((directory/'inventory.json').read_text())
                    assert owned['credits'] == 100_000_000 and owned['sandbox_grant'] == 1
                    assert len([item for item in owned['vault'] if item]) == 4
                    assert owned['equipped'][3]['template'] == 1158
                print('PASS: both accounts provisioned with elite JE gear and credits; old gear preserved', flush=True)
                follower_state = state/'session.local.json'
                sessions[0].send(0x17, struct.pack('<II', a, b))
                pump(sessions, lambda: any(index == 0 and group_action_enabled(frame, b)
                    for index, frame in observed), 5, observed)
                print('PASS: selecting a live peer advertises the enabled native Group/Invite verb', flush=True)
                sessions[0].send(0x14, struct.pack('<IB', a, 12))

                def following() -> bool:
                    if not follower_state.exists(): return False
                    snapshot = json.loads(follower_state.read_text())
                    return snapshot['following'] and snapshot['position'][0] > 100.

                pump(sessions, following, 8, observed)
                sessions[0].send(0x14, struct.pack('<IB', a, 4))
                pump(sessions, lambda: not json.loads(follower_state.read_text())['following'], 5, observed)
                print('PASS: native impulse 12 follows selected peer; manual stop cancels', flush=True)

                sessions[0].send(0x2c, struct.pack('<4I', a, 10, b, 0))
                pump(sessions, lambda: any(index == 1 and frame.opcode == 0x1e for index, frame in observed), 5, observed)
                sessions[1].send(0x2c, struct.pack('<4I', b, 11, 0, 0))

                def grouped() -> bool:
                    path = state / 'groups.local.json'
                    if not path.exists(): return False
                    return any(set(group['members']) == {a, b} for group in json.loads(path.read_text())['groups'].values())

                pump(sessions, grouped, 5, observed)
                print('PASS: two independent logins, native invitation and shared group state', flush=True)
                sessions[0].send(0xbc, struct.pack('<3I', a, 0, 5))
                sessions[1].send(0xbc, struct.pack('<3I', b, 0, 7))
                pump(sessions, lambda: any(index == 1 and frame.opcode == 0x41
                     and struct.unpack_from('<II', frame.body) == (b, a)
                     for index, frame in observed), 5, observed)
                print('PASS: explicit form-up produces leader-relative native movement', flush=True)
                sessions[0].send(0x58, struct.pack('<3I', a, 0, 51))
                a_state = state / 'session.local.json'
                b_state = state / 'accounts/bob/session.local.json'

                def both_cloaked() -> bool:
                    return all(path.exists() and json.loads(path.read_text())['skills']['cloak_rank'] == 7 for path in (a_state, b_state))

                pump(sessions, both_cloaked, 8, observed)
                print('PASS: group cloak affects both clients and both session checkpoints', flush=True)
                sockets = [session.sector.getsockname() for session in sessions]
                sequences = [session.stream.expected for session in sessions]
                (state / 'stop.request').write_text('restart\n')
                process.wait(timeout=5)
                process = subprocess.Popen(command, stdout=log, stderr=log)
                deadline = time.monotonic() + 10
                while not registered(host):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Replacement server did not start')
                    time.sleep(.1)
                pump(sessions, lambda: all(session.stream.expected > before for session, before in zip(sessions, sequences)), 5, observed)
                assert [session.sector.getsockname() for session in sessions] == sockets
                assert grouped() and both_cloaked()
                print('PASS: both UDP streams, group membership and cloak survive process replacement without relog', flush=True)
                assert any(b in group['formed'] for group in json.loads((state / 'groups.local.json').read_text())['groups'].values())
                sessions[1].send(0x14, struct.pack('<IB', b, 4))
                pump(sessions, lambda: all(b not in group['formed'] for group in json.loads((state / 'groups.local.json').read_text())['groups'].values()), 5, observed)
                print('PASS: formation survives restart; manual flight detaches without leaving the group', flush=True)
                # Use a real client action to leave, then verify inherited cloak
                # is removed while the caster retains its own cloak.
                sessions[1].send(0x2c, struct.pack('<4I', b, 14, 0, 0))
                pump(sessions, lambda: json.loads(b_state.read_text())['skills']['cloak_rank'] == 0, 5, observed)
                assert json.loads(a_state.read_text())['skills']['cloak_rank'] == 7
                print('PASS: leaving a group revokes inherited support effects', flush=True)
            except BaseException:
                print((Path(folder) / 'server.log').read_text(errors='replace'))
                raise
            finally:
                for session in sessions: session.close()
                if process is not None and process.poll() is None:
                    process.terminate(); process.wait(timeout=5)


if __name__ == '__main__':
    main()
