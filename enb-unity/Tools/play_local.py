"""Build and launch the Unity 3D game with its own local Rust server."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import TextIO

from install_mac import install, installed_executable


class LaunchLock:
    """Serialize launches for one project, releasing ownership on process exit."""

    def __init__(self, path: Path) -> None:
        """Keep the lock path without opening or truncating any session files."""
        self.path = path
        self.file: TextIO | None = None

    def acquire(self) -> bool:
        """Returns:
            bool: Whether this process acquired exclusive launch ownership.
        """
        self.file = self.path.open('a+')
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.release()
            return False
        except OSError:
            self.release()
            raise
        return True

    def release(self) -> None:
        """Close the descriptor without unlinking the shared lock inode."""
        if self.file is not None:
            self.file.close()
            self.file = None


def running_client(client: Path) -> bool:
    """Returns:
        bool: Whether an exact executable-path match is currently running.

    Raises:
        subprocess.CalledProcessError: The process inventory could not be read.
    """
    inventory = subprocess.check_output(['/bin/ps', '-ax', '-o', 'comm='], text=True)
    return any(line.strip() == str(client) for line in inventory.splitlines())


def show_existing(client: Path) -> bool:
    """Bring this project's running Mac application forward.

    Returns:
        bool: Whether an existing client was found and activated.
    """
    if not running_client(client):
        return False
    subprocess.run(['/usr/bin/open', '-a', str(client.parents[2])], check=True)
    print('ENB is already running; brought the existing game forward.', flush=True)
    return True


def find_editor(explicit: str | None) -> Path:
    """Find the requested editor or an installed Unity 6.3 editor.

    Returns:
        Path: Executable suitable for a batch build.

    Raises:
        FileNotFoundError: No compatible editor is installed.
    """
    if explicit:
        editor = Path(explicit).expanduser()
        if editor.is_file():
            return editor
        raise FileNotFoundError(f'Unity editor does not exist: {editor}')
    roots = [Path('/Applications/Unity/Hub/Editor'), Path.home() / 'Applications/Unity/Hub/Editor']
    for root in roots:
        matches = sorted(root.glob('6000.3.*/Unity.app/Contents/MacOS/Unity'), reverse=True)
        if matches:
            return matches[0]
    raise FileNotFoundError('Unity 6.3 is not installed. Supply --editor PATH if it is installed elsewhere.')


def stop_owned(process: subprocess.Popen[bytes]) -> None:
    """Wait briefly, then terminate only a process owned by this launcher."""
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_game(project: Path, editor: str | None, rebuild: bool, sandbox_map: bool) -> None:
    """Build when necessary and own both game and server through shutdown."""
    root = project.parent
    builds = project / 'Builds'
    builds.mkdir(exist_ok=True)
    built_client = builds / '42 and Beyond.app/Contents/MacOS/42 and Beyond'
    if rebuild or not built_client.is_file():
        subprocess.run([
            str(find_editor(editor)), '-batchmode', '-quit', '-projectPath', str(project),
            '-executeMethod', 'Enb.Editor.BuildClient.BuildMac', '-logFile', str(builds / 'build.log'),
        ], check=True)
    client = install(project) / 'Contents/MacOS/42 and Beyond'
    cargo = shutil.which('cargo') or str(Path.home() / '.cargo/bin/cargo')
    subprocess.run([cargo, 'build', '--locked', '--manifest-path', str(root / 'enb-server/Cargo.toml')], check=True)
    state = project / '.local-state'
    state.mkdir(exist_ok=True)
    executable = root / 'enb-server/target/debug' / ('enb-server.exe' if os.name == 'nt' else 'enb-server')
    content = root / 'enb-server/data' / ('sector.json' if sandbox_map else 'asteroid_belt_alpha.json')
    # Append so a failed attempt never destroys another session's diagnostics.
    with (builds / 'server.log').open('a+') as log:
        log.seek(0, os.SEEK_END)
        attempt_offset = log.tell()
        server = subprocess.Popen([
            str(executable), '--bind', '127.0.0.1', '--state-dir', str(state), '--content', str(content),
        ], stdout=log, stderr=subprocess.STDOUT)
        game: subprocess.Popen[bytes] | None = None
        try:
            # Check the status file's PID so stale state cannot authorize a launch.
            deadline = time.monotonic() + 15
            while True:
                if server.poll() is not None:
                    log.seek(attempt_offset)
                    detail = log.read()
                    if 'Address already in use' in detail or 'AddrInUse' in detail:
                        raise RuntimeError('Another server is already using the local game ports. '
                                           'Close its launcher before starting this game. No existing server was stopped.')
                    raise RuntimeError(f'Local server exited; see {builds / "server.log"}.')
                try:
                    status = json.loads((state / 'server.local.json').read_text())
                    if isinstance(status, dict) and status.get('pid') == server.pid:
                        break
                except (OSError, ValueError):
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError('Local server did not become ready.')
                time.sleep(.1)
            logs = Path.home() / 'Library/Logs/42 and Beyond'
            logs.mkdir(parents=True, exist_ok=True)
            game = subprocess.Popen([str(client), '--enb-local', '-logFile', str(logs / 'local-player.log')])
            print('42 and Beyond is running. Close the game or press Ctrl+C here to stop its local server.', flush=True)
            game.wait()
        except KeyboardInterrupt:
            print('Stopping the owned local game and server.', flush=True)
        finally:
            if game is not None and game.poll() is None:
                game.terminate()
                stop_owned(game)
            if server.poll() is None:
                (state / 'stop.request').write_text('stop\n')
            stop_owned(server)


def launch(project: Path, editor: str | None, rebuild: bool, sandbox_map: bool) -> None:
    """Focus an existing game or serialize one new game/server lifetime."""
    state = project / '.local-state'
    state.mkdir(exist_ok=True)
    client = installed_executable()
    lock = LaunchLock(state / 'launcher.lock')
    acquired = lock.acquire()
    try:
        # This also recognizes a game started by the previous, lockless launcher.
        if show_existing(client):
            if rebuild or sandbox_map:
                print('Close the running game before rebuilding or changing maps.', flush=True)
            return
        if not acquired:
            print('ENB is already starting or shutting down. Wait for that launcher to finish.', flush=True)
            return
        run_game(project, editor, rebuild, sandbox_map)
    finally:
        lock.release()


def main() -> None:
    """Report actionable launcher errors without a Python traceback."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', help='Path to the Unity editor executable')
    parser.add_argument('--rebuild', action='store_true', help='Rebuild the Unity application')
    parser.add_argument('--sandbox-map', action='store_true', help='Use the small four-object map')
    args = parser.parse_args()
    try:
        launch(Path(__file__).resolve().parents[1], args.editor, args.rebuild, args.sandbox_map)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'ENB could not start: {error}', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
