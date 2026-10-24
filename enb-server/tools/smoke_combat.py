"""Fight the public Controller profile on a disposable server and rebind a save.

This uses real PW equipment and native combat packets. Only temporary accounts,
content and processes are changed; no graphical or production client is driven.
"""
from __future__ import annotations
import argparse
from collections.abc import Callable
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import time
from typing import TypedDict, cast
from build import ROOT
from smoke_support import start_server
from smoke_test import connect
from start_proxy import registered
from enb_protocol.session import SpaceSession
from enb_protocol.wire import Frame

class MobSnapshot(TypedDict):
    health: float
    max_health: float
    shield: float
    max_shield: float
    definition: dict[str, object]

class CombatSnapshot(TypedDict):
    shield: float
    player_firing: bool
    mobs: list[MobSnapshot]

def exercise(binary: Path, host: str) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-combat-') as temporary:
        folder=Path(temporary)
        state,log=folder/'state',folder/'server.log'
        subprocess.run([str(binary),'--create-account','fighter','--character','Fighter','--class','pw',
            '--state-dir',str(state)],input='example\n',text=True,capture_output=True,check=True)
        (state/'sandbox_loadout.json').write_bytes((ROOT/'data/sandbox_loadout.json').read_bytes())
        universe=json.loads((ROOT/'data/universe.json').read_text())
        boss=next(mob for sector in universe for mob in sector['mobs'] if mob['name']=='The Controller')
        boss.update(source_id=10,spawn=[4000.,0.,0.],respawn_seconds=120.)
        content=json.loads((ROOT/'data/sector.json').read_text())
        content['mobs'],content['resources']=[boss],[]
        content_file=folder/'sector.json'
        content_file.write_text(json.dumps(content))
        process: subprocess.Popen[bytes] | None=None
        client: SpaceSession | None=None
        observed: list[Frame]=[]

        def snapshot() -> CombatSnapshot:
            return cast(CombatSnapshot,json.loads((state/'session.local.json').read_text()))

        def wait(until: Callable[[],bool], timeout: float=12.) -> None:
            assert client is not None
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                observed.extend(client.poll())
                if until(): return
            raise TimeoutError('Combat condition was not observed')

        try:
            process=start_server(binary,host,state,content_file,log)
            client=connect(host,'fighter','example','Fighter')
            wait(lambda:(state/'session.local.json').exists())
            original=snapshot()
            assert original['mobs'][0]['max_shield']>7_000_000
            assert original['mobs'][0]['max_health']>300_000
            player=client.ticket.player_id
            client.send(0x17,struct.pack('<II',player,0x10000001))
            client.send(0x2c,struct.pack('<4I',player,9,0x10000001,0))
            # Native proxy prediction report: a bounded impulse-position update
            # puts the test pilot in beam range while the MOB approaches it.
            client.send(0x1004,struct.pack('<3f',1800.,0.,0.))
            wait(lambda:snapshot()['mobs'][0]['shield']<original['mobs'][0]['shield'])
            wait(lambda:snapshot()['shield']<original['shield']-2000.)
            assert any(frame.opcode==0xb and struct.unpack_from('<I',frame.body,2)[0]==player for frame in observed)
            assert any(frame.opcode==0xb and struct.unpack_from('<I',frame.body,2)[0]==0x10000001 for frame in observed)
            client.send(0x2c,struct.pack('<4I',player,9,0x10000001,0))
            wait(lambda:not snapshot()['player_firing'])
            print('PASS: public Controller has multi-million shields; PW beams and strong MOB attacks exchange damage',flush=True)
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            before=snapshot()['mobs'][0]
            # A public content refresh must replace old definitions/caps while
            # keeping damage, without healing the MOB or restoring prototype HP.
            boss['level']=65
            content_file.write_text(json.dumps(content))
            sequence=client.stream.expected
            process=start_server(binary,host,state,content_file,log)
            # Old packets can still be queued in the UDP socket. Wait for the
            # replacement's durable snapshot as well as stream progress.
            wait(lambda:client is not None and client.stream.expected>sequence
                and snapshot()['mobs'][0]['definition']['level']==65)
            after=snapshot()['mobs'][0]
            assert after['definition']['level']==65
            assert after['max_shield']<before['max_shield']
            assert abs(after['shield']/after['max_shield']-before['shield']/before['max_shield'])<1e-5
            # CL65: interpolate DAC at overall level 195, take the mechanical
            # 73% shield portion and the Controller's public 10x shield modifier.
            assert abs(after['max_shield']-6_957_896.5)<10.
            print('PASS: authenticated stream resumes; refreshed combat caps preserve the saved damage fraction',flush=True)
        except BaseException:
            if log.exists(): print(log.read_text(errors='replace'))
            raise
        finally:
            if client is not None: client.close()
            if process is not None and process.poll() is None:
                (state/'stop.request').write_text('smoke complete\n')
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)

def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'target/debug/enb-server.exe')
    parser.add_argument('--server',default='127.0.0.12')
    args=parser.parse_args()
    if registered(args.server): raise RuntimeError('Use an unused disposable server address')
    exercise(args.binary.resolve(),args.server)

if __name__=='__main__': main()
