"""Best-effort UI evidence for macro failures, without sending game input."""
from datetime import datetime
from pathlib import Path

from gui import client_geometry, find_window_exact
import pyautogui as pag

from config import WINDOW_TITLE
from util import log_event

DIAGNOSTICS_DIR = Path(__file__).resolve().parent / 'diagnostics'


def capture_failure(error):
    """Save native screen pixels; a capture failure must not hide the error."""
    try:
        region = None
        try:
            window = find_window_exact(WINDOW_TITLE)
            if window:
                _, size, origin = client_geometry(window)
                region = (*origin, *size)
        except Exception:
            pass  # A vanished game window still warrants a desktop capture.
        snapshot = pag.screenshot(region=region)
        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        scope = 'game' if region else 'desktop'
        path = DIAGNOSTICS_DIR / f'failure_{datetime.now():%Y%m%d_%H%M%S_%f}_{scope}.png'
        snapshot.save(path)
        log_event(f'Failure screenshot: {path}; {type(error).__name__}: {error}')
        return path
    except Exception as capture_error:
        log_event(f'Could not capture failure screenshot: {capture_error}; original error: {error}')
        return None
