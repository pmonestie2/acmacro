"""Build the Mac player and capture the actual Unity view using disposable server state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from play_local import find_editor, stop_owned


def main() -> None:
    """Run editor checks without reading or modifying existing player state."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor')
    parser.add_argument('--skip-build', action='store_true')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root = project.parent
    editor = find_editor(args.editor)
    output = project / 'Builds/verification'
    output.mkdir(parents=True, exist_ok=True)
    for name in ('chase.png', 'chart.png', 'asteroid.png', 'station.png', 'gate.png'):
        capture = output / name
        if capture.exists():
            capture.unlink()
    if not args.skip_build:
        subprocess.run([
            str(editor), '-batchmode', '-quit', '-projectPath', str(project),
            '-executeMethod', 'Enb.Editor.BuildClient.BuildMac', '-logFile', str(output / 'build.log'),
        ], check=True, timeout=600)
    cargo = shutil.which('cargo') or str(Path.home() / '.cargo/bin/cargo')
    subprocess.run([cargo, 'build', '--locked', '--manifest-path', str(root / 'enb-server/Cargo.toml')], check=True)
    with tempfile.TemporaryDirectory(prefix='enb-unity-visual-') as temporary:
        state = Path(temporary)
        executable = root / 'enb-server/target/debug/enb-server'
        for account, character, password in (
            ('local', 'SandboxPilot', 'local'), ('peer', 'Visitor', 'pass')
        ):
            subprocess.run([
                str(executable), '--state-dir', str(state), '--create-account', account,
                '--character', character,
            ], input=password + '\n', text=True, check=True, stdout=subprocess.DEVNULL)
        sector = json.loads((root / 'enb-server/data/asteroid_belt_alpha.json').read_text())
        sector['mobs'][0]['spawn'] = [1000.0, 0.0, 0.0]
        hostile = next(mob for mob in sector['mobs'] if mob['aggressive'])
        hostile['spawn'] = [2400.0, 0.0, 0.0]
        hostile['aggressive'] = False
        hostile['aggression'] = 0
        visual_sector = state / 'visual-sector.json'
        visual_sector.write_text(json.dumps(sector))
        with (output / 'server.log').open('w') as log:
            server = subprocess.Popen([
                str(executable), '--bind', '127.0.0.1',
                '--state-dir', str(state), '--content', str(visual_sector),
            ], stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 15
                while not (state / 'server.local.json').exists():
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError(f'Disposable server failed to start; see {output / "server.log"}.')
                    time.sleep(.1)
                status = json.loads((state / 'server.local.json').read_text())
                if status['pid'] != server.pid:
                    raise RuntimeError('Server ownership mismatch.')
                subprocess.run([
                    str(editor), '-projectPath', str(project), '--enb-visual-smoke',
                    '-logFile', str(output / 'visual.log'),
                ], check=True, timeout=420)
            finally:
                if server.poll() is None:
                    (state / 'stop.request').write_text('stop\n')
                stop_owned(server)
    for name in ('chase.png', 'chart.png', 'asteroid.png', 'station.png', 'gate.png'):
        if not (output / name).is_file():
            raise FileNotFoundError(f'Missing Unity screenshot: {name}')
    print(f'PASS: Unity visual smoke. Inspect screenshots in {output}', flush=True)


if __name__ == '__main__':
    main()
