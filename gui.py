import ctypes
import time
from keyboard_input import send as send_scan_code

# Set physical-pixel coordinates before PyAutoGUI initializes Windows DPI mode.
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
import win32con
import win32gui
import pyautogui as pag

from typing import Callable

SLEEP_FN:Callable[[float], None] = time.sleep

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
            pag.keyUp(key, _pause=False)


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
    if not hwnd:
        print("Window not found")
        return False

    print("Got window:", hex(hwnd))

    # Try to bring to foreground
    # Show window
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    if win32gui.GetForegroundWindow() != hwnd:
        try:
            win32gui.SetForegroundWindow(hwnd)
        except win32gui.error:
            return False
    SLEEP_FN(.5)
    return win32gui.GetForegroundWindow() == hwnd


if __name__ == "__main__":
    bring_to_front_exact("Earth")
    time.sleep(1)
    print("Starting key press test...")
    # First try with a simple key press
    print("\nTesting single key press (A key down/up):")
    key_press("RIGHT", 2)

