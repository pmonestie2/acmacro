"""Faction/roaming/takeover packets against an owned disposable Rust server.

Uses the public JE/PW faction matrix and a survivable spawn-region fixture.
Never reads local player state or drives a graphical client.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import math
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
from typing import TypedDict, cast

from build import ROOT
from enb_protocol.login import Character, CharacterTicket
from enb_protocol.session import SpaceSession
from enb_protocol.wire import Frame
from smoke_support import start_server
from smoke_test import connect
from start_proxy import registered

MOB = 0x10000001


class AggroSnapshot(TypedDict):
    target: int | None


class ActorSnapshot(TypedDict):
    position: list[float]
    aggro: AggroSnapshot


class WorldSnapshot(TypedDict):
    actors: list[ActorSnapshot]


class RecordingSession(SpaceSession):
    def __init__(self, host: str, ticket: CharacterTicket, character: Character,
                 sector: socket.socket, comms: socket.socket, sector_port: int) -> None:
        super().__init__(host, ticket, character, sector, comms, sector_port)
        self.observed: list[Frame] = []

    def poll(self) -> list[Frame]:
        frames = super().poll()
        self.observed.extend(frames)
        return frames

    def reaction(self) -> int | None:
        values = [struct.unpack_from('<I', frame.body, 4)[0]
                  for frame in self.observed if frame.opcode == 0x89
                  and len(frame.body) == 9 and struct.unpack_from('>I', frame.body)[0] == MOB]
        return values[-1] if values else None


def exercise(binary: Path, host: str) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-factions-') as temporary:
        folder = Path(temporary)
        state, log = folder / 'state', folder / 'server.log'
        for name, character_class in [('friend', 'je'), ('foe', 'pw')]:
            subprocess.run([str(binary), '--create-account', name, '--character', name.title(),
                            '--class', character_class, '--state-dir', str(state)],
                           input='example\n', text=True, capture_output=True, check=True)
        content = json.loads((ROOT / 'data/sector.json').read_text())
        mob = content['mobs'][0]
        mob.update(spawn=[4000., 0., 0.], faction=16, aggression=3, aggressive=False,
                   spawn_region=dict(center=[4000., 0., 0.], radius=8000.))
        mob['combat_override'].update(dps=0.25, range=1000., reload=20.)
        content['resources'] = []
        content_file = folder / 'sector.json'
        content_file.write_text(json.dumps(content))
        world_file = state / 'combat/1076.json'
        sessions: list[RecordingSession] = []
        process: subprocess.Popen[bytes] | None = None

        def actor() -> ActorSnapshot:
            return cast(WorldSnapshot, json.loads(world_file.read_text()))['actors'][0]

        def wait(condition: Callable[[], bool], timeout: float = 12.) -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                for session in sessions:
                    if not session.closed:
                        session.poll()
                if condition():
                    return
            raise TimeoutError('Faction/roaming protocol condition was not confirmed')

        def login(name: str) -> RecordingSession:
            client = connect(host, name, 'example', name.title(), session_type=RecordingSession)
            assert isinstance(client, RecordingSession)
            sessions.append(client)
            return client

        try:
            process = start_server(binary, host, state, content_file, log)
            friend = login('friend')
            wait(lambda: friend.reaction() == 2 and world_file.exists())
            initial = actor()['position']
            wait(lambda: math.dist(initial, actor()['position']) > 150.)
            assert actor()['aggro']['target'] is None
            assert any(frame.opcode == 0x3e for frame in friend.observed)
            print('PASS: same-faction JE sees friendly NPC drifting without acquisition', flush=True)

            foe = login('foe')
            owner = foe.ticket.player_id
            wait(lambda: actor()['aggro']['target'] == owner and foe.reaction() == 0)
            assert friend.reaction() == 2
            print('PASS: aggression 3 acquires hostile PW; simultaneous JE remains friendly', flush=True)

            replacement = login('foe')
            # Explicit late logout from the original transport must not retire
            # the new owner. Closing after takeover also exercises comms logout.
            foe.close()
            sequence = replacement.stream.expected
            wait(lambda: replacement.stream.expected > sequence
                 and actor()['aggro']['target'] == owner)
            print('PASS: authenticated takeover survives old transport logout', flush=True)

            sequence = replacement.stream.expected
            (state / 'stop.request').write_text('faction restart check\n')
            process.wait(timeout=5)
            process = start_server(binary, host, state, content_file, log)
            wait(lambda: replacement.stream.expected > sequence)
            wait(lambda: replacement.reaction() == 0 and friend.reaction() == 2
                 and actor()['aggro']['target'] == owner)
            print('PASS: faction attitudes, pursuit and replacement session survive restart', flush=True)
        except BaseException:
            if log.exists():
                print(log.read_text(errors='replace'))
            raise
        finally:
            for session in sessions:
                session.close()
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
    parser.add_argument('--server', default='127.0.0.18')
    args = parser.parse_args()
    if registered(args.server):
        raise RuntimeError('Use an unused disposable server address')
    exercise(args.binary.resolve(), args.server)


if __name__ == '__main__':
    main()
