import ctypes
import time
from keyboard_input import send as send_scan_code

# Set physical-pixel coordinates before PyAutoGUI initializes Windows DPI mode.
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
import win32con
import win32gui
import win32api
import win32process
import pyautogui as pag

from typing import Callable

SLEEP_FN:Callable[[float], None] = time.sleep
FOCUS_SETTLE_TIME = 0.2

_user32 = ctypes.windll.user32
_user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_user32.GetWindowDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p


def client_geometry(hwnd):
    """Return game coordinates, physical screen size, and physical origin."""
    previous = _user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        physical = (right - left, bottom - top)
        origin = win32gui.ClientToScreen(hwnd, (0, 0))
        _user32.SetThreadDpiAwarenessContext(_user32.GetWindowDpiAwarenessContext(hwnd))
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        logical = (right - left, bottom - top)
    finally:
        _user32.SetThreadDpiAwarenessContext(previous)
    if min(*logical, *physical) <= 0:
        raise RuntimeError("Game window has no visible client area.")
    return logical, physical, origin

ARROW_SCAN_CODES = {"left": 0x4B, "right": 0x4D, "up": 0x48, "down": 0x50}


def release_input(function, *args, **kwargs):
    """The mouse-corner failsafe must not prevent releasing held inputs."""
    previous = pag.FAILSAFE
    pag.FAILSAFE = False
    try:
        return function(*args, **kwargs)
    finally:
        pag.FAILSAFE = previous


def normalize_key(key: str) -> str:
    """Normalize named keys and ACTool braces; preserve literal characters."""
    if len(key) > 1:
        key = key.strip()
        if key.startswith("{") and key.endswith("}"):
            key = key[1:-1]
        if len(key) > 1:
            key = key.lower()
    return {"return": "enter", "escape": "esc", " ": "space"}.get(key, key)


def key_press(key: str, delay: float = 0.03):
    """Tap a key with PyAutoGUI, using scan codes for game arrows."""
    key = normalize_key(key)
    if key in ARROW_SCAN_CODES:
        scan = ARROW_SCAN_CODES[key]
        send_scan_code(scan)
        try:
            SLEEP_FN(delay)
        finally:
            send_scan_code(scan, release=True)
    else:
        pag.keyDown(key, _pause=False)
        try:
            SLEEP_FN(delay)
        finally:
            release_input(pag.keyUp, key, _pause=False)


def find_window_title(title: str) -> int | None:
    """Find one visible window by its complete title, ignoring case."""
    found: list[int] = []

    def inspect(hwnd: int, unused: object) -> None:
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).casefold() == title.casefold():
            found.append(hwnd)

    win32gui.EnumWindows(inspect, None)
    if len(found) > 1:
        raise RuntimeError(f'Multiple visible windows have the title {title}')
    return found[0] if found else None


def find_window_exact(title_substr: str) -> int:
    """Try to find a window whose title contains `title_substr`."""

    hwnd = None

    def enum_handler(h, _):
        nonlocal hwnd
        text = win32gui.GetWindowText(h)
        if hwnd is None and win32gui.IsWindowVisible(h) and title_substr.lower() in text.lower():
            hwnd = h

    win32gui.EnumWindows(enum_handler, None)
    return hwnd

def bring_to_front_exact(title_substr: str) -> bool:
    """Try to bring a window whose title contains `title_substr` to foreground."""

    hwnd = find_window_exact(title_substr)
    return focus_window(hwnd)


def focus_window(hwnd) -> bool:
    """Focus a specific owned window without routine console output."""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    # Try to bring to foreground
    # Show window
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    for attempt in range(4):
        if not win32gui.IsWindow(hwnd):
            return False
        try:
            win32gui.SetForegroundWindow(hwnd)
        except win32gui.error:
            pass
        if win32gui.GetForegroundWindow() != hwnd:
            # Foreground restrictions can reject a borrower from another process.
            # Temporarily share its input queue with the foreground thread.
            current = win32api.GetCurrentThreadId()
            foreground = win32gui.GetForegroundWindow()
            other = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
            attached = False
            try:
                win32gui.PeekMessage(None, 0, 0, win32con.PM_NOREMOVE)
                if other and other != current:
                    attached = bool(_user32.AttachThreadInput(current, other, True))
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            except win32gui.error:
                pass
            finally:
                if attached:
                    _user32.AttachThreadInput(current, other, False)
        # Let the game settle after activation before any input or screenshot.
        SLEEP_FN(0.1)
        if win32gui.GetForegroundWindow() == hwnd:
            SLEEP_FN(FOCUS_SETTLE_TIME)
            if win32gui.GetForegroundWindow() == hwnd:
                return True
    return False


if __name__ == "__main__":
    bring_to_front_exact("Earth")
    time.sleep(1)
    print("Starting key press test...")
    # First try with a simple key press
    print("\nTesting single key press (A key down/up):")
    key_press("RIGHT", 2)

