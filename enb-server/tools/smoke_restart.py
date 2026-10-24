"""Verify a real process replacement using one unchanged disposable UDP client."""
from __future__ import annotations

import argparse
from pathlib import Path
import struct
import subprocess
import tempfile
import time

from build import ROOT
from smoke_test import connect, wait_for
from start_proxy import registered


def exercise(binary: Path, host: str) -> None:
    with tempfile.TemporaryDirectory(prefix='enb-restart-') as folder:
        state = Path(folder) / 'state'
        command = [str(binary.resolve()), '--bind', host, '--content', str(ROOT / 'data/sector.json'),
                   '--state-dir', str(state)]
        process: subprocess.Popen[bytes] | None = None
        session = None
        with (Path(folder) / 'server.log').open('wb') as log:
            try:
                process = subprocess.Popen(command, stdout=log, stderr=log)
                deadline = time.monotonic() + 15
                while not registered(host):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Disposable server did not start')
                    time.sleep(.1)
                session = connect(host)
                owner = session.ticket.player_id
                # Start free warp, replace the process while moving, then use
                # the same client object/socket to cancel and move again.
                session.send(0x9b, struct.pack('<IH', owner, 0))
                wait_for(session, lambda: session.position is not None and session.position[0] > 1000,
                         8, 'Warp never started')
                wait_for(session, lambda: (state / 'session.local.json').exists(), 3, 'No checkpoint')
                before = session.stream.expected
                endpoint = session.sector.getsockname()
                (state / 'stop.request').write_text('restart\n')
                process.wait(timeout=5)
                process = subprocess.Popen(command, stdout=log, stderr=log)
                deadline = time.monotonic() + 10
                while not registered(host):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Replacement did not start')
                    time.sleep(.1)
                wait_for(session, lambda: session.stream.expected > before, 5, 'Stream did not resume')
                assert session.sector.getsockname() == endpoint
                session.send(0x14, struct.pack('<IB', owner, 4))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    session.poll()
                stopped = session.position
                deadline = time.monotonic() + .5
                while time.monotonic() < deadline:
                    session.poll()
                assert stopped is not None and session.position == stopped, 'Cancel after restart did not stop motion'
                session.send(0x14, struct.pack('<IB', owner, 2))
                wait_for(session, lambda: session.position is not None and session.position[0] > stopped[0] + 10,
                         4, 'Unchanged session could not move after restart')
                print('PASS: process restart preserves socket, sequence, active warp and input; no relog', flush=True)
            except BaseException:
                print((Path(folder) / 'server.log').read_text(errors='replace'))
                raise
            finally:
                if session is not None:
                    session.close()
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/debug/enb-server.exe')
    parser.add_argument('--server', default='127.0.0.4')
    args = parser.parse_args()
    if registered(args.server):
        raise SystemExit('Use an unused disposable server address')
    exercise(args.binary, args.server)


if __name__ == '__main__':
    main()
