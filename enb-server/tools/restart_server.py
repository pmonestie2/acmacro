"""Build/restart the owned Rust sandbox while preserving its space sessions.

The server checkpoints its UDP state. This tool requests a graceful stop instead
of terminating a process, then launches the replacement with the same arguments.
It never closes Net7Proxy or the graphical game. A failed build starts the saved
old executable so a compiler error does not leave the player disconnected.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from build import ROOT, build
from start_proxy import registered


def process_identity(pid: int) -> tuple[Path, float] | None:
    """Use OS facilities to check PID birth and executable without psutil."""
    if os.name == 'nt':
        import win32api
        import win32event
        import win32process
        from local_windows import process_path
        try:
            handle = win32api.OpenProcess(0x1000 | 0x100000, False, pid)
        except (OSError, win32api.error) as error:
            if (getattr(error,'winerror',None) or getattr(error,'errno',None)) == 87:
                return None  # ERROR_INVALID_PARAMETER: this PID no longer exists.
            raise  # Access denied is not evidence that a process has exited.
        try:
            if win32event.WaitForSingleObject(handle,0) == win32event.WAIT_OBJECT_0:
                return None
            try:
                return Path(process_path(int(handle))).resolve(), win32process.GetProcessTimes(handle)['CreationTime'].timestamp()
            except (OSError, win32api.error):
                # QueryFullProcessImageName can fail during exit even though
                # OpenProcess succeeded a moment earlier. The pinned process
                # handle becoming signaled proves termination without PID reuse.
                if win32event.WaitForSingleObject(handle,1000) == win32event.WAIT_OBJECT_0:
                    return None
                raise
        finally:
            handle.Close()
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'comm='],
        capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'})
    line = result.stdout.strip()
    if result.returncode or len(line) < 26:
        return None
    started = time.mktime(time.strptime(line[:24], '%a %b %d %H:%M:%S %Y'))
    return Path(line[24:].strip()).resolve(), started


def restart(state: Path, *, no_build: bool) -> int:
    state = state.resolve()
    status = json.loads((state / 'server.local.json').read_text())
    pid = int(status['pid'])
    identity = process_identity(pid)
    if identity is None:
        raise RuntimeError('Recorded server is no longer running')
    executable, started_at = identity
    if (executable.name.lower() not in ('enb-server', 'enb-server.exe')
            or not executable.is_relative_to(ROOT / 'target')
            or abs(started_at - float(status['started_at'])) > 10):
        raise RuntimeError('Recorded server process identity changed; no stop requested')
    command = [str(part) for part in status['command']]
    backup = ROOT / 'target/restart-fallback' / executable.name
    if executable == backup.resolve():
        backup = ROOT / 'target/restart-fallback-2' / executable.name
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable, backup)
    print(f'Restarting owned server PID {pid}; game/proxy stay open', flush=True)
    (state / 'stop.request').write_text('restart\n')
    deadline = time.monotonic() + 15
    while process_identity(pid) == identity:
        if time.monotonic() >= deadline:
            raise TimeoutError('Owned server did not stop gracefully; no replacement launched')
        time.sleep(.1)
    build_error: Exception | None = None
    try:
        binary = executable if no_build else build()
    except Exception as error:
        build_error = error
        binary = backup
        print('Build failed; restoring the previous server executable', flush=True)
    command[0] = str(binary)
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    with (logs / 'server.log').open('ab') as output:
        replacement = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    deadline = time.monotonic() + 15
    while not registered(str(status['bind'])):
        if replacement.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError('Replacement failed to start; see enb-server/logs/server.log')
        time.sleep(.1)
    print(f'Server ready: PID {replacement.pid}; eligible game sessions resumed', flush=True)
    if build_error is not None:
        raise RuntimeError('Build failed; previous server is running') from build_error
    return replacement.pid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=ROOT / 'state')
    parser.add_argument('--no-build', action='store_true', help='Restart the current binary without building')
    args = parser.parse_args()
    restart(args.state_dir, no_build=args.no_build)


if __name__ == '__main__':
    main()
