"""Build/restart the owned Rust sandbox while preserving its space sessions.

The server checkpoints its UDP state. This tool requests a graceful stop instead
of terminating a process, then launches the replacement with the same arguments.
It never closes Net7Proxy or the graphical game. Build in a separate Cargo target
while the old server remains playable; only a finished binary requests shutdown.
A terminal startup failure restores the previous executable. A live process that
has not answered the readiness probe is left running for further inspection.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from build import ROOT, build
from start_proxy import registered


def install_staged(source: Path, destination: Path) -> None:
    """Prepare a complete sibling file before replacing an unmapped executable."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.next-' + uuid.uuid4().hex)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def launch(command: list[str], logs: Path) -> subprocess.Popen[bytes]:
    logs.mkdir(exist_ok=True)
    with (logs / 'server.log').open('ab') as output:
        return subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def wait_ready(process: subprocess.Popen[bytes], bind: str, timeout: float = 30.) -> None:
    deadline = time.monotonic() + timeout
    while True:
        code = process.poll()
        if code is not None:
            raise RuntimeError(f'Replacement exited with code {code}; see enb-server/logs/server.log')
        if registered(bind) and process.poll() is None:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f'Server PID {process.pid} is still running without a readiness reply; no duplicate or fallback launched')
        time.sleep(.1)


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
    # Cargo must not link over a running Windows image. This persistent build
    # cache is never executed directly, so later builds remain possible too.
    staged: Path | None = None
    if not no_build:
        print(f'Building replacement while owned server PID {pid} stays running', flush=True)
        try:
            staged = build(target_dir=ROOT / 'target/restart-build')
        except Exception:
            print('Build failed; no shutdown requested; current server remains running', flush=True)
            raise
    if process_identity(pid) != identity:
        raise RuntimeError('Owned server changed while building; no stop requested')
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
    binary = executable if staged is None else ROOT / 'target/debug' / executable.name
    failure: Exception | None = None
    replacement: subprocess.Popen[bytes] | None = None
    try:
        if staged is not None:
            install_staged(staged, binary)
        command[0] = str(binary)
        replacement = launch(command, ROOT / 'logs')
        wait_ready(replacement, str(status['bind']))
    except Exception as error:
        if replacement is not None and replacement.poll() is None:
            raise  # Observation expiry does not establish process termination.
        if registered(str(status['bind'])):
            raise RuntimeError('Server address became occupied; no fallback launched') from error
        failure = error
        print('Replacement could not start; restoring the previous executable', flush=True)
        command[0] = str(backup)
        replacement = launch(command, ROOT / 'logs')
        wait_ready(replacement, str(status['bind']))
    print(f'Server ready: PID {replacement.pid}; eligible game sessions resumed', flush=True)
    if failure is not None:
        raise RuntimeError('Replacement failed; previous server is running') from failure
    return replacement.pid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=ROOT / 'state')
    parser.add_argument('--no-build', action='store_true', help='Restart the current binary without building')
    args = parser.parse_args()
    restart(args.state_dir, no_build=args.no_build)


if __name__ == '__main__':
    main()
