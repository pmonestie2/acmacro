import random
import threading
from typing import Callable, Optional
import queue
import time
import os
import sys

from util import log, log_event

# Globals that coordinate between threads

#type that queue will contain

class RestartException(Exception):
    pass


class StopMacro(Exception):
    """A final stop request; must never be treated as a relogin/restart."""


class GameWindowLost(RuntimeError):
    """The game disappeared during an operation; the runner must relogin."""


class GameUnresponsive(RuntimeError):
    """The game did not acknowledge a readiness check; kill and relogin."""


class RepeatingTask(threading.Event):
    """A stop event whose worker can be drained before COM/process shutdown."""

    def join(self, timeout=None):
        if self.thread is not threading.current_thread():
            self.thread.join(timeout)

_interrupt_queue: queue.Queue[Callable[[], None]] = queue.Queue()
_lock = threading.RLock()
_processing = False

def request_interrupt(action: Callable[[], None]) -> None:
    """
    Called from the checker thread when it decides it needs to 'pause the world'
    and run `action` on the main thread.
    """
    global _interrupt_queue
    _interrupt_queue.put(action)

def run_interrupts() -> None:
    global _processing
    with _lock:
        if _processing:
            return
        _processing = True
    try:
        while True:
            try:
                action = _interrupt_queue.get_nowait()
            except queue.Empty:
                break
            action()
    finally:
        with _lock:
            _processing = False

def run_every(seconds: float, action: Callable[[], None]) -> RepeatingTask:
    _stop = RepeatingTask()
    def _run_every(seconds: float, action: Callable[[], None]) -> None:
        log(f"run_every thread started: {threading.current_thread().name} method {action}")
        while not _stop.wait(seconds):
            if _stop.is_set():
                break
            try:
                action()
            except Exception as exc:
                log_event(f"Scheduled check failed: {exc}")
    _stop.thread = threading.Thread(target=lambda: _run_every(seconds, action), daemon=True)
    _stop.thread.start()
    return _stop

def restart_script():
    print("restart")
    python = sys.executable
    os.execv(python, [python] + sys.argv)



if __name__ == "__main__":
    print("watchdog")
    def test():
        rnd = random.randint(1, 10)
        if rnd < 5:
            print("enqueued")
            request_interrupt(lambda: print("interrupted"))
        if rnd == 6:
            print("enq. restart")
            request_interrupt(lambda: restart_script())    
    run_every(2, test)
    while True:
        run_interrupts()
        time.sleep(5)
