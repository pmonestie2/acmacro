"""Packet-only station worker; UI and route ownership stay on the main thread."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import threading
from typing import Self

from focus import process_identity
from job_policy import JobPolicy
from job_sender import submit, JobPacketUnavailable
from job_types import BackgroundJobController, JobMonitor, JobSender, OwnedJobProxy
from packet_jobs import PacketJobPicker
from util import log_event
from watchdog import StopMacro


@dataclass(frozen=True)
class _OwnedProxy:
    pid: int | None
    born: str | None
    proxy_pid: int | None
    proxy_born: str | None
    _worker: BackgroundJobPickup

    def valid(self) -> bool:
        return self._worker.valid()


class BackgroundPacketController:
    """Adapt the background worker to the interface used by PacketJobPicker.

    Counts come from packets, waits respect worker cancellation, and control
    checks leave the main thread's focus and input handling alone.
    """

    def __init__(self, worker: BackgroundJobPickup) -> None:
        self._worker = worker
        self.instance: OwnedJobProxy = worker.instance
        self.job_monitor: JobMonitor = worker.monitor

    def start_packet_capture(self) -> None:
        """Reuse the capture started by the main thread before this worker."""
        pass

    def count_mission(self) -> int:
        return self._worker.packet_count()

    def polling_time(self) -> float:
        return self._worker.controller.polling_time()

    def _yield(self, seconds: float) -> None:
        self._worker.wait(seconds)

    def _check_remote_logoff(self) -> None:
        self._worker.checkpoint()

    def _check_pause(self) -> None:
        self._worker.checkpoint()


class BackgroundJobPickup:
    def __init__(self, controller: BackgroundJobController, policy: JobPolicy, target: int, count: int, *,
                 sender: JobSender = submit) -> None:
        self.controller = controller
        self.policy = policy
        self.target = target
        self.count = count
        self.sender = sender
        self.cancelled = threading.Event()
        self.sales_done = threading.Event()
        self.commands = threading.RLock()
        self.done = threading.Event()
        self.error: Exception | None = None
        self.result: int | None = None
        self.thread: threading.Thread | None = None
        instance = controller.instance
        monitor = controller.job_monitor
        if instance is None or monitor is None:
            raise JobPacketUnavailable('Background pickup requires an owned game and packet capture')
        self.owner = (instance.pid, instance.born, instance.proxy_pid, instance.proxy_born)
        # Freeze submission ownership so relogin cannot redirect a running worker.
        self.instance: OwnedJobProxy = _OwnedProxy(pid=instance.pid, born=instance.born,
            proxy_pid=instance.proxy_pid, proxy_born=instance.proxy_born,
            _worker=self)
        self.monitor: JobMonitor = monitor

    def valid(self) -> bool:
        instance = self.controller.instance
        return (instance is not None
                and self.owner == (instance.pid, instance.born, instance.proxy_pid, instance.proxy_born)
                and instance.valid() and process_identity(self.owner[2]) == self.owner[3])

    def checkpoint(self) -> None:
        while True:
            if self.cancelled.is_set():
                raise JobPacketUnavailable('Background job pickup cancelled')
            if not self.valid():
                raise JobPacketUnavailable('Background job pickup lost its owned game/proxy')
            if self.controller._remote_logoff.is_set() or self.controller._stopping:
                raise JobPacketUnavailable('Background job pickup stopped for logoff')
            control = self.controller._pause_control
            if not self.controller._paused and not (control and control.requested()):
                return
            self.cancelled.wait(.05)

    def wait(self, seconds: float) -> None:
        deadline = self.controller.polling_time() + seconds
        while True:
            self.checkpoint()
            remaining = deadline - self.controller.polling_time()
            if remaining <= 0:
                return
            self.cancelled.wait(min(.05, remaining))

    def before_accept(self) -> None:
        while not self.sales_done.is_set():
            self.checkpoint()
            self.cancelled.wait(.05)
        self.checkpoint()

    def send(self, instance: OwnedJobProxy, action: int, target: int = 0) -> object:
        with self.commands:
            self.checkpoint()
            return self.sender(instance, action, target)

    @contextmanager
    def vendor(self) -> Iterator[None]:
        # Starbase/vendor requests share server state with terminal commands.
        # Wait for an in-flight submission, then exclude new ones while selling.
        while not self.commands.acquire(timeout=.05):
            self.controller._yield(.05)
        try:
            yield
        finally:
            self.commands.release()

    def allow_accepts(self) -> None:
        self.sales_done.set()

    def accepted(self, count: int) -> None:
        self.count = count

    def packet_count(self) -> int:
        self.checkpoint()
        snapshot = self.monitor.mission_snapshot()
        if snapshot is not None:
            self.count = max(self.count, snapshot['count'])
        return self.count

    def start(self) -> Self:
        if self.thread is not None:
            raise RuntimeError('Background job pickup already started')
        controller = BackgroundPacketController(self)
        picker = PacketJobPicker(controller, self.policy, self.target, sender=self.send,
                                 on_accept=self.accepted, before_accept=self.before_accept)
        self.thread = threading.Thread(target=self._run, args=(picker,), name='packet-job-pickup', daemon=True)
        log_event(f'Background packet job pickup starting at Earth station: count={self.count}, target={self.target}')
        self.thread.start()
        return self

    def _run(self, picker: PacketJobPicker) -> None:
        try:
            self.result = picker.pick()
        except Exception as error:
            self.error = error
        finally:
            reason = str(self.error) if self.error is not None else 'finished'
            log_event(f'Background packet job pickup stopped: count={self.count}, target={self.target}, reason={reason}')
            self.done.set()

    def finish(self) -> int:
        if self.thread is None:
            raise RuntimeError('Background job pickup has not started')
        while not self.done.is_set():
            self.controller._yield(.1)
        self.thread.join()
        if self.error is not None:
            if isinstance(self.error, JobPacketUnavailable):
                raise StopMacro(f'Background packet job pickup unavailable: {self.error}; UI pickup disabled') from self.error
            raise self.error
        if self.result is None:
            raise RuntimeError('Background job pickup finished without a result')
        log_event(f'Background packet job pickup joined before undocking: count={self.result}, target={self.target}')
        return self.result

    def close(self) -> None:
        self.cancelled.set()
        if self.thread is not None:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                raise StopMacro('Background job sender did not stop; undocking/relogin blocked')
