"""One command for E&B utilities; Pluto and vendor macros remain separate."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command', required=True)
    start = commands.add_parser('start', help='Start a named game at its login screen')
    start.add_argument('name', help='Window/character name or player config, e.g. ENB_a or .\\mbboytt.conf')
    start.add_argument('--client', type=Path)
    start.add_argument('--proxy', type=Path)
    start.add_argument('--timeout', type=float, default=60)
    start.add_argument('--check', action='store_true', help='Validate target/config without launching')
    kill = commands.add_parser('kill', help='Stop one character/window and its macro/client/proxy')
    kill.add_argument('name')
    kill.add_argument('--list', action='store_true')
    kill_all = commands.add_parser('kill-all', help='Stop all games and macros')
    kill_all.add_argument('--list', action='store_true')
    find = commands.add_parser('find', aliases=['grep'], help='Lenient search of NPC and item names')
    find.add_argument('text', nargs='+')
    trade = commands.add_parser('trade', help='Open an NPC ID on an exact window')
    trade.add_argument('window')
    trade.add_argument('npc_id', type=int)
    trade.add_argument('--dialogue', action='store_true')
    layout = commands.add_parser('layout', help='Arrange all game windows for their current count')
    layout.add_argument('--fixed', action='store_true', help='Use the fixed character positions')
    layout.add_argument('--list', action='store_true', help='Preview adaptive layout')
    commands.add_parser('windows', help='List windows and proposed adaptive positions')
    drag = commands.add_parser('drag', help='Start or stop the native Alt+drag helper')
    drag.add_argument('action', choices=['start', 'stop'], nargs='?', default='start')
    drag.add_argument('--elevated', action='store_true', help=argparse.SUPPRESS)
    vault = commands.add_parser('vault', help='List vault item IDs, withdraw by ID, or unload hull cargo')
    vault.add_argument('name', help='Character, ENB_<character>, or player config path')
    vault_mode = vault.add_mutually_exclusive_group()
    vault_mode.add_argument('--list', action='store_true', help='List vault contents and item IDs')
    vault_mode.add_argument('--withdraw', type=int, metavar='ITEM_ID')
    vault_mode.add_argument('--all', action='store_true', help='Deposit all eligible hull cargo')
    vault.add_argument('--grep', metavar='TEXT', help='Filter deposits or vault listings by item name or ID')
    vault.add_argument('--quantity', type=int)
    vault.add_argument('--dry-run', action='store_true')
    manufacture = commands.add_parser('manufacture', help='Fill hull with Callipus M2, then sell only those to NPC 36')
    manufacture.add_argument('window', help='Exact character window title; matching player config required')
    manufacture.add_argument('--batches', type=int, default=1, help='Default 1; 0 repeats until stopped')
    manufacture.add_argument('--parallel', type=int, default=1, metavar='N',
                             help='Maximum builds in flight; positive integer, capped by free hull slots')
    for action in ('pause', 'resume', 'status'):
        control = commands.add_parser(action, help=f'{action.capitalize()} macros; omit names for all')
        control.add_argument('names', nargs='*')
    return result


def python_command(script: str, *arguments: str) -> list[str]:
    return [sys.executable, str(ROOT / script), *arguments]


def command_line(args: argparse.Namespace) -> list[str]:
    command: str = args.command
    if command == 'start':
        name = args.name
        if (Path(name).is_file() or Path(name).suffix.casefold() in ('.conf', '.ini', '.txt')
                or '/' in name or '\\' in name):
            name = str(Path(name).resolve())
        options = [name, '--timeout', str(args.timeout)]
        for flag in ('client', 'proxy'):
            path: Path | None = getattr(args, flag)
            if path is not None:
                options += [f'--{flag}', str(path.resolve())]
        if args.check:
            options.append('--check')
        return python_command('start_game.py', *options)
    if command in ('find', 'grep'):
        return python_command('npc_trade.py', '--grep', ' '.join(args.text))
    if command == 'trade':
        return python_command('npc_trade.py', args.window, '--id', str(args.npc_id),
                              *(['--dialogue'] if args.dialogue else []))
    if command == 'kill':
        name = args.name
        # Preserve relative config paths before changing to the repository directory.
        if Path(name).suffix.casefold() == '.txt' or '/' in name or '\\' in name:
            name = str(Path(name).resolve())
        return python_command('kill_player.py', name, *(['--list'] if args.list else []))
    if command == 'kill-all':
        return python_command('kill_games.py', *(['--list'] if args.list else []))
    if command in ('layout', 'windows'):
        fixed = command == 'layout' and args.fixed
        preview = command == 'windows' or args.list
        if fixed and preview:
            raise ValueError('--fixed and --list cannot be combined')
        return python_command('arrange_windows.py' if fixed else 'relayout.py',
                              *(['--list'] if preview else []))
    if command == 'vault':
        from text_search import normalize
        if args.grep is not None and (args.all or args.withdraw is not None or not normalize(args.grep)):
            raise ValueError('--grep needs a name or ID and cannot be combined with --all or --withdraw')
        if not args.list and args.withdraw is None and not args.all and args.grep is None:
            raise ValueError('Select hull items with --grep TEXT, or use --all')
        from kill_player import resolve_player
        value: str = args.name
        if Path(value).suffix.casefold() == '.txt' or '/' in value or '\\' in value:
            config = Path(value).resolve()
        else:
            name = resolve_player(value)
            if not name or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for char in name):
                raise ValueError('Invalid character name')
            config = ROOT / f'config_player_{name.casefold()}.txt'
        options = ['--list'] if args.list else []
        if args.all:
            options.append('--all')
        if args.grep is not None:
            options += ['--grep', args.grep]
        if args.withdraw is not None:
            if not 0 < args.withdraw < 0xfffffffe:
                raise ValueError('--withdraw requires a positive item ID')
            options += ['--withdraw', str(args.withdraw)]
        if args.quantity is not None:
            if args.withdraw is None or args.quantity < 1:
                raise ValueError('--quantity requires --withdraw and must be positive')
            options += ['--quantity', str(args.quantity)]
        if args.dry_run:
            options.append('--dry-run')
        return python_command('vault.py', str(config), *options)
    if command in ('pause', 'resume', 'status'):
        names = [name[4:] if name.casefold().startswith('enb_') else name for name in args.names]
        return python_command('macro_control.py', command, *names)
    if command == 'manufacture':
        if args.batches < 0:
            raise ValueError('--batches must be nonnegative')
        if args.parallel < 1:
            raise ValueError('--parallel must be a positive integer')
        return python_command('manufacture_batch.py', args.window, '--batches', str(args.batches),
                              '--parallel', str(args.parallel))
    raise ValueError(f'Unknown utility: {command}')


def drag_helper(action: str, *, elevated: bool = False) -> int:
    executable = ROOT / 'enb_drag.exe'
    if action == 'start':
        subprocess.Popen([str(executable)], cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
        print('Alt+left-drag helper launched; use its tray menu to disable or exit.')
        return 0
    import win32con
    import win32event
    import win32gui
    import win32process
    from arrange_windows import client_path
    import ctypes
    if not ctypes.windll.shell32.IsUserAnAdmin():
        if elevated:
            raise RuntimeError('Administrator access is required to stop the drag helper')
        from win32com.shell import shell, shellcon
        result = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb='runas',
            lpFile=sys.executable, lpParameters=subprocess.list2cmdline(
                [str(ROOT / 'enb.py'), 'drag', 'stop', '--elevated']), nShow=win32con.SW_HIDE)
        handle = result['hProcess']
        try:
            win32event.WaitForSingleObject(handle, win32event.INFINITE)
            return int(win32process.GetExitCodeProcess(handle))
        finally:
            handle.Close()
    window = win32gui.FindWindow('AcMacro.EnbAltDrag.v1', None)
    if not window:
        print('Alt-drag helper is not running.')
        return 0
    pid = win32process.GetWindowThreadProcessId(window)[1]
    if client_path(pid).resolve() != executable.resolve():
        raise RuntimeError('Drag helper identity differs; no stop sent')
    win32gui.PostMessage(window, win32con.WM_CLOSE, 0, 0)
    print('Alt-drag helper stop requested.')
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    try:
        if args.command == 'drag':
            return drag_helper(args.action, elevated=args.elevated)
        return subprocess.run(command_line(args), cwd=ROOT, check=False).returncode
    except (OSError, ValueError, RuntimeError) as error:
        print(f'E&B utility failed: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
