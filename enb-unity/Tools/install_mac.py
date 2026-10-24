"""Install the macOS game outside protected project folders and launch it."""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys

APP_NAME = '42 and Beyond.app'
EXECUTABLE = 'Contents/MacOS/42 and Beyond'


def installed_bundle() -> Path:
    """Returns:
        Path: Stable game bundle in the user's Applications directory.
    """
    return Path.home() / 'Applications' / APP_NAME


def installed_executable() -> Path:
    """Returns:
        Path: Executable inside the stable application bundle.
    """
    return installed_bundle() / EXECUTABLE


def running_client(client: Path) -> bool:
    """Returns:
        bool: Whether the exact installed executable is running.
    """
    inventory = subprocess.check_output(['/bin/ps', '-ax', '-o', 'comm='], text=True)
    return any(line.strip() == str(client) for line in inventory.splitlines())


def install(project: Path) -> Path:
    """Copy a newer build into Applications without replacing a running client.

    Returns:
        Path: Installed app bundle ready to launch.

    Raises:
        FileNotFoundError: The Unity build is absent.
        RuntimeError: The installed game must be closed before replacement.
    """
    source = project / 'Builds' / APP_NAME
    built_client = source / EXECUTABLE
    if not built_client.is_file():
        raise FileNotFoundError(f'Build the Unity client first: {source}')
    destination = installed_bundle()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.parent / '.42-and-beyond-install.lock'
    with lock_path.open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = destination / EXECUTABLE
        if current.is_file() and current.stat().st_mtime_ns >= built_client.stat().st_mtime_ns:
            return destination
        if running_client(current):
            raise RuntimeError('Close 42 and Beyond before installing a new build.')
        staging = destination.parent / '.42-and-beyond.installing'
        backup = destination.parent / '.42-and-beyond.previous'
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(source, staging, symlinks=True, copy_function=shutil.copy2)
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(staging, destination)
        except BaseException:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    return destination


def launch_vm(project: Path) -> None:
    """Install and activate the VM client with its log in Library/Logs."""
    destination = installed_bundle()
    if running_client(installed_executable()):
        subprocess.run(['/usr/bin/open', '-a', str(destination)], check=True)
        return
    destination = install(project)
    logs = Path.home() / 'Library' / 'Logs' / '42 and Beyond'
    logs.mkdir(parents=True, exist_ok=True)
    subprocess.run(['/usr/bin/open', '-a', str(destination), '--args', '--enb-vm',
                    '-logFile', str(logs / 'player.log')], check=True)


def main() -> None:
    """Run the protected-folder-free VM launcher from a command file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch-vm', action='store_true')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    try:
        if args.launch_vm:
            launch_vm(project)
        else:
            print(install(project))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'42 and Beyond could not start: {error}', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
