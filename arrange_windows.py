"""Rename and position running E&B windows without starting the macro."""
import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys
from typing import Literal

import win32con
import win32api
import win32gui
import win32process


CHARACTER_POSITIONS: dict[str, tuple[Literal['left', 'center', 'right'], bool]] = {
    'macboyje': ('left', False),
    'macboypw': ('right', False),
    'macboytt': ('left', True),
    'macboyte': ('right', True),
    'macboyts': ('center', False),
    'macboyapw': ('center', True),
}


def character_position(hwnd: int | None, toon: str) -> tuple[int, int] | None:
    """Return this character's fixed position in the primary monitor's work area."""
    position = CHARACTER_POSITIONS.get(toon.casefold())
    if position is None or hwnd is None:
        return None
    horizontal, bottom_side = position
    area = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0), 1))['Work']
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    if horizontal == 'center':
        x = max(area[0], area[0] + (area[2] - area[0] - width) // 2)
    elif horizontal == 'right':
        x = max(area[0], area[2] - width)
    else:
        x = area[0]
    return (x,
            max(area[1], area[3] - (bottom - top)) if bottom_side else area[1])


def client_path(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(buffer.value).resolve()
    finally:
        kernel.CloseHandle(handle)


def find_client(path: str | Path, toon: str | None = None) -> int:
    matches = []
    wanted = Path(path).resolve()

    def inspect(hwnd: int, unused: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if "Earth & Beyond" not in title and not title.startswith("ENB_") and title != "EBPopup":
            return
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        if client_path(pid) == wanted:
            matches.append(hwnd)

    win32gui.EnumWindows(inspect, None)
    if toon is not None:
        named = [hwnd for hwnd in matches if win32gui.GetWindowText(hwnd).casefold() == f'ENB_{toon}'.casefold()]
        if named:
            matches = named
        else:
            matches = [hwnd for hwnd in matches if win32gui.GetWindowText(hwnd) in ('Earth & Beyond', 'EBPopup')]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one running game at {wanted}; found {len(matches)}")
    return matches[0]


def visible_position(hwnd, x, y):
    """Clamp the entire window, including borders, to its destination monitor."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    x = left if x is None else x
    y = top if y is None else y
    monitor = win32api.MonitorFromRect((x, y, x + width, y + height), 2)
    work_left, work_top, work_right, work_bottom = win32api.GetMonitorInfo(monitor)["Work"]
    return (max(work_left, min(x, work_right - width)),
            max(work_top, min(y, work_bottom - height)))


def arrange(targets: list[tuple[str, Path, bool]], x: int | None = None, y: int | None = None) -> None:
    from focus import SharedFocus
    coordination = SharedFocus("window-layout", "WindowLayout", claim_game=False)
    try:
        coordination.acquire()
        coordination.depth = 1
        arrange_owned(targets, x, y)
    finally:
        coordination.depth = 0
        coordination.close()


def arrange_owned(targets: list[tuple[str, Path, bool]], x: int | None = None, y: int | None = None) -> None:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    area = wintypes.RECT()
    if not ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(area), 0):
        raise ctypes.WinError()
    # Resolve every target before changing any window.
    windows = [(find_client(path, toon), toon, corner) for toon, path, corner in targets]
    for hwnd, toon, corner in windows:
        if win32gui.GetClassName(hwnd) == "#32770":
            win32gui.SetWindowText(hwnd, "EBPopup")
            print("EBPopup: startup dialog position preserved")
            continue
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        target_x = x if x is not None else (
            max(area.left, area.right - (right - left)) if corner else area.left
        )
        target_y = y if y is not None else (
            max(area.top, area.bottom - (bottom - top)) if corner else area.top
        )
        fixed = character_position(hwnd, toon)
        if fixed is not None:
            target_x, target_y = fixed
        target_x, target_y = visible_position(hwnd, target_x, target_y)
        title = f"ENB_{toon}"
        win32gui.SetWindowText(hwnd, title)
        win32gui.SetWindowPos(
            hwnd, 0, target_x, target_y, 0, 0,
            win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
        )
        bounds = win32gui.GetWindowRect(hwnd)
        if win32gui.GetWindowText(hwnd) != title or bounds[:2] != (target_x, target_y):
            raise RuntimeError(f"Could not apply window layout for {toon}")
        print(f"{title}: position {bounds[:2]}")


def named_games() -> list[tuple[str, Path, bool]]:
    """Find the named characters, including clients sharing an installation."""
    targets: list[tuple[str, Path, bool]] = []

    def inspect(hwnd: int, unused: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title.casefold().startswith('enb_'):
            return
        toon = title[4:]
        if toon.casefold() not in CHARACTER_POSITIONS:
            return
        try:
            path = client_path(win32process.GetWindowThreadProcessId(hwnd)[1])
        except OSError:
            return  # A client that exited during enumeration is not a target.
        targets.append((toon, path, False))

    win32gui.EnumWindows(inspect, None)
    return targets


def main():
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toon", help="Toon name for a single game")
    parser.add_argument("--client", type=Path, help="Client executable for a single game")
    parser.add_argument("--x", type=int, help="Physical screen X coordinate")
    parser.add_argument("--y", type=int, help="Physical screen Y coordinate")
    parser.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if bool(args.toon) != bool(args.client):
        parser.error("--toon and --client must be supplied together")
    if (args.x is not None or args.y is not None) and not args.toon:
        parser.error("--x and --y require --toon and --client")
    root = Path(r"C:\Program Files (x86)\EA GAMES")
    targets = [(args.toon, args.client.resolve(), False)] if args.toon else [
        ("Lightbulb", root / "Earth & Beyond/release/client.exe", False),
        ("Lightje", root / "EnB-client-2/release/client.exe", True),
    ]
    if not args.toon:
        targets = named_games() or targets
    if not ctypes.windll.shell32.IsUserAnAdmin():
        if args.elevated:
            raise RuntimeError("Administrator rights are required to move the game windows")
        import win32event
        from win32com.shell import shell, shellcon

        arguments = [str(Path(__file__).resolve())]
        if args.toon:
            arguments += ["--toon", args.toon, "--client", str(args.client.resolve())]
        for name in ("x", "y"):
            value = getattr(args, name)
            if value is not None:
                arguments += [f"--{name}", str(value)]
        arguments.append("--elevated")
        print("Requesting administrator access; accept the UAC prompt.", flush=True)
        result = shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb="runas",
            lpFile=sys.executable, lpParameters=subprocess.list2cmdline(arguments),
            nShow=win32con.SW_HIDE,
        )
        handle = result["hProcess"]
        try:
            win32event.WaitForSingleObject(handle, win32event.INFINITE)
            status = win32process.GetExitCodeProcess(handle)
        finally:
            handle.Close()
        if status:
            raise RuntimeError("Elevated window arrangement failed; run this command in an administrator terminal for details")
        for toon, path, unused in targets:
            hwnd = find_client(path, toon)
            print(f"{win32gui.GetWindowText(hwnd)}: position {win32gui.GetWindowRect(hwnd)[:2]}")
    else:
        arrange(targets, args.x, args.y)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Cannot arrange game windows: {error}", file=sys.stderr)
        raise SystemExit(1)
