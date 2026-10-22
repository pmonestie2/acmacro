"""Passive game packet monitoring for positions, missions and job-terminal state."""
from collections.abc import Callable
import ctypes
import json
import math
from pathlib import Path
import select
import socket
import struct
import threading
import time

from focus import process_identity
from protocol.decode_capture import decode_datagram, udp
from protocol.ip_reassembly import IPv4Reassembler
from protocol.world_tracker import sector_name
from protocol.mission_state import MissionState
from protocol.job_state import JobState
from job_types import JobMonitor, JobSnapshot, MissionSnapshot, OwnedJobProxy, ProxyOwner

SECTORS = {'pluto': 'Pluto and Charon', 'akeron': "Akeron's Gate",
           'saturn': 'Saturn', 'jupiter': 'Jupiter', 'aba': 'Asteroid Belt Alpha',
           'earth': 'Earth', 'glenn': 'Glenn', 'carp': 'Carpenter',
           'uranus': 'Uranus', 'newed': 'New Edinburgh', 'swoop': 'Swooping Eagle', 'xipe': 'Xipe Totec'}


def udp_ports(pid):
    function = ctypes.WinDLL('iphlpapi', use_last_error=True).GetExtendedUdpTable
    function.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.c_int,
                         ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    function.restype = ctypes.c_ulong
    size = ctypes.c_ulong()
    result = function(None, ctypes.byref(size), False, socket.AF_INET, 1, 0)
    if result not in (0, 122):
        raise OSError(result, 'Cannot read UDP ownership')
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        result = function(buffer, ctypes.byref(size), False, socket.AF_INET, 1, 0)
        if result == 122:
            continue
        if result:
            raise OSError(result, 'Cannot read UDP ownership')
        count = struct.unpack_from('<I', buffer)[0]
        return {socket.ntohs(port & 0xffff)
                for address, port, owner in
                (struct.unpack_from('<III', buffer, offset) for offset in range(4, 4 + count * 12, 12))
                if owner == pid}
    raise OSError('UDP ownership changed repeatedly')


class Positions:
    def __init__(self):
        self.token, self.sector, self.xyz, self.seen = None, None, None, 0
        self.speed, self.moved = 0, 0

    def observe(self, payload, outgoing, now):
        if outgoing:
            if len(payload) < 12:
                return
            token = struct.unpack_from('<I', payload, 4)[0]
            if token != self.token:
                self.xyz, self.sector, self.seen = None, None, 0
                self.speed, self.moved = 0, 0
            self.token = token
            if struct.unpack_from('<H', payload, 2)[0] == 0x1004 and len(payload) in (28, 40):
                self.set_position(struct.unpack_from('<fff', payload, 12), now)
            return
        for frame in decode_datagram(payload).get('frames', []):
            body = bytes.fromhex(frame.get('body_hex', ''))
            if frame['opcode'] == '0x0097':
                sector = sector_name(body)
                if sector and sector != self.sector:
                    self.xyz, self.seen = None, 0
                    self.speed, self.moved = 0, 0
                    self.sector = sector
            elif frame['opcode'] == '0x003e' and ((len(body) == 66 and body[:2] == b'\x2f\x01') or
                                                  (len(body) == 54 and body[:2] == b'\x28\x01')):
                if self.token == struct.unpack_from('<I', body, 2)[0]:
                    self.set_position(struct.unpack_from('<fff', body, 10), now)

    def set_position(self, xyz, now):
        if all(math.isfinite(value) for value in xyz):
            if self.xyz is not None and tuple(xyz) != self.xyz and now > self.seen:
                self.speed = math.dist(self.xyz, xyz) / (now - self.seen)
                self.moved = now
            self.xyz, self.seen = tuple(xyz), now


class GamePacketMonitor(JobMonitor):
    """Track one owned proxy's player position, missions and job replies."""
    def __init__(self, instance: OwnedJobProxy, report: Callable[[str], None]) -> None:
        self.instance, self.report = instance, report
        self.lock, self.done = threading.Lock(), threading.Event()
        self.owner: ProxyOwner | None = None
        self.positions = Positions()
        self.missions = MissionState()
        self.mission_remote: str | None = None
        self.traffic_seen: float = 0
        self.jobs = JobState()
        self.points: dict[str, dict[str, dict[str, object]]] = {}
        self.catalog_stamp: int | None = None
        self.catalog = Path(__file__).resolve().parents[1] / 'navigation/navpoints.json'
        self.thread = threading.Thread(target=self.run, name='game-packet-monitor', daemon=True)
        self.thread.start()

    def job_snapshot(self) -> JobSnapshot | None:
        with self.lock:
            if self.owner is None or self.owner != (self.instance.proxy_pid, self.instance.proxy_born) or not self.thread.is_alive():
                return None
            return JobSnapshot(**self.jobs.snapshot(), owner=self.owner, remote=self.mission_remote)

    def mission_snapshot(self, now: float | None = None) -> MissionSnapshot | None:
        now = time.time() if now is None else now
        with self.lock:
            if (self.owner != (self.instance.proxy_pid, self.instance.proxy_born)
                    or not self.thread.is_alive() or now - self.traffic_seen > 5):
                return None
            snapshot: MissionSnapshot | None = self.missions.snapshot()
            # Login/reset packets can be immediately followed by populated slots.
            if snapshot is None or now - snapshot['seen'] < .5:
                return None
            return snapshot

    def mission_unavailable_reason(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if self.owner != (self.instance.proxy_pid, self.instance.proxy_born):
                reason = 'owned proxy changed; capture has not synchronized'
            elif not self.thread.is_alive():
                reason = 'capture thread is not running'
            elif not self.traffic_seen:
                reason = 'no incoming traffic captured for owned proxy'
            elif now - self.traffic_seen > 5:
                reason = f'incoming capture is stale ({now - self.traffic_seen:.1f}s)'
            elif not self.missions.ready:
                reason = self.missions.reason
                if self.missions.snapshots_seen == 0:
                    reason += '; no full mission list seen since capture started (existing login may predate capture)'
            else:
                reason = 'mission update is still settling'
            return (f'{reason}; proxy={self.owner}; remote={self.mission_remote}; '
                    f'mission_updates={self.missions.updates_seen}; full_snapshots={self.missions.snapshots_seen}')

    def position(self, sector, now=None):
        now = time.time() if now is None else now
        with self.lock:
            value = self.positions
            name = SECTORS.get(sector)
            if (self.owner != (self.instance.proxy_pid, self.instance.proxy_born) or not name or
                    value.xyz is None or now - value.seen > 5 or
                    (value.sector is not None and value.sector != name)):
                return None
            return dict(xyz=value.xyz, seen=value.seen, speed=value.speed, moved=value.moved)

    def arrived(self, sector):
        with self.lock:
            return (SECTORS.get(sector) is not None and
                    self.owner == (self.instance.proxy_pid, self.instance.proxy_born) and
                    self.positions.sector == SECTORS.get(sector) and self.positions.xyz is not None and
                    time.time() - self.positions.seen <= 5)

    def distance(self, sector, identity, now=None):
        now = time.time() if now is None else now
        try:
            stamp = self.catalog.stat().st_mtime_ns
            if stamp != self.catalog_stamp:
                self.points = json.loads(self.catalog.read_text())['sectors']
                self.catalog_stamp = stamp
            with self.lock:
                if self.owner != (self.instance.proxy_pid, self.instance.proxy_born):
                    return None
                position = self.positions
                name = SECTORS.get(sector)
                if (not name or not position.xyz or now - position.seen > 5 or
                        (position.sector is not None and position.sector != name)):
                    return None
                target = next((obj for obj in self.points.get(name, {}).values()
                               if int(obj['id'], 16) == identity), None)
                return math.dist(position.xyz, target['raw_xyz']) if target else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def close(self):
        self.done.set()
        self.thread.join(timeout=2)

    def run(self):
        sockets = []
        try:
            addresses = {row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                         if not row[4][0].startswith('127.')}
            for address in addresses:
                sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
                sockets.append(sock)
                sock.bind((address, 0))
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
                sock.ioctl(socket.SIO_RCVALL, 3)
                sock.setblocking(False)
            if not sockets:
                raise OSError('No IPv4 capture interface')
            refreshed, ports = 0, set()
            reassembler = IPv4Reassembler()
            while not self.done.is_set():
                now = time.time()
                owner = self.instance.proxy_pid, self.instance.proxy_born
                if now - refreshed >= 1 or owner != self.owner:
                    with self.lock:
                        if owner != self.owner:
                            self.owner, self.positions = owner, Positions()
                            self.missions, self.mission_remote, self.traffic_seen = MissionState(), None, 0
                            self.jobs = JobState()
                            reassembler = IPv4Reassembler()
                    ports = (udp_ports(owner[0]) if isinstance(owner[0], int) and
                             process_identity(owner[0]) == owner[1] else set())
                    refreshed = now
                for sock in select.select(sockets, [], [], .1)[0]:
                    packet = reassembler.feed(sock.recv(65535))
                    # Timestamp receipt, not the start of the select wait. A reply
                    # can otherwise appear to predate the command that caused it.
                    received = time.time()
                    item = udp(bytes(12) + b'\x08\x00' + packet) if packet else None
                    if not item:
                        continue
                    source, sport = item['source'].rsplit(':', 1)
                    destination, dport = item['destination'].rsplit(':', 1)
                    outgoing = source in addresses and int(sport) in ports
                    if outgoing or (destination in addresses and int(dport) in ports):
                        try:
                            with self.lock:
                                if not outgoing:
                                    if self.mission_remote != item['source']:
                                        self.missions = MissionState()
                                        if self.mission_remote is not None:
                                            self.jobs = JobState()
                                        self.mission_remote = item['source']
                                    self.traffic_seen = received
                                self.positions.observe(item['payload'], outgoing, received)
                                self.missions.observe(item['payload'], outgoing, received)
                                self.jobs.observe(item['payload'], outgoing, received)
                        except (ValueError, struct.error):
                            pass
        except Exception as error:
            self.report(f'Game packet capture unavailable: {error}; using normal gate polling')
        finally:
            with self.lock:
                self.positions = Positions()
                self.missions = MissionState()
                self.jobs = JobState()
            for sock in sockets:
                sock.close()
