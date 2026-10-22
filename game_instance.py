"""Bind launch, window discovery and shutdown to an owned client process."""
from pathlib import Path
import ctypes
from ctypes import wintypes
from subprocess import Popen
from player import PlayerSettings

import win32api
import win32con
import win32gui
import win32process

from arrange_windows import character_position, client_path, visible_position
from config import GAME_CLIENT, CHAT_LOG_PATH, GAME_CLIENTS
from focus import process_identity


def parent_process_id(pid: int) -> int | None:
    """Read the current parent without WMI or COM."""
    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = Entry(size=ctypes.sizeof(Entry))
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.pid == pid:
                return int(entry.parent)
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return None
    finally:
        kernel.CloseHandle(snapshot)


class GameInstance:
    def __init__(self, settings: PlayerSettings, *, shared_install: bool = False) -> None:
        self.shared_install: bool = shared_install
        self.automatic = not shared_install and not (settings.client_exe or settings.game_dir or settings.window_x is not None or settings.window_y is not None)
        self.slot: int | None = None
        if settings.client_exe:
            self.client = Path(settings.client_exe).resolve()
        elif settings.game_dir:
            self.client = (Path(settings.game_dir) / "release/client.exe").resolve()
        else:
            self.client = GAME_CLIENT.resolve()
        self.chat_log = self.client.parent / "chat.log" if settings.client_exe or settings.game_dir else CHAT_LOG_PATH
        self.title = f"ENB_{settings.name}"
        self.toon_name: str = settings.name
        self.x, self.y = settings.window_x, settings.window_y
        self.pid: int | None = None
        self.born: str | None = None
        self.proxy_pid: int | None = None
        self.proxy_born: str | None = None
        self.window: int | None = None
        self.popup = False
        self.launching = False

    def assign_slot(self, slot: int) -> None:
        self.slot = slot
        self.client = GAME_CLIENTS[slot]
        self.chat_log = self.client.parent / "chat.log"

    def slot_availability(self) -> tuple[set[int], int | None]:
        blocked, preferred = set(), None
        def inspect(hwnd: int, unused: object) -> None:
            nonlocal preferred
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title.startswith("ENB_"):
                return
            try:
                path = client_path(win32process.GetWindowThreadProcessId(hwnd)[1])
                if path in GAME_CLIENTS:
                    slot = GAME_CLIENTS.index(path)
                    if title == self.title:
                        preferred = slot
                    else:
                        blocked.add(slot)
            except OSError:
                pass
        win32gui.EnumWindows(inspect, None)
        return blocked, preferred

    def valid(self) -> bool:
        try:
            return bool(self.window and win32gui.IsWindow(self.window)
                        and win32gui.IsWindowVisible(self.window)
                        and win32process.GetWindowThreadProcessId(self.window)[1] == self.pid
                        and process_identity(self.pid) == self.born)
        except win32gui.error as error:
            if error.winerror == 1400:
                return False
            raise

    def discover(self, *, apply_layout: bool = True) -> int | None:
        if self.valid():
            expected_title = "EBPopup" if self.popup else self.title
            if apply_layout:
                fixed = None if self.popup else character_position(self.window, self.toon_name)
                if (win32gui.GetWindowText(self.window) != expected_title or
                        (fixed is not None and win32gui.GetWindowRect(self.window)[:2] != fixed)):
                    self.layout()
            return self.window
        # A popup and its subsequent main window have different HWNDs but the
        # same process identity. Never switch to another PID or a reused PID.
        if self.pid is not None and process_identity(self.pid) != self.born:
            self.window = None
            return None
        if self.proxy_pid is not None and self.proxy_born is not None and process_identity(self.proxy_pid) != self.proxy_born:
            self.window = None
            return None
        matches = []

        def inspect(hwnd: int, unused: object) -> None:
            title = win32gui.GetWindowText(hwnd)
            if not win32gui.IsWindowVisible(hwnd) or (
                "Earth & Beyond" not in title and not title.startswith("ENB_") and title != "EBPopup"
            ):
                return
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            if self.pid is not None and pid != self.pid:
                return
            if self.shared_install and self.pid is None:
                if self.proxy_pid is not None:
                    if parent_process_id(pid) != self.proxy_pid:
                        return
                elif title != self.title:
                    return  # Never adopt another character's unclaimed window.
            if client_path(pid) == self.client:
                matches.append((hwnd, pid))

        win32gui.EnumWindows(inspect, None)
        if len({pid for unused, pid in matches}) > 1:
            raise RuntimeError(f"More than one game uses {self.client}; use one installation per instance")
        if not matches:
            self.window = None
            return None
        popups = [item for item in matches if win32gui.GetClassName(item[0]) == "#32770"]
        self.window, self.pid = (popups or matches)[0]
        self.popup = bool(popups)
        self.born = process_identity(self.pid)
        self.launching = False
        if not self.proxy_pid:
            parent = parent_process_id(self.pid)
            if parent is not None:
                try:
                    if client_path(parent).name.lower() == "net7proxy.exe":
                        self.proxy_pid, self.proxy_born = parent, process_identity(parent)
                except OSError:
                    pass
        if apply_layout:
            self.layout()
        return self.window

    def layout(self) -> None:
        win32gui.SetWindowText(self.window, "EBPopup" if self.popup else self.title)
        if self.popup:
            return
        fixed = character_position(self.window, self.toon_name)
        if fixed is not None:
            self.x, self.y = fixed
        elif self.slot is not None and (self.automatic or
                                     (self.shared_install and self.x is None and self.y is None)):
            area = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0), 1))["Work"]
            left, top, right, bottom = win32gui.GetWindowRect(self.window)
            positions = ((area[0], area[1]),
                         (max(area[0], area[2] - (right - left)), area[1]),
                         (area[0], max(area[1], area[3] - (bottom - top))),
                         (max(area[0], area[2] - (right - left)), max(area[1], area[3] - (bottom - top))))
            if self.shared_install:
                self.x, self.y = positions[self.slot]
            else:
                self.x, self.y = (area[0], area[1]) if self.slot == 0 else (
                    max(area[0], area[2] - (right - left)), max(area[1], area[3] - (bottom - top)))
        if self.x is not None or self.y is not None:
            x, y = visible_position(self.window, self.x, self.y)
            win32gui.SetWindowPos(self.window, 0,
                                 x, y, 0, 0,
                                 win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)

    def launched(self, proxy: Popen[bytes]) -> None:
        self.pid = self.born = self.window = None
        self.proxy_pid = proxy.pid
        self.proxy_born = process_identity(proxy.pid)
        self.launching = True
        self.popup = False

    def stop(self) -> None:
        if self.launching:
            # A client can have appeared before the next normal window check.
            self.discover(apply_layout=False)
        for pid, born in ((self.pid, self.born), (self.proxy_pid, self.proxy_born)):
            if pid and born and process_identity(pid) == born:
                try:
                    handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE | 0x1000 | win32con.SYNCHRONIZE, False, pid)
                except win32api.error as error:
                    if error.winerror == 87:  # It exited between identity check and open.
                        continue
                    raise
                try:
                    # Recheck creation time through the opened handle to avoid PID reuse.
                    if str(win32process.GetProcessTimes(handle)["CreationTime"]) == born:
                        import win32event
                        try:
                            win32api.TerminateProcess(handle, 0)
                        except win32api.error:
                            if win32event.WaitForSingleObject(handle, 0) != win32event.WAIT_OBJECT_0:
                                raise
                        if win32event.WaitForSingleObject(handle, 5000) == win32event.WAIT_TIMEOUT:
                            raise RuntimeError(f"Owned process {pid} did not stop")
                finally:
                    handle.Close()
        self.window = None
        self.pid = self.born = self.proxy_pid = self.proxy_born = None
        self.launching = False
        self.popup = False
