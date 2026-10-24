"""Two disposable native-protocol gate transitions across server replacement.

One client finishes early; another waits for its camera and replaces the server
again before acknowledging. Uses real Alpha/Saturn gates and no live accounts.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
from typing import TypedDict, cast

from build import ROOT
from smoke_support import start_server
from smoke_test import connect
from start_proxy import registered, sandbox_address
from enb_protocol.session import SpaceSession
from enb_protocol.wire import encode_udp


class GateState(TypedDict):
    gate: int
    destination: int
    started_at_ms: int
    camera_sent: bool
    completion_requested: bool


class SessionState(TypedDict):
    sector: int
    target: int
    gate_transit: GateState | None


def exercise(binary: Path, host: str, *, fail_mirror: bool = False) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-gating-') as temporary:
        folder=Path(temporary)
        state, log=folder/'state', folder/'server.log'
        for username, character in [('local','SandboxPilot'),('bob','Bob')]:
            subprocess.run([str(binary),'--create-account',username,'--character',character,'--state-dir',str(state)],
                input='example\n',text=True,capture_output=True,check=True)
        directories=[state,state/'accounts/bob']
        for directory in directories:
            (directory/'player.json').write_text(json.dumps({'version':1,'sector':1076,
                'position':[186000.,-16192.,0.],'home_station':10761}))
        process:subprocess.Popen[bytes] | None=None
        clients:list[SpaceSession]=[]
        cameras=[0,0]
        selected: list[set[int]]=[set(),set()]

        def snapshot(index:int) -> SessionState:
            return cast(SessionState,json.loads((directories[index]/'session.local.json').read_text()))

        def completed_early() -> bool:
            gate=snapshot(0).get('gate_transit')
            return gate is not None and gate['completion_requested']

        def camera_saved() -> bool:
            gate=snapshot(1).get('gate_transit')
            return gate is not None and gate['camera_sent']

        def pump() -> None:
            for index, client in enumerate(clients):
                for frame in client.poll():
                    if frame.opcode==0x92 and frame.body==struct.pack('>II',4,101654): cameras[index]+=1
                    if frame.opcode==0x19 and len(frame.body)==8: selected[index].add(struct.unpack_from('<I',frame.body)[0])

        def wait(until:Callable[[],bool], seconds:float=12.) -> None:
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                pump()
                if until():return
            raise TimeoutError('Gate transition condition was not confirmed; no activation retry sent')

        def action(index:int, number:int, target:int=101654) -> None:
            client=clients[index]
            client.send(0x2c,struct.pack('<4I',client.ticket.player_id,number,target,0))

        def replace(release_block: Path | None = None) -> subprocess.Popen[bytes]:
            assert process is not None
            (state/'stop.request').write_text('restart\n')
            process.wait(timeout=5)
            if release_block is not None: release_block.rmdir()
            return start_server(binary,host,state,ROOT/'data/asteroid_belt_alpha.json',log)

        try:
            process=start_server(binary,host,state,ROOT/'data/asteroid_belt_alpha.json',log)
            clients=[connect(host,password='example'),connect(host,'bob','example','Bob')]
            endpoints=[client.sector.getsockname() for client in clients]
            for client in clients:client.send(0x17,struct.pack('<II',client.ticket.player_id,101654))
            wait(lambda:all(snapshot(index)['target']==101654 for index in range(2)))
            action(0,18);action(1,18)
            wait(lambda:all(snapshot(index).get('gate_transit') is not None for index in range(2)))
            assert cameras==[0,0]
            blocked=state/'session.local.json.next'
            if fail_mirror: blocked.mkdir()
            client=clients[0]
            body=struct.pack('<4I',client.ticket.player_id,19,101654,0)
            repeated=encode_udp(0x2c,client.ticket.player_id,body,client.sequence)
            client.send(0x2c,body)
            wait(completed_early)
            assert snapshot(0)['sector']==1076
            if fail_mirror:
                wait(lambda:(state/'sector-transition.local.json').exists())
                assert snapshot(0)['sector']==1076 and clients[0].ticket.sector_id==1076
                saved_player=json.loads((state/'player.json').read_text())
                assert saved_player['sector']==1071
                assert clients[1].ticket.sector_id==1076
                clients[1].send(0x17,struct.pack('<II',clients[1].ticket.player_id,102253))
                wait(lambda:102253 in selected[1])
                print('PASS: committed journal holds handoff while location/checkpoint mirrors disagree; other client remains active',flush=True)
            process=replace(blocked if fail_mirror else None)
            wait(lambda:clients[0].ticket.sector_id==1071 and clients[0].started and cameras[1]==1)
            assert snapshot(1)['sector']==1076 and snapshot(1)['gate_transit'] is not None
            assert clients[0].sector.getsockname()==endpoints[0]
            assert not (state/'sector-transition.local.json').exists()
            print('PASS: two independent gate jobs; early completion survives replacement and enters real Saturn on the same UDP socket',flush=True)
            process=replace()
            wait(camera_saved)
            assert cameras==[0,1]
            action(1,19,100027) # A destination gate cannot replace the authorized source.
            deadline=time.monotonic()+0.5
            while time.monotonic()<deadline:pump()
            assert snapshot(1)['sector']==1076
            action(1,19)
            wait(lambda:clients[1].ticket.sector_id==1071 and clients[1].started)
            assert clients[1].sector.getsockname()==endpoints[1] and cameras[1]==1
            clients[0].sector.send(repeated)
            deadline=time.monotonic()+0.5
            while time.monotonic()<deadline:pump()
            assert all(snapshot(index)['sector']==1071 and snapshot(index)['gate_transit'] is None for index in range(2))
            print('PASS: camera already sent survives another replacement without replay; wrong-target and old completions cannot redirect or duplicate a handoff',flush=True)
        except BaseException:
            print(log.read_text(errors='replace'))
            raise
        finally:
            for client in clients:client.close()
            if process is not None and process.poll() is None:
                process.terminate();process.wait(timeout=5)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'target/debug'/('enb-server.exe' if os.name=='nt' else 'enb-server'))
    parser.add_argument('--server',default='127.0.0.10')
    parser.add_argument('--fail-mirror',action='store_true',help='Block a transition checkpoint write, then recover the committed journal after replacement')
    args=parser.parse_args()
    host=sandbox_address(args.server)
    if registered(host):raise SystemExit('Use an unused disposable server address')
    exercise(args.binary.resolve(),host,fail_mirror=args.fail_mirror)


if __name__=='__main__':main()
