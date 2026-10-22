"""Best-effort UI evidence for macro failures, without sending game input."""
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

from gui import client_geometry, find_window_exact
import pyautogui as pag

from config import WINDOW_TITLE
from util import log_event
from watchdog import StopMacro

DIAGNOSTICS_DIR = Path(__file__).resolve().parent / 'diagnostics'


@contextmanager
def capture_ownership(controller):
    focus = getattr(controller, "focus", None)
    if not focus:
        yield
        return
    # A vanished game still permits a desktop diagnostic, but no other macro
    # may change foreground while this process captures it.
    focus.acquire()
    focus.depth += 1
    try:
        yield
    finally:
        focus.depth -= 1
        focus.release()


def capture_failure(error, controller=None):
    """Save native screen pixels; a capture failure must not hide the error."""
    try:
        with capture_ownership(controller):
            region = None
            try:
                window = controller.window if controller else find_window_exact(WINDOW_TITLE)
                if controller and controller.instance and not controller.instance.valid():
                    window = None
                if window:
                    if controller and controller.focus:
                        from gui import focus_window
                        if not focus_window(window):
                            raise RuntimeError("Cannot focus game for diagnostic")
                    _, size, origin = client_geometry(window)
                    region = (*origin, *size)
            except Exception:
                pass  # A vanished game window still warrants a desktop capture.
            snapshot = pag.screenshot(region=region)
        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        scope = 'game' if region else 'desktop'
        toon = f'{controller.player.toon_name}_' if controller else ''
        path = DIAGNOSTICS_DIR / f'failure_{toon}{datetime.now():%Y%m%d_%H%M%S_%f}_{scope}.png'
        snapshot.save(path)
        log_event(f'Failure screenshot: {path}; {type(error).__name__}: {error}')
        return path
    except (StopMacro, pag.FailSafeException):
        raise
    except Exception as capture_error:
        log_event(f'Could not capture failure screenshot: {capture_error}; original error: {error}')
        return None
