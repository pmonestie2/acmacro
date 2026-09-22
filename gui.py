import time
from keyboard_input import send as send_scan_code
import win32api
import win32con
import win32gui
import pyautogui as pag

from typing import Callable

SLEEP_FN:Callable[[float], None] = time.sleep

# Map ACTool → Virtual-Key codes
VK = {

    # special keys
    "SPACE":  win32con.VK_SPACE,
    "ENTER":  win32con.VK_RETURN,
    "ESC":    win32con.VK_ESCAPE,

    "LEFT":   win32con.VK_LEFT,
    "RIGHT":  win32con.VK_RIGHT,
    "UP":     win32con.VK_UP,
    "DOWN":   win32con.VK_DOWN,
    
}

# Extended keys (must set KEYEVENTF_EXTENDEDKEY)
EXT_KEYS = {
    win32con.VK_LEFT,
    win32con.VK_RIGHT,
    win32con.VK_UP,
    win32con.VK_DOWN,
}


def normalize_key(k: str) -> int:
    """
    Convert ACTool-style input:
      'a', 'A', '{LEFT}', '{SPACE}', etc.
    into a Virtual-Key code.
    """
    k = k.strip().upper()
    if k.startswith("{") and k.endswith("}"):
        k = k[1:-1]  # strip {LEFT} -> LEFT

    if k not in VK:
        return -1

    return VK[k]


def _send(vk: int, is_down: bool):
    """Send game arrows as scan codes; preserve other special-key input."""
    if vk in EXT_KEYS:
        scan_code = {win32con.VK_LEFT: 0x4B, win32con.VK_RIGHT: 0x4D,
                     win32con.VK_UP: 0x48, win32con.VK_DOWN: 0x50}[vk]
        send_scan_code(scan_code, release=not is_down)
        return
    flags = 0
    if vk in EXT_KEYS:
        flags |= win32con.KEYEVENTF_EXTENDEDKEY
    if not is_down:
        flags |= win32con.KEYEVENTF_KEYUP

    win32api.keybd_event(vk, 0, flags, 0)


def key_press(key: str, delay: float = 0.03):
    """Tap a key once."""
    vk = normalize_key(key)
    if vk>=0:
        _send(vk, True)
        try:
            SLEEP_FN(delay)
        finally:
            _send(vk, False)
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
        if title_substr.lower() in text.lower():
            hwnd = h
            return False

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
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
    win32gui.SetForegroundWindow(hwnd)
    SLEEP_FN(.5)
    return True


if __name__ == "__main__":
    bring_to_front_exact("Earth")
    time.sleep(1)
    print("Starting key press test...")
    # First try with a simple key press
    print("\nTesting single key press (A key down/up):")
    key_press("RIGHT", 2)
    