"""Launch one named E&B client through its own Net-7 proxy, without logging in."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator

import win32con
import win32event
import win32gui
import win32process

from arrange_windows import client_path
from config import GAME_CLIENT, SERVER_IP, net7_proxy
from focus import process_identity
from game_instance import GameInstance, parent_process_id
from player import load_player_config


def window_title(name: str) -> str:
    name = name.strip()
    suffix = name[4:] if name.casefold().startswith('enb_') else name
    if not re.fullmatch(r'[A-Za-z0-9_-]+', suffix):
        raise ValueError('Use a name such as ENB_a or macboytt')
    return f'ENB_{suffix}'


@dataclass(frozen=True)
class LaunchTarget:
    title: str
    client: Path


def resolve_target(value: str, client_override: Path | None = None) -> LaunchTarget:
    """Resolve a title or player config before elevation changes working directories."""
    value = value.strip()
    path = Path(value)
    is_config = (path.is_file() or path.suffix.casefold() in ('.conf', '.ini', '.txt')
                 or '/' in value or '\\' in value)
    if is_config:
        settings = load_player_config(path.resolve())
        title = window_title(settings.name)
        client = GameInstance(settings, shared_install=True).client
    else:
        title, client = window_title(value), GAME_CLIENT
    return LaunchTarget(title, (client_override or client).resolve())


def title_exists(title: str) -> bool:
    found: list[int] = []

    def inspect(hwnd: int, unused: object) -> None:
        if win32gui.GetWindowText(hwnd).casefold() == title.casefold():
            found.append(hwnd)

    win32gui.EnumWindows(inspect, None)
    return bool(found)


@contextmanager
def start_lock(title: str) -> Iterator[None]:
    digest = hashlib.sha256(title.casefold().encode()).hexdigest()
    handle = win32event.CreateMutex(None, False, f'Local\\AcMacro.Start.{digest}')
    acquired = False
    try:
        acquired = win32event.WaitForSingleObject(handle, 0) in (
            win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED)
        if not acquired:
            raise RuntimeError(f'A launch for {title} is already in progress')
        yield
    finally:
        if acquired:
            win32event.ReleaseMutex(handle)
        handle.Close()


@dataclass(frozen=True)
class StartedGame:
    title: str
    client_pid: int
    client_born: str
    proxy_pid: int
    proxy_born: str


def launched_windows(proxy_pid: int, proxy_born: str, client: Path) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []

    def inspect(hwnd: int, unused: object) -> None:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetClassName(hwnd) == '#32770':
            return
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        if parent_process_id(pid) != proxy_pid:
            return
        try:
            born = process_identity(pid)
            if born is not None and born >= proxy_born and client_path(pid).resolve() == client:
                found.append((hwnd, pid, born))
        except OSError:
            return

    win32gui.EnumWindows(inspect, None)
    return found


def start_game(name: str, client: Path, proxy: Path, timeout: float = 60) -> StartedGame:
    title = window_title(name)
    client, proxy = client.resolve(), proxy.resolve()
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
        raise ValueError('Startup timeout must be between 0 and 300 seconds')
    if not client.is_file() or client.name.casefold() != 'client.exe':
        raise FileNotFoundError(f'Game client not found: {client}')
    if not proxy.is_file() or proxy.name.casefold() != 'net7proxy.exe':
        raise FileNotFoundError(f'Net-7 proxy not found: {proxy}')
    with start_lock(title):
        if title_exists(title):
            raise RuntimeError(f'A window named {title} already exists; no game started')
        process = subprocess.Popen([str(proxy), f'/ADDRESS:{SERVER_IP}', f'/CLIENT:{client}',
                                    '/SSL:443', '/POPT', '/DML'], cwd=proxy.parent,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        born = process_identity(process.pid)
        print(f'Starting {title}: proxy PID {process.pid}; waiting for its game window.', flush=True)
        if born is None:
            raise RuntimeError('New proxy exited before its identity could be pinned')
        deadline = time.monotonic() + timeout
        while True:
            if process.poll() is not None or process_identity(process.pid) != born:
                raise RuntimeError('Launched proxy exited or changed before a game window appeared')
            windows = launched_windows(process.pid, born, client)
            if len(windows) > 1:
                raise RuntimeError(f'Multiple windows from new proxy {process.pid}; none renamed')
            if windows:
                hwnd, pid, client_born = windows[0]
                if title_exists(title):
                    raise RuntimeError(f'{title} appeared during launch; new proxy PID {process.pid} left running')
                if (win32process.GetWindowThreadProcessId(hwnd)[1] != pid or
                        process_identity(pid) != client_born or parent_process_id(pid) != process.pid):
                    raise RuntimeError('New client identity changed; no window renamed')
                win32gui.SetWindowText(hwnd, title)
                if win32gui.GetWindowText(hwnd) != title:
                    raise RuntimeError(f'Could not name new client PID {pid}')
                return StartedGame(title, pid, client_born, process.pid, born)
            if time.monotonic() >= deadline:
                raise TimeoutError(f'No game window within {timeout:g}s; new proxy PID {process.pid} '
                                   'left running for inspection; no launch retry')
            time.sleep(.2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', help='Window name (ENB_a), character name, or player config (.conf/.ini/.txt)')
    parser.add_argument('--client', type=Path, help='Override the configured/default game client')
    parser.add_argument('--proxy', type=Path)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--check', action='store_true', help='Validate target/config without starting a game')
    parser.add_argument('--report', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        target = resolve_target(args.name, args.client)
        title, client = target.title, target.client
        proxy = (args.proxy or net7_proxy()).resolve()
        if args.check:
            print(f'Window: {title}\nClient: {client}\nProxy: {proxy}\nManual login; no macro.')
            return 0
        if not ctypes.windll.shell32.IsUserAnAdmin():
            from win32com.shell import shell, shellcon
            with tempfile.TemporaryDirectory(prefix='enb-start-') as directory:
                report = Path(directory) / 'result.txt'
                parameters = [str(Path(__file__).resolve()), title, '--client', str(client),
                              '--proxy', str(proxy), '--timeout', str(args.timeout), '--report', str(report)]
                result = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
                    lpVerb='runas', lpFile=sys.executable,
                    lpParameters=subprocess.list2cmdline(parameters), nShow=win32con.SW_HIDE)
                handle = result['hProcess']
                try:
                    win32event.WaitForSingleObject(handle, win32event.INFINITE)
                    code = int(win32process.GetExitCodeProcess(handle))
                finally:
                    handle.Close()
                print(report.read_text(encoding='utf-8') if report.exists() else 'Launch helper produced no report.')
                return code
        started = start_game(title, client, proxy, args.timeout)
        output = (f'Started {started.title}: client PID {started.client_pid}, proxy PID {started.proxy_pid}. '
                  'Log in manually; no macro started.')
        folder = Path(__file__).resolve().parent / 'diagnostics/named_games'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{title}.json').write_text(json.dumps(asdict(started), indent=2), encoding='utf-8')
        code = 0
    except (OSError, ValueError, RuntimeError) as error:
        output, code = f'Could not start game: {error}', 1
    if args.report:
        args.report.write_text(output, encoding='utf-8')
    else:
        print(output)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
