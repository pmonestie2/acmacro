"""Native self/peer Jumpstart actions with real NPC incapacitation and no group.

Owns a disposable server, accounts and state; never drives the graphical client.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
import copy
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import time
from typing import TypedDict, cast

from build import ROOT
from enb_protocol.session import SpaceSession
from enb_protocol.wire import Frame
from smoke_multiplayer import target_verb_enabled
from smoke_support import start_server
from smoke_test import connect
from start_proxy import registered, sandbox_address


class PlayerState(TypedDict):
    hull: float
    shield: float
    skills: SkillState


class SkillState(TypedDict):
    cooldowns: dict[str, float]


def exercise(binary: Path, host: str) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-jumpstart-') as temporary:
        folder = Path(temporary)
        state, log = folder / 'state', folder / 'server.log'
        for name, character_class in [('rescuer', 'je'), ('victim', 'pw')]:
            subprocess.run([str(binary), '--create-account', name, '--character', name.title(),
                            '--class', character_class, '--state-dir', str(state)],
                           input='example\n', text=True, capture_output=True, check=True)
        (state / 'sandbox_loadout.json').write_bytes((ROOT / 'data/sandbox_loadout.json').read_bytes())
        (state / 'accounts/victim/player.json').write_text(json.dumps(dict(
            version=1, sector=1076, position=[20000., 0., 0.])))
        content = json.loads((ROOT / 'data/sector.json').read_text())
        template = content['mobs'][0]
        template.update(aggressive=True, radius=0., spawn_region=None)
        # One committed beam incapacitates its nearby victim. The long reload
        # leaves a recovery window; the fixture does not alter production HP.
        template['combat_override'].update(dps=1_000_000., reload=300., range=3000.)
        first, second = copy.deepcopy(template), copy.deepcopy(template)
        first.update(source_id=11, spawn=[500., 0., 0.])
        second.update(source_id=12, spawn=[19500., 0., 0.])
        content['mobs'], content['resources'] = [first, second], []
        waypoint = 100010
        content['navpoints'].append(dict(id=waypoint, name='Rescue rendezvous',
            object_type=37, asset=48, position=[18500., 0., 0.], navigation_type=1))
        content_file = folder / 'sector.json'
        content_file.write_text(json.dumps(content))
        sessions: list[SpaceSession] = []
        observed: list[tuple[int, Frame]] = []
        process: subprocess.Popen[bytes] | None = None

        def snapshot(name: str) -> PlayerState:
            path = (state if name == 'rescuer' else state / 'accounts/victim') / 'session.local.json'
            return cast(PlayerState, json.loads(path.read_text()))

        def wait(condition: Callable[[], bool], timeout: float = 30.) -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                for index, session in enumerate(sessions):
                    observed.extend((index, frame) for frame in session.poll())
                if condition():
                    return
            raise TimeoutError('Native Jumpstart protocol condition was not confirmed')

        def select(session: SpaceSession, target: int) -> None:
            session.send(0x17, struct.pack('<II', session.ticket.player_id, target))

        def jumpstart(session: SpaceSession, target: int) -> None:
            session.send(0x2c, struct.pack('<4I', session.ticket.player_id, 26, target, 0))

        def verb(target: int) -> bool:
            return any(index == 0 and target_verb_enabled(frame, target, 12)
                       for index, frame in observed)

        try:
            process = start_server(binary, host, state, content_file, log)
            sessions.append(connect(host, 'rescuer', 'example', 'Rescuer'))
            rescuer = sessions[0]
            owner = rescuer.ticket.player_id
            wait(lambda: snapshot('rescuer')['hull'] == 0.)
            select(rescuer, owner)
            wait(lambda: verb(owner))
            jumpstart(rescuer, owner)
            wait(lambda: snapshot('rescuer')['hull'] > 0.)
            print('PASS: NPC incapacitation exposes self Jumpstart; native action26 recovers the JE', flush=True)

            sessions.append(connect(host, 'victim', 'example', 'Victim'))
            victim = sessions[1].ticket.player_id
            wait(lambda: snapshot('victim')['hull'] == 0.)
            rescuer.send(0x9b, struct.pack('<IHI', owner, 1, waypoint))
            wait(lambda: rescuer.position is not None
                 and math.dist(rescuer.position, [18500., 0., 0.]) <= 101.)
            select(rescuer, victim)
            wait(lambda: verb(victim))
            # Self recovery's cooldown is real; wait for that cast's skill
            # cooldown rather than assuming arrival permits immediate reuse.
            wait(lambda: snapshot('rescuer')['skills']['cooldowns'].get('31', 0.) <= 0.)
            jumpstart(rescuer, victim)
            wait(lambda: snapshot('victim')['hull'] > 0.)
            groups = state / 'groups.local.json'
            if groups.exists():
                assert not json.loads(groups.read_text())['groups']
            print('PASS: visible nongroup PW exposes Jumpstart and receives native action26 recovery', flush=True)
        except BaseException:
            for index, frame in observed:
                if frame.opcode == 0x1d:
                    print(f'Client {index} message: {frame.body!r}')
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
    parser.add_argument('--server', default='127.0.0.20')
    args = parser.parse_args()
    host = sandbox_address(args.server)
    if registered(host):
        raise RuntimeError('Use an unused disposable server address')
    exercise(args.binary.resolve(), host)


if __name__ == '__main__':
    main()
