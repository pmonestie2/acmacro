"""Stop one character's macros, named game clients and their Net-7 proxies."""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import win32con
import win32event
import win32gui
import win32process

from arrange_windows import client_path
from config import PROJECT_DIR
from focus import process_identity
from kill_games import collect_claims, command_arguments, macro_script, terminate
from macro_control import active_statuses
from player import load_player_config


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    parent: int
    name: str
    command: str
    born: str


def process_snapshot() -> dict[int, ProcessInfo]:
    query = ("Get-CimInstance Win32_Process -Filter \"Name='client.exe' OR "
             "Name='net7proxy.exe' OR Name='python.exe' OR Name='pythonw.exe'\" "
             "| Select-Object ProcessId,ParentProcessId,Name,CommandLine "
             "| ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', query],
                            capture_output=True, text=True, check=True, timeout=15)
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(rows, dict):
        rows = [rows]
    found: dict[int, ProcessInfo] = {}
    for row in rows:
        pid = int(row['ProcessId'])
        born = process_identity(pid)
        if born:
            found[pid] = ProcessInfo(pid, int(row['ParentProcessId']), str(row['Name']),
                                     str(row.get('CommandLine') or ''), born)
    return found


def named_clients(player: str, processes: dict[int, ProcessInfo]) -> set[int]:
    """Exact character titles only; a shared installation never implies ownership."""
    found: set[int] = set()

    def inspect(hwnd: int, unused: object) -> None:
        if win32gui.GetWindowText(hwnd).casefold() != f'ENB_{player}'.casefold():
            return
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        process = processes.get(pid)
        if (process is not None and process.name.casefold() == 'client.exe'
                and process_identity(pid) == process.born
                and client_path(pid).name.casefold() == 'client.exe'):
            found.add(pid)

    win32gui.EnumWindows(inspect, None)
    return found


def macro_player(command: str) -> str | None:
    """Resolve the explicit config of a recognized repository macro."""
    script = macro_script(command)
    if script is None:
        return None
    args = command_arguments(command)
    for index, argument in enumerate(args[1:], 1):
        if Path(argument).name.casefold() == script.casefold():
            if index + 1 >= len(args) or args[index + 1].startswith('-'):
                return None
            path = Path(args[index + 1])
            if not path.is_absolute():
                path = PROJECT_DIR / path
            try:
                return load_player_config(path).name
            except (OSError, ValueError):
                return None
    return None


def resolve_player(value: str) -> str:
    """Accept a character, its ENB window title, or a player config."""
    value = value.strip()
    if not value:
        raise ValueError('Specify a character name, for example macboyje')
    path = Path(value)
    if path.is_file() or path.suffix.casefold() == '.txt' or '/' in value or '\\' in value:
        # Resolve in the caller's directory before elevation changes that directory.
        try:
            name = load_player_config(path.resolve()).name.strip()
        except (OSError, ValueError) as error:
            raise ValueError(f'Cannot read player config {path}: {error}') from error
        if not name:
            raise ValueError(f'Player config {path} has no character name')
        return name
    if value.casefold().startswith('enb_'):
        value = value[4:].strip()
        if not value:
            raise ValueError('Specify a character after ENB_, for example ENB_macboytt')
    return value


def stop_player(player: str, *, list_only: bool = False) -> list[str]:
    player = resolve_player(player)
    processes = process_snapshot()
    clients = named_clients(player, processes)
    macros: dict[int, ProcessInfo] = {}
    for member in [*collect_claims(), *active_statuses()]:
        if str(member['toon']).casefold() != player.casefold():
            continue
        process = processes.get(member['pid'])
        if (process is not None and process.born == member['born']
                and process.name.casefold() in ('python.exe', 'pythonw.exe')):
            macros[process.pid] = process
    for process in processes.values():
        if process.name.casefold() not in ('python.exe', 'pythonw.exe'):
            continue
        owner = macro_player(process.command)
        if owner is not None and owner.casefold() == player.casefold():
            macros[process.pid] = process

    proxies: dict[int, ProcessInfo] = {}
    for pid in clients:
        parent = processes.get(processes[pid].parent)
        if parent is None or parent.name.casefold() != 'net7proxy.exe':
            continue
        if parent.born >= processes[pid].born:
            continue  # A reused parent PID does not own an older client.
        # A proxy shared with an unselected client must remain alive.
        if any(p.name.casefold() == 'client.exe' and p.parent == parent.pid
               and p.pid not in clients for p in processes.values()):
            continue
        if (process_identity(parent.pid) == parent.born
                and client_path(parent.pid).name.casefold() == 'net7proxy.exe'):
            proxies[parent.pid] = parent

    output: list[str] = []
    # Capture ownership before stopping macros, and stop recovery loops first.
    targets = [('macro', process) for process in macros.values()]
    targets.extend(('client', processes[pid]) for pid in sorted(clients))
    targets.extend(('proxy', process) for process in proxies.values())
    for kind, process in targets:
        if list_only or terminate(process.pid, process.born):
            output.append(f'{"Running" if list_only else "Stopped"} {player} {kind} '
                          f'(PID {process.pid})')
    return output or [f'No matching games or macros found for {player}.']


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('player', help='Character name, exact ENB_<name> window title, or player config file')
    parser.add_argument('--list', action='store_true', help='Preview without stopping anything')
    parser.add_argument('--report', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        args.player = resolve_player(args.player)
    except ValueError as error:
        parser.error(str(error))
    if not ctypes.windll.shell32.IsUserAnAdmin():
        from win32com.shell import shell, shellcon
        with tempfile.TemporaryDirectory(prefix='enb-player-stop-') as directory:
            report = Path(directory) / 'result.txt'
            parameters = [str(Path(__file__).resolve()), args.player, '--report', str(report)]
            if args.list:
                parameters.append('--list')
            result = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb='runas',
                lpFile=sys.executable, lpParameters=subprocess.list2cmdline(parameters), nShow=win32con.SW_HIDE)
            handle = result['hProcess']
            try:
                win32event.WaitForSingleObject(handle, win32event.INFINITE)
                code = int(win32process.GetExitCodeProcess(handle))
            finally:
                handle.Close()
            print(report.read_text(encoding='utf-8') if report.exists() else 'Helper produced no report.')
            return code
    try:
        output = '\n'.join(stop_player(args.player, list_only=args.list))
        code = 0
    except Exception as error:
        output, code = f'Could not stop games for {args.player}: {error}', 1
    if args.report:
        args.report.write_text(output, encoding='utf-8')
    else:
        print(output)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
