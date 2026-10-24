"""Nine class logins and class-correct peer models on a disposable server."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import time
from build import ROOT
from smoke_support import start_server
from smoke_test import connect
from start_proxy import registered
from enb_protocol.session import SpaceSession

CODES=('te','tt','ts','jd','js','je','pw','pp','ps')

def exercise(binary: Path, host: str) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-classes-') as temporary:
        folder=Path(temporary)
        state=folder/'state'
        for code in CODES:
            subprocess.run([str(binary),'--create-account',code,'--character',code.upper(),'--class',code,
                '--state-dir',str(state)],input='pass\n',text=True,capture_output=True,check=True)
        (state/'sandbox_loadout.json').write_bytes((ROOT/'data/sandbox_loadout.json').read_bytes())
        content=json.loads((ROOT/'data/sector.json').read_text())
        content['mobs']=[]
        content_file=folder/'sector.json'
        content_file.write_text(json.dumps(content))
        process: subprocess.Popen[bytes] | None=None
        clients: list[SpaceSession]=[]
        seen: dict[int,int]={}
        try:
            process=start_server(binary,host,state,content_file,folder/'server.log')
            for index,code in enumerate(CODES):
                clients.append(connect(host,code,'pass',code.upper()))
                directory=state if index==0 else state/'accounts'/code
                account=json.loads((directory/'account.local.json').read_text())['character']
                assert (account['race'],account['profession'])==divmod(index,3)
                owned=json.loads((directory/'inventory.json').read_text())
                assert owned['credits']==100_000_000 and owned['sandbox_grant']==1
                assert len([item for item in owned['equipped'][3:9] if item])==(5,4,4,5,4,3,6,5,4)[index]
                deadline=time.monotonic()+.4
                while time.monotonic()<deadline:
                    for client in clients:
                        for frame in client.poll():
                            if frame.opcode==4 and len(frame.body)==23:
                                identity=struct.unpack_from('<I',frame.body)[0]
                                if identity & 0xff000000 == 0x40000000:
                                    seen[identity]=struct.unpack_from('<H',frame.body,8)[0]
                print(f'PASS: {code.upper()} authenticated; own class, skills, mounts and elite inventory loaded',flush=True)
            # enter() consumes peers that already existed during login. Relog
            # the first pilot so its Create is observed by the other eight
            # established streams as well, with removal confirmed first.
            first=clients[0].ticket.player_id
            clients[0].close()
            removed=False
            deadline=time.monotonic()+5
            while not removed and time.monotonic()<deadline:
                for client in clients[1:]:
                    for frame in client.poll():
                        if frame.opcode==7 and frame.body==struct.pack('<I',first): removed=True
            if not removed: raise TimeoutError('First pilot departure was not observed')
            clients[0]=connect(host,'te','pass','TE')
            deadline=time.monotonic()+5
            while first not in seen and time.monotonic()<deadline:
                for client in clients:
                    for frame in client.poll():
                        if frame.opcode==4 and len(frame.body)==23:
                            identity=struct.unpack_from('<I',frame.body)[0]
                            if identity & 0xff000000 == 0x40000000:
                                seen[identity]=struct.unpack_from('<H',frame.body,8)[0]
            expected={client.ticket.player_id:(1600,1603,1606,1609,1612,1615,1618,1621,1624)[index]
                for index,client in enumerate(clients)}
            assert seen==expected,(seen,expected)
            assert len({client.sector.getsockname() for client in clients})==9
            print('PASS: all nine sessions coexist and peers receive nine class-correct hull models',flush=True)
        except BaseException:
            print((folder/'server.log').read_text(errors='replace'))
            raise
        finally:
            for client in clients: client.close()
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
