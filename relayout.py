"""Arrange currently open E&B windows in a grid chosen for their count and size."""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from math import ceil
from pathlib import Path
import subprocess
import sys
import tempfile

import win32con
import win32event
import win32gui
import win32process

from arrange_windows import client_path
from focus import SharedFocus, process_identity


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    pid: int
    born: str
    title: str
    width: int
    height: int


def game_windows() -> list[GameWindow]:
    windows: list[GameWindow] = []

    def inspect(hwnd: int, unused: object) -> None:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetClassName(hwnd) == '#32770':
            return
        title = win32gui.GetWindowText(hwnd)
        if not (title.casefold() == 'enb' or title.casefold().startswith('enb_')
                or title == 'Earth & Beyond'):
            return
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        try:
            if client_path(pid).name.casefold() != 'client.exe':
                return
            born = process_identity(pid)
            if born is None:
                return
            # Minimized HWND bounds are iconic coordinates, not the normal size.
            bounds = win32gui.GetWindowPlacement(hwnd)[4] if win32gui.IsIconic(hwnd) else win32gui.GetWindowRect(hwnd)
            windows.append(GameWindow(hwnd, pid, born, title, bounds[2] - bounds[0], bounds[3] - bounds[1]))
        except OSError:
            return

    win32gui.EnumWindows(inspect, None)
    order = {name: index for index, name in enumerate(
        ('enb_macboyje', 'enb_macboyts', 'enb_macboypw', 'enb_macboytt', 'enb_macboyapw', 'enb_macboyte'))}
    return sorted(windows, key=lambda window: (order.get(window.title.casefold(), 6), window.title.casefold(), window.pid))


def window_names(windows: list[GameWindow]) -> list[str]:
    reserved = {window.title.casefold() for window in windows if window.title.casefold().startswith('enb_')}
    names: list[str] = []
    suffix = 0
    for window in windows:
        if window.title.casefold().startswith('enb_'):
            names.append(window.title)
            continue
        while True:
            label = chr(ord('a') + suffix) if suffix < 26 else f'a{suffix - 25}'
            suffix += 1
            title = f'ENB_{label}'
            if title.casefold() not in reserved:
                reserved.add(title.casefold())
                names.append(title)
                break
    return names


def grid_positions(sizes: list[tuple[int, int]], area: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    if not sizes:
        return []
    left, top, right, bottom = area
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0 or any(w <= 0 or h <= 0 for w, h in sizes):
        raise ValueError('Window and work-area sizes must be positive')
    maximum_width = max(w for w, h in sizes)
    maximum_height = max(h for w, h in sizes)
    count = len(sizes)
    # Maximize available space per window, then prefer fewer unused cells.
    columns = max(range(1, count + 1), key=lambda cols: (
        min(width / (cols * maximum_width), height / (ceil(count / cols) * maximum_height)),
        -(cols * ceil(count / cols) - count)))
    rows = ceil(count / columns)
    positions: list[tuple[int, int]] = []
    for index, (window_width, window_height) in enumerate(sizes):
        row, column = divmod(index, columns)
        row_count = min(columns, count - row * columns)
        span_x = max(0, width - maximum_width)
        span_y = max(0, height - maximum_height)
        x = span_x * column // (row_count - 1) if row_count > 1 else span_x // 2
        y = span_y * row // (rows - 1) if rows > 1 else span_y // 2
        x += (maximum_width - window_width) // 2
        y += (maximum_height - window_height) // 2
        positions.append((left + min(max(0, width - window_width), x),
                          top + min(max(0, height - window_height), y)))
    return positions


def relayout(*, list_only: bool = False) -> list[str]:
    import win32api
    coordination = SharedFocus('adaptive-window-layout', 'WindowLayout', claim_game=False)
    try:
        coordination.acquire()
        coordination.depth = 1
        windows = game_windows()
        if not windows:
            return ['No E&B game windows found on this desktop.']
        area = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0), 1))['Work']
        positions = grid_positions([(window.width, window.height) for window in windows], area)
        names = window_names(windows)
        output: list[str] = []
        for window, title, (x, y) in zip(windows, names, positions):
            if (not win32gui.IsWindow(window.hwnd) or process_identity(window.pid) != window.born
                    or win32process.GetWindowThreadProcessId(window.hwnd)[1] != window.pid
                    or win32gui.GetWindowText(window.hwnd) != window.title):
                raise RuntimeError(f'Window identity changed for PID {window.pid}; stopped layout')
            if not list_only:
                if win32gui.IsIconic(window.hwnd):
                    win32gui.ShowWindow(window.hwnd, win32con.SW_RESTORE)
                if title != window.title:
                    win32gui.SetWindowText(window.hwnd, title)
                win32gui.SetWindowPos(window.hwnd, 0, x, y, 0, 0,
                    win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
                if (win32gui.GetWindowText(window.hwnd) != title
                        or win32gui.GetWindowRect(window.hwnd)[:2] != (x, y)):
                    raise RuntimeError(f'Could not apply layout for {title}')
            output.append(f'{"Preview" if list_only else "Placed"} {title}: ({x}, {y}), '
                          f'{window.width}x{window.height}; PID {window.pid}')
        return output
    finally:
        coordination.depth = 0
        coordination.close()


def main(argv: list[str] | None = None) -> int:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true', help='Preview names and positions without moving windows')
    parser.add_argument('--report', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--elevated', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not ctypes.windll.shell32.IsUserAnAdmin():
        if args.elevated:
            raise PermissionError('Administrator access is required to arrange these game windows')
        from win32com.shell import shell, shellcon
        with tempfile.TemporaryDirectory(prefix='enb-layout-') as directory:
            report = Path(directory) / 'result.txt'
            parameters = [str(Path(__file__).resolve()), '--elevated', '--report', str(report)]
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
            print(report.read_text(encoding='utf-8') if report.exists() else 'Layout helper produced no report.')
            return code
    try:
        output, code = '\n'.join(relayout(list_only=args.list)), 0
    except Exception as error:
        output, code = f'Cannot arrange game windows: {error}', 1
    if args.report:
        args.report.write_text(output, encoding='utf-8')
    else:
        print(output)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
