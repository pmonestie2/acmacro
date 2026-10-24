"""NPC launch, cloak and in-flight replacement using a disposable local account.

The public PL template identifies the weapon; a small explicit combat fixture
keeps the shot survivable. This never drives a graphical or production client.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
import json
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
from typing import TypedDict, cast

from build import ROOT
from enb_protocol.session import SpaceSession
from enb_protocol.login import Character, CharacterTicket
from enb_protocol.wire import Frame
from smoke_support import start_server
from smoke_test import connect
from start_proxy import registered


class SkillSnapshot(TypedDict):
    cloak_rank: int


class PlayerSnapshot(TypedDict):
    session_epoch: int
    skills: SkillSnapshot


class HitSnapshot(TypedDict):
    target: int
    target_epoch: int
    damage: float
    damage_type: int


class ShotSnapshot(TypedDict):
    hit: HitSnapshot
    duration_ms: int


class WorldSnapshot(TypedDict):
    npc_shots: list[ShotSnapshot]
    actors: list[ActorSnapshot]


class ActorSnapshot(TypedDict):
    state: str


class RecordingSession(SpaceSession):
    """Keep the frames consumed during initial map login as well as later polls."""

    def __init__(self, host: str, ticket: CharacterTicket, character: Character,
                 sector: socket.socket, comms: socket.socket, sector_port: int) -> None:
        super().__init__(host, ticket, character, sector, comms, sector_port)
        self.observed: list[Frame] = []

    def poll(self) -> list[Frame]:
        frames = super().poll()
        self.observed.extend(frames)
        return frames


def exercise(binary: Path, host: str, level: int = 20) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-npc-projectile-') as temporary:
        folder = Path(temporary)
        state, log = folder / 'state', folder / 'server.log'
        subprocess.run([str(binary), '--create-account', 'victim', '--character', 'Victim',
                        '--class', 'je', '--state-dir', str(state)],
                       input='example\n', text=True, capture_output=True, check=True)
        content = json.loads((ROOT / 'data/sector.json').read_text())
        mob = content['mobs'][0]
        mob.update(level=level, spawn=[4000., 0., 0.], aggressive=True)
        mob['weapon'] = dict(item=3022, item_type=16, damage=11., damage_type=3,
                             reload=2., effect=948)
        mob['combat_override'].update(dps=0.25, reload=20., range=5000., effect=948)
        content['resources'] = []
        content_file = folder / 'sector.json'
        content_file.write_text(json.dumps(content))
        player_file = state / 'session.local.json'
        world_file = state / 'combat/1076.json'
        frames: list[Frame] = []
        client: RecordingSession | None = None
        process: subprocess.Popen[bytes] | None = None

        def player() -> PlayerSnapshot:
            return cast(PlayerSnapshot, json.loads(player_file.read_text()))

        def world() -> WorldSnapshot:
            return cast(WorldSnapshot, json.loads(world_file.read_text()))

        def wait(condition: Callable[[], bool], timeout: float = 20.) -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if client is not None and not client.closed:
                    client.poll()
                if condition():
                    return
            raise TimeoutError('Disposable NPC projectile condition was not confirmed')

        try:
            process = start_server(binary, host, state, content_file, log)
            connected = connect(host, 'victim', 'example', 'Victim', session_type=RecordingSession)
            assert isinstance(connected, RecordingSession)
            client = connected
            frames = client.observed
            owner = client.ticket.player_id
            wait(lambda: player_file.exists() and bool(world()['npc_shots']))
            shot = world()['npc_shots'][0]
            assert shot['hit']['target'] == owner
            assert shot['hit']['target_epoch'] == player()['session_epoch']
            assert shot['hit']['damage'] == 5. and shot['hit']['damage_type'] == 3
            assert shot['duration_ms'] == 12000

            def damage_reports() -> list[Frame]:
                return [frame for frame in frames if frame.opcode == 0x64
                        and len(frame.body) == 24
                        and struct.unpack_from('<II', frame.body, 16) == (0x10000001, owner)]

            def saw_launch() -> bool:
                return any(frame.opcode == 0x0b and len(frame.body) == 23
                           and struct.unpack_from('<II', frame.body, 2) == (0x10000001, owner)
                           and struct.unpack_from('<H', frame.body, 10)[0] == 948
                           and struct.unpack_from('<H', frame.body, 21)[0] == 12000
                           for frame in frames)

            wait(saw_launch)
            assert not damage_reports()
            client.send(0x58, struct.pack('<3I', owner, 0, 28))
            wait(lambda: player()['skills']['cloak_rank'] == 1, timeout=6.)
            # Trained JE Cloak7 at OL150 gives stealth185 even for variant1.
            expected_state = 'Attacking' if level * 3 >= 185 else 'Idle'
            wait(lambda: world()['actors'][0]['state'] == expected_state)
            assert not damage_reports()
            print(f'PASS: CL{level} sensor state after cloak is {expected_state}', flush=True)
            epoch = player()['session_epoch']
            endpoint = client.sector.getsockname()
            sequence = client.stream.expected
            (state / 'stop.request').write_text('replace with NPC shot in flight\n')
            process.wait(timeout=5)
            process = start_server(binary, host, state, content_file, log)
            wait(lambda: client is not None and client.stream.expected > sequence)
            assert player()['session_epoch'] == epoch
            assert player()['skills']['cloak_rank'] == 1
            wait(lambda: len(damage_reports()) == 1 and world()['npc_shots'] == [])
            report = damage_reports()[0]
            assert struct.unpack_from('<f', report.body)[0] == 5.
            assert struct.unpack_from('<II', report.body, 8) == (3, 1)
            assert client.sector.getsockname() == endpoint
            print('PASS: NPC PL effect precedes its frozen 12-second impact', flush=True)
            print('PASS: committed NPC shot lands on a cloaked target after server replacement', flush=True)
            (state / 'stop.request').write_text('replace after NPC impact acknowledgment\n')
            process.wait(timeout=5)
            sequence = client.stream.expected
            process = start_server(binary, host, state, content_file, log)
            wait(lambda: client is not None and client.stream.expected > sequence)
            assert len(damage_reports()) == 1
            assert world()['npc_shots'] == []
            print('PASS: second replacement does not replay acknowledged NPC damage', flush=True)
        except BaseException:
            if log.exists():
                print(log.read_text(errors='replace'))
            raise
        finally:
            if client is not None:
                client.close()
            if process is not None and process.poll() is None:
                (state / 'stop.request').write_text('smoke complete\n')
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/debug/enb-server.exe')
    parser.add_argument('--server', default='127.0.0.15')
    parser.add_argument('--level', type=int, default=20, choices=(20, 61, 62))
    args = parser.parse_args()
    if registered(args.server):
        raise RuntimeError('Use an unused disposable server address')
    exercise(args.binary.resolve(), args.server, args.level)


if __name__ == '__main__':
    main()
