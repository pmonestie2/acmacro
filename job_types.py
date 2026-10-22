"""Shared packet-job data shapes and the interfaces used by both pickers."""
from collections.abc import Callable
import threading
from typing import NotRequired, Protocol, TypedDict


ProxyOwner = tuple[int | None, str | None]
JobContext = tuple[ProxyOwner, str | None, int]
CommandStamp = tuple[float, float]  # Wall-clock issue time, active timeout clock.


class JobCandidate(TypedDict):
    type: int
    title: str
    level: int


class JobListing(JobCandidate):
    id: int
    unknown: NotRequired[int]
    sponsor: NotRequired[str]
    reward: NotRequired[str]


class JobDescription(TypedDict):
    available: bool
    title: str
    description: str
    id: NotRequired[int]


class ObservedJobDescription(JobDescription):
    seen: float


class JobDescriptionError(TypedDict):
    seen: float
    reason: str
    availability: int | None
    body_prefix: str


class JobRequest(TypedDict):
    player: int
    target: int
    action: int


class JobStateSnapshot(TypedDict):
    generation: int
    opened_at: float
    list_seen: float
    closed: bool
    jobs: dict[int, JobListing]
    descriptions: dict[int, ObservedJobDescription]
    description_errors: dict[int, JobDescriptionError]
    accepted: dict[int, float]
    removed: set[int]
    limit_at: float
    rejected_at: float
    rejection: str
    error: str


class JobSnapshot(JobStateSnapshot):
    owner: ProxyOwner
    remote: str | None


class MissionCountSnapshot(TypedDict):
    count: int


class MissionSnapshot(MissionCountSnapshot):
    missions: list[dict[str, object]]
    seen: float
    source: str
    capacity: int


class OwnedJobProxy(Protocol):
    @property
    def pid(self) -> int | None: ...
    @property
    def born(self) -> str | None: ...
    @property
    def proxy_pid(self) -> int | None: ...
    @property
    def proxy_born(self) -> str | None: ...
    def valid(self) -> bool: ...


JobSender = Callable[[OwnedJobProxy, int, int], object]


class JobMonitor(Protocol):
    def job_snapshot(self) -> JobSnapshot | None: ...
    def mission_snapshot(self) -> MissionCountSnapshot | None: ...


class PacketJobController(Protocol):
    @property
    def instance(self) -> OwnedJobProxy | None: ...
    @property
    def job_monitor(self) -> JobMonitor | None: ...

    def start_packet_capture(self) -> None: ...
    def count_mission(self) -> int: ...
    def polling_time(self) -> float: ...
    def _yield(self, seconds: float) -> None: ...
    def _check_remote_logoff(self) -> None: ...
    def _check_pause(self) -> None: ...


class PauseRequest(Protocol):
    def requested(self) -> bool: ...


class BackgroundJobController(PacketJobController, Protocol):
    _remote_logoff: threading.Event
    _stopping: bool
    _paused: bool
    _pause_control: PauseRequest | None
