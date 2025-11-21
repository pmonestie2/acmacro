import random
import threading
from typing import Callable, Optional
import queue
import time
import os
import sys

from util import log

# Globals that coordinate between threads

#type that queue will contain

class RestartException(Exception):
    pass

_interrupt_queue: queue.Queue[Callable[[], None]] = queue.Queue()
_lock = threading.Lock()

def request_interrupt(action: Callable[[], None]) -> None:
    """
    Called from the checker thread when it decides it needs to 'pause the world'
    and run `action` on the main thread.
    """
    global _interrupt_queue
    _interrupt_queue.put(action)

def run_interrupts() -> None:
    global _interrupt_queue
    if _interrupt_queue.qsize()>0:
        with _lock:
            while _interrupt_queue.qsize() > 0:
                # get a lock so we don't run_every thread while we're processing
                action = _interrupt_queue.get()
                action()

def run_every(seconds: float, action: Callable[[], None]) -> threading.Event:
    _stop = threading.Event()
    def _run_every(seconds: float, action: Callable[[], None]) -> None:
        log(f"run_every thread started: {threading.current_thread().name} method {action}")
        while not _stop.is_set():
            _stop.wait(seconds)
            with _lock:
                action()
    threading.Thread(target=lambda: _run_every(seconds, action), daemon=True).start()
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
