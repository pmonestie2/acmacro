"""Native launcher loading/fire, owner logout and in-flight restart recovery.

Uses disposable TE/JE accounts and real public launchers/ammunition. It never
launches a graphical client or reads a live character's inventory.
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

class OwnedItem(TypedDict):
    template: int
    quantity: int
    quality: float
    structure: float

class OwnedState(TypedDict):
    ammunition: list[OwnedItem | None]
    pending_launch: object
    shot_sequence: int

class ActorState(TypedDict):
    shield: float

class WorldState(TypedDict):
    actors: list[ActorState]
    projectiles: list[dict[str, object]]

def item(template: int,quantity: int=1)->OwnedItem:
    return OwnedItem(template=template,quantity=quantity,quality=1.,structure=1.)

def exercise(binary: Path,host: str,kind: str)->None:
    code,launcher,ammo,effect,range_m,damage=('te',2656,507,886,3000.,60.) if kind=='ml' else ('je',3022,512,948,2000.,11.)
    with tempfile.TemporaryDirectory(prefix=f'enb-launcher-{kind}-') as temporary:
        folder=Path(temporary)
        state,log=folder/'state',folder/'server.log'
        for username,character,character_class in [('shooter','Shooter',code),('watcher','Watcher','je')]:
            subprocess.run([str(binary),'--create-account',username,'--character',character,'--class',character_class,
                '--state-dir',str(state)],input='example\n',text=True,capture_output=True,check=True)
        loadout=json.loads((ROOT/'data/sandbox_loadout.json').read_text())['by_class'][code]
        equipped: list[OwnedItem | None]=[None]*20
        for entry in loadout:
            if entry['slot']<3:
                equipped[entry['slot']]=item(entry['template'])
        equipped[3]=item(launcher)
        ammunition: list[OwnedItem | None]=[None]*20
        ammunition[3]=item(ammo)
        cargo: list[OwnedItem | None]=[item(ammo,2)]+[None]*21
        (state/'inventory.json').write_text(json.dumps({'equipped':equipped,'ammunition':ammunition,
            'cargo':cargo,'credits':100_000_000,'sandbox_grant':1}))
        content=json.loads((ROOT/'data/sector.json').read_text())
        content['mobs'][0].update(spawn=[range_m,0.,0.],aggressive=False,respawn_seconds=120.)
        content['resources']=[]
        content_file=folder/'sector.json'
        content_file.write_text(json.dumps(content))
        clients: list[SpaceSession]=[]
        process: subprocess.Popen[bytes] | None=None
        frames: list[Frame]=[]

        def owned()->OwnedState:
            return cast(OwnedState,json.loads((state/'inventory.json').read_text()))

        def world()->WorldState:
            return cast(WorldState,json.loads((state/'combat/1076.json').read_text()))

        def wait(until:Callable[[],bool],timeout:float=12.)->None:
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                for client in clients:
                    if not client.closed: frames.extend(client.poll())
                if until():return
            raise TimeoutError(f'{kind} launcher condition was not confirmed')

        def launched()->bool:
            data=owned()
            loaded=data['ammunition'][3]
            return data.get('shot_sequence')==1 and data.get('pending_launch') is None and loaded is not None and loaded['quantity']==2

        def effect_count(owner:int)->int:
            return sum(frame.opcode==0xb and len(frame.body)==23
                and struct.unpack_from('<I',frame.body,2)[0]==owner
                and struct.unpack_from('<H',frame.body,10)[0]==effect for frame in frames)

        try:
            process=start_server(binary,host,state,content_file,log)
            clients=[connect(host,'shooter','example','Shooter'),connect(host,'watcher','example','Watcher')]
            shooter,watcher=clients
            owner=shooter.ticket.player_id
            shooter.send(0x17,struct.pack('<II',owner,0x10000001))
            shooter.send(0x5d,struct.pack('<IBB',owner,2,3))
            wait(launched)
            shooter.send(0x5d,struct.pack('<IBB',owner,2,3))
            assert world()['actors'][0]['shield']==150.
            assert len(world()['projectiles'])==1
            wait(lambda:effect_count(owner)>0)
            assert owned()['shot_sequence']==1
            shooter.close()
            wait(lambda:not (state/'session.local.json').exists())
            # The shot remains a sector-owned event after its owner logs out.
            endpoint=watcher.sector.getsockname()
            sequence=watcher.stream.expected
            (state/'stop.request').write_text('restart with round in flight\n')
            process.wait(timeout=5)
            process=start_server(binary,host,state,content_file,log)
            wait(lambda:watcher.stream.expected>sequence and world()['actors'][0]['shield']==150.-damage)
            assert watcher.sector.getsockname()==endpoint
            assert world()['projectiles']==[]
            assert owned()['shot_sequence']==1
            loaded=owned()['ammunition'][3]
            assert loaded is not None and loaded['quantity']==2
            print(f'PASS: {kind.upper()} uses ammo effect {effect}, consumes one round and auto-reloads owned cargo',flush=True)
            print(f'PASS: delayed {damage:g} damage lands once after owner logout and server replacement',flush=True)
            # A second replacement must not replay the already applied hit.
            (state/'stop.request').write_text('restart after hit\n')
            process.wait(timeout=5)
            sequence=watcher.stream.expected
            process=start_server(binary,host,state,content_file,log)
            wait(lambda:watcher.stream.expected>sequence)
            assert world()['actors'][0]['shield']==150.-damage
            assert owned()['shot_sequence']==1
            print(f'PASS: {kind.upper()} resolved hit stays depleted across a second replacement',flush=True)
        except BaseException:
            if log.exists():print(log.read_text(errors='replace'))
            raise
        finally:
            for client in clients:client.close()
            if process is not None and process.poll() is None:
                (state/'stop.request').write_text('smoke complete\n')
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)

def main()->None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'target/debug/enb-server.exe')
    parser.add_argument('--server',default='127.0.0.12')
    parser.add_argument('--kind',choices=('ml','pl','both'),default='both')
    args=parser.parse_args()
    if registered(args.server):raise RuntimeError('Use an unused disposable server address')
    for kind in (('ml','pl') if args.kind=='both' else (args.kind,)):
        exercise(args.binary.resolve(),args.server,kind)

if __name__=='__main__':main()
