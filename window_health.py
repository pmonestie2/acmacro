"""Check an owned game window without taking focus or sending game input."""
from __future__ import annotations

import win32con
import win32gui
import win32process

from focus import process_identity


class WindowProbeTimeout(RuntimeError):
    """The same owned window did not answer within the short probe interval."""


def check_window_responsive(window: int | None, pid: int, born: str) -> None:
    try:
        if (not window or not win32gui.IsWindow(window) or
                not win32gui.IsWindowVisible(window) or
                win32process.GetWindowThreadProcessId(window)[1] != pid or
                process_identity(pid) != born):
            raise RuntimeError('Owned game window disappeared or changed; no further submissions')
        # WM_NULL probes the UI thread without changing focus or gameplay.
        # This short probe reports a delay. The caller decides its grace period.
        win32gui.SendMessageTimeout(window, win32con.WM_NULL, 0, 0,
                                    win32con.SMTO_ABORTIFHUNG | win32con.SMTO_BLOCK, 250)
    except win32gui.error as error:
        if error.winerror == 1460:
            raise WindowProbeTimeout('Owned game window did not answer the 250 ms probe '
                                     '(Windows error 1460)') from None
        raise RuntimeError(f'Owned game window is unresponsive or unavailable '
                           f'(Windows error {error.winerror}); no further submissions') from None
