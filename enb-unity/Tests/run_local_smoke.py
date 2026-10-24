"""Exercise the C# client against an owned Rust server with disposable state."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def main() -> None:
    """Build both components and always stop the test-owned server afterward."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dotnet', default='dotnet')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    server_root = root / 'enb-server'
    project = Path(__file__).with_name('ProtocolChecks.csproj')
    cargo = shutil.which('cargo')
    if cargo is None:
        raise FileNotFoundError('Install a Rust toolchain to run the local smoke test.')
    subprocess.run([cargo, 'build', '--locked', '--manifest-path', str(server_root / 'Cargo.toml')], check=True)
    subprocess.run([args.dotnet, 'build', str(project), '-p:NuGetAudit=false', '-p:UseSharedCompilation=false'], check=True)
    executable = server_root / 'target' / 'debug' / ('enb-server.exe' if os.name == 'nt' else 'enb-server')
    with tempfile.TemporaryDirectory(prefix='enb-unity-smoke-') as temporary:
        state = Path(temporary)
        for account, character, password in [('local', 'SandboxPilot', 'local'), ('peer', 'Visitor', 'pass')]:
            subprocess.run([
                str(executable), '--state-dir', str(state), '--create-account', account,
                '--character', character,
            ], input=password + '\n', text=True, check=True)
        (state / 'sandbox_loadout.json').write_bytes((server_root / 'data' / 'sandbox_loadout.json').read_bytes())
        sector = json.loads((server_root / 'data' / 'sector.json').read_text())
        for point in sector['navpoints']:
            if point['id'] == 100001:
                point['id'] = 100002
                point['gate_destination'] = 10761
            elif point['id'] == 100002:
                point['id'] = 100004
        sector_path = state / 'sector-with-station.json'
        sector_path.write_text(json.dumps(sector))
        with (state / 'server.log').open('w+') as log:
            process = subprocess.Popen([
                str(executable), '--bind', '127.0.0.1', '--state-dir', str(state),
                '--content', str(sector_path),
            ], stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 15
                while not (state / 'server.local.json').exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Disposable server did not start; check whether its ports are already in use.')
                    time.sleep(.05)
                subprocess.run([
                    args.dotnet, str(project.parent / 'bin' / 'Debug' / 'net8.0' / 'ProtocolChecks.dll'),
                    '--server', '127.0.0.1',
                ], check=True, timeout=140)
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read())
                raise
            finally:
                (state / 'stop.request').write_text('stop\n')
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == '__main__':
    main()
