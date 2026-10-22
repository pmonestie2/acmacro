import time
import logging
from datetime import datetime
from pathlib import Path
from config import MACRO_LOG_PATH


LOG_DICT: dict[str, float] = {}
_event_logger = logging.getLogger("acmacro.events")
_event_logger.setLevel(logging.INFO)
_event_logger.propagate = False
_event_logger.addHandler(logging.NullHandler())
_player_prefix: str = ""


def configure_event_log(path: str | Path = MACRO_LOG_PATH, *, player: str | None = None) -> None:
    """Enable append-only, thread-safe event logging for a macro process."""
    handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(player_prefix)s%(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    for previous in list(_event_logger.handlers):
        _event_logger.removeHandler(previous)
        previous.close()
    _event_logger.addHandler(handler)
    global _player_prefix
    _player_prefix = f"[{player}] " if player else ""


def log_event(message: object) -> None:
    # Keep incoming chat on one physical line so it cannot forge log entries.
    _event_logger.info(str(message).replace("\r", "\\r").replace("\n", "\\n"),
                       extra={"player_prefix": _player_prefix})
    log(str(message))


def log(s: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {_player_prefix}{s}")
    


def log_every(category: str, s: str, interval: float = 60.0, *, important=False):
    """
    Log `s` under `category` at most once per `interval` seconds.
    """
    if interval <= 0:
        (log_event if important else log)(s)
        return
    now = time.time()
    last = LOG_DICT.get(category, 0)
    if now - last >= interval:
        (log_event if important else log)(s)
        LOG_DICT[category] = now
