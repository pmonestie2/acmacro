import time
from datetime import datetime


LOG_DICT = {}


def log(s: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {s}")
    


def log_every(category: str, s: str, interval: float = 60.0):
    """
    Log `s` under `category` at most once per `interval` seconds.
    """
    if interval <= 0:
        log(s)
        return
    now = time.time()
    last = LOG_DICT.get(category, 0)
    if now - last >= interval:
        log(s)
        LOG_DICT[category] = now
