"""Navigate an existing character through recorded sector gates to a destination."""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import select
import socket
import struct
import time
from typing import Literal, TextIO, TypedDict, cast

from explore_gates import GateExplorer, PacketRow, SectorGraph
from focus import SharedFocus, process_identity
from protocol.decode_capture import decode_datagram, udp
from protocol.game_packet_monitor import udp_ports
from protocol.ip_reassembly import IPv4Reassembler
from protocol.client_sector import read_current_sector
from protocol.vendor_trading import VendorSession
from protocol.verb_updates import VerbUpdate, decode_verb_update
from util import configure_event_log, log_event
from navigation.stations import StationCatalog
from protocol.world_tracker import sector_name
from macro_control import PauseControl

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class NavigationLeg:
    source: str
    destination: str
    target_id: int
    name: str
    action: Literal[7, 8, 18] = 18


@dataclass(frozen=True)
class ObservedVerbs:
    sector: str | None
    received_at: float
    update: VerbUpdate


def normalized(value: str) -> str:
    return ''.join(character for character in value.casefold() if character.isalnum())


def resolve_sector(query: str, sectors: set[str]) -> str:
    """Prefer exact/partial names, then accept a clearly better typo match."""
    needle = normalized(query)
    if not needle:
        raise ValueError('Destination must contain a sector name')
    exact = [sector for sector in sectors if normalized(sector) == needle]
    matches = exact or [sector for sector in sectors if needle in normalized(sector)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError(f'Sector {query!r} is ambiguous: {", ".join(sorted(matches))}')
    if len(needle) >= 4:
        scores: list[tuple[float, str]] = []
        for sector in sorted(sectors):
            words = re.findall(r'[a-z0-9]+', sector.casefold().replace("'", ''))
            # A misspelled distinctive word also matches a longer sector name.
            candidates = [normalized(sector), *words]
            score = max(SequenceMatcher(None, needle, candidate).ratio() for candidate in candidates)
            scores.append((score, sector))
        scores.sort(key=lambda entry: (-entry[0], entry[1]))
        if scores and scores[0][0] >= .75:
            close = [sector for score, sector in scores if scores[0][0] - score < .08]
            if len(close) == 1:
                return close[0]
            raise ValueError(f'Sector {query!r} is ambiguous: {", ".join(close)}')
    raise ValueError(f'Sector {query!r} is unknown: no recorded match')


class DestinationGraph:
    """Shortest routes over sector-scoped gates and verified planet transfers."""

    def __init__(self, gates: SectorGraph, transfers: Path | None = None,
                 station_catalog: StationCatalog | None = None) -> None:
        self.gates = gates
        self.station_catalog = station_catalog if station_catalog is not None else StationCatalog()
        self.transfers: list[NavigationLeg] = []
        if transfers is not None and transfers.exists():
            for record in json.loads(transfers.read_text(encoding='utf-8')):
                if record.get('action') != 8 or not record.get('confirmed_at'):
                    raise ValueError('Planet transfers require action 8 and a confirmed_at timestamp')
                target = int(record['target_id'])
                if not 0 < target < 0xffffffff:
                    raise ValueError('Invalid planet target ID')
                self.transfers.append(NavigationLeg(str(record['source']), str(record['destination']),
                    target, str(record['name']), 8))

    def sectors(self) -> set[str]:
        names = set(self.gates.sectors)
        names.update(station.sector for station in self.station_catalog.stations.values())
        for leg in self.transfers:
            names.update((leg.source, leg.destination))
        for gates in self.gates.sectors.values():
            names.update(gate.destination for gate in gates.values() if gate.destination)
        return names

    def legs(self, source: str) -> list[NavigationLeg]:
        candidates = [NavigationLeg(source, gate.destination, gate.item_id, gate.name)
            for gate in self.gates.sectors.get(source, {}).values()
            if gate.destination and not gate.unavailable_reason
            and (gate.confirmed_at is not None or gate.inferred_reverse)]
        return candidates + [leg for leg in self.transfers if leg.source == source]

    def route(self, source: str, destination: str, *, dock: bool = False) -> list[NavigationLeg]:
        self.gates.refresh_peers()
        source = resolve_sector(source, self.sectors())
        destination = resolve_sector(destination, self.sectors() | self.station_catalog.labels())
        if destination in self.station_catalog.labels():
            station = self.station_catalog.by_label(destination)
            return self.route(source, station.sector) + [NavigationLeg(
                station.sector, station.name, station.item_id, station.name, 7)]
        docking: list[NavigationLeg] = []
        if dock:
            stations = [station for station in self.station_catalog.stations.values() if station.sector == destination]
            if not stations:
                raise ValueError(f'No recorded station in {destination}')
            station = min(stations, key=lambda row: row.item_id)
            docking.append(NavigationLeg(destination, station.name, station.item_id, station.name, 7))
        queue: deque[tuple[str, list[NavigationLeg]]] = deque([(source, [])])
        seen = {source}
        while queue:
            sector, path = queue.popleft()
            if sector == destination:
                return path + docking
            for leg in self.legs(sector):
                if leg.destination not in seen:
                    seen.add(leg.destination)
                    queue.append((leg.destination, [*path, leg]))
        raise ValueError(f'No recorded route from {source} to {destination}; explore the missing gates first')


class UdpPacket(TypedDict):
    source: str
    destination: str
    payload: bytes


class NavigationCapture:
    """Main-thread passive capture, pinned to one proxy PID and birth identity."""

    def __init__(self, session: VendorSession) -> None:
        self.session = session
        self.sockets: list[socket.socket] = []
        self.addresses: set[str] = set()
        self.ports: set[int] = set()
        self.refreshed = 0.0
        self.fragments = IPv4Reassembler()

    def open(self) -> None:
        self.session.check()
        self.addresses = {str(row[4][0]) for row in
            socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
        try:
            for address in self.addresses:
                if address.startswith('127.'):
                    continue
                connection = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
                self.sockets.append(connection)
                connection.bind((address, 0))
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
                connection.ioctl(socket.SIO_RCVALL, 3)
                connection.setblocking(False)
            if not self.sockets:
                raise RuntimeError('No passive capture interface')
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        for connection in self.sockets:
            connection.close()
        self.sockets.clear()

    def decode(self, packet: bytes, now: float) -> PacketRow | None:
        complete = self.fragments.feed(packet)
        datagram = cast(UdpPacket | None, udp(bytes(12) + b'\x08\x00' + complete)) if complete else None
        if datagram is None:
            return None
        source, source_port = datagram['source'].rsplit(':', 1)
        destination, destination_port = datagram['destination'].rsplit(':', 1)
        outgoing = source in self.addresses and int(source_port) in self.ports
        if not outgoing and not (destination in self.addresses and int(destination_port) in self.ports):
            return None
        return PacketRow(time=now, proxy=self.session.proxy_pid, born=self.session.proxy_born,
            outgoing=outgoing, payload_hex=datagram['payload'].hex())

    def read(self) -> list[PacketRow]:
        self.session.check()
        now = time.time()
        if now - self.refreshed >= .5:
            self.ports, self.refreshed = udp_ports(self.session.proxy_pid), now
        rows: list[PacketRow] = []
        for connection in select.select(self.sockets, [], [], 0)[0]:
            for _ in range(1024):
                try:
                    packet = connection.recv(65535)
                except BlockingIOError:
                    break
                row = self.decode(packet, now)
                if row is not None:
                    rows.append(row)
        return rows


def seed_rows(path: Path, session: VendorSession) -> list[PacketRow]:
    """Ignore other processes and a trailing record still being written."""
    rows: list[PacketRow] = []
    with path.open(encoding='utf-8') as source:
        for line in source:
            if not line.endswith('\n'):
                break
            row = cast(PacketRow, json.loads(line))
            if row['proxy'] == session.proxy_pid and row['born'] == session.proxy_born:
                rows.append(row)
    return rows


def automatic_seed(root: Path, name: str, session: VendorSession, now: float) -> Path | None:
    """Use an active recorder only; an old recording cannot prove current location."""
    candidates = [root / f'explore_{name}.jsonl', root / 'gate_explore_live.jsonl']
    fresh: list[tuple[float, Path]] = []
    for path in candidates:
        try:
            status = json.loads(path.with_suffix('.status.json').read_text(encoding='utf-8'))
            updated = float(status['updated'])
            if (status['proxy'] == session.proxy_pid and status['born'] == session.proxy_born
                    and status['state'] == 'running' and 0 <= now - updated <= 3 and path.exists()):
                fresh.append((updated, path))
        except (FileNotFoundError, KeyError, ValueError):
            continue
    return max(fresh, key=lambda item: item[0])[1] if fresh else None


class DestinationNavigator(GateExplorer):
    """Use the existing guarded gate engine with an owned, synchronous capture."""

    def __init__(self, session: VendorSession, name: str, capture: Path,
                 graph: DestinationGraph, *, control: PauseControl | None = None) -> None:
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.touch(exist_ok=False)
        super().__init__(session, name, capture, graph.gates, control=control)
        self.destination_graph = graph
        self.receiver = NavigationCapture(session)
        self.output: TextIO | None = None
        self.stop = ROOT / f'navigate_{name}.stop'
        self.landing_guard: tuple[str, int] | None = None
        self.landing_ready_after: float | None = None
        self.latest_verbs: ObservedVerbs | None = None
        self.station_arrival: tuple[str, str, float] | None = None
        self.docking_guard: tuple[str, int] | None = None

    def observe(self, row: PacketRow) -> None:
        super().observe(row)
        if (row['proxy'] != self.session.proxy_pid or row['born'] != self.session.proxy_born
                or row['outgoing']):
            return
        try:
            decoded = decode_datagram(bytes.fromhex(row['payload_hex']))
        except ValueError:
            return
        for frame in decoded.get('frames', [decoded]):
            if frame['opcode'] == '0x0097':
                body = bytes.fromhex(frame.get('body_hex', ''))
                sector = sector_name(body)
                fields = body[12:].split(b'\0')
                if (sector and len(fields) >= 3 and fields[2]
                        and struct.unpack_from('<I', body, 8)[0] & 0xffffff == self.session.game_id & 0xffffff
                        and all(32 <= value <= 126 for value in fields[2])):
                    self.station_arrival = sector, fields[2].decode('ascii'), row['time']
            if frame['opcode'] == '0x005c':
                try:
                    update = decode_verb_update(bytes.fromhex(frame.get('body_hex', '')))
                except ValueError as error:
                    self.latest_verbs = None
                    log_event(f'Navigation ignored malformed verb update: {error}')
                    continue
                self.latest_verbs = ObservedVerbs(self.world.sector, row['time'], update)

    def poll(self) -> None:
        for row in self.receiver.read():
            if self.output is not None:
                self.output.write(json.dumps(row) + '\n')
            self.observe(row)
        if self.output is not None:
            self.output.flush()
        catalog = self.destination_graph.station_catalog
        added = catalog.observe(self.world.objects.values(), 'server station map object')
        for station in added:
            log_event(f'Station recorded: sector={station.sector}, name={station.name}, id={station.item_id} ({station.item_id:#x})')
        if added:
            catalog.save()

    def transfer(self, leg: NavigationLeg) -> None:
        self.checkpoint()
        if self.world.sector != leg.source:
            raise RuntimeError(f'Route expected {leg.source}, received {self.world.sector}')
        if leg.action == 7:
            self.dock(leg)
            return
        if leg.action == 18:
            gate = self.graph.sectors[leg.source][leg.target_id]
            self.traverse(gate)
        else:
            # Landing uses the same guarded selection and server-arrival check.
            # A verified planet transfer is deliberately separate from gate inference.
            self.select_target(leg.source, leg.target_id, leg.name)
            self.approach_planet(leg)
            # Planet handoffs do not belong in the gate graph.
            submitted = time.time()
            self.landing_ready_after = submitted - 3
            self.send(0x2c, struct.pack('<4I', self.session.game_id, 8, leg.target_id, 0))
            self.landing_guard = None
            self.landing_ready_after = None
            deadline = self.active_time() + 60
            while self.world.sector == leg.source:
                self.checkpoint()
                if self.active_time() >= deadline:
                    raise TimeoutError(f'Planet arrival unconfirmed after submission at {submitted}; no retry')
                time.sleep(.05)
            self.cargo.close()
            self.jumps += 1
        if self.world.sector != leg.destination:
            raise RuntimeError(f'Route arrived at {self.world.sector}, expected {leg.destination}; stopped')
        log_event(f'Navigation arrival confirmed: sector={leg.destination}, transitions={self.jumps}')

    def dock(self, leg: NavigationLeg) -> None:
        self.select_target(leg.source, leg.target_id, leg.name)
        self.validate_selected_target(leg.source, leg.target_id)
        submitted = time.time()
        log_event(f'Station docking started: sector={leg.source}, station={leg.name}, id={leg.target_id}')
        # Captured completion action; the station is selected and acknowledged
        # before sending. Its identity is scoped to the current sector.
        self.docking_guard = leg.source, leg.target_id
        try:
            self.send(0x2c, struct.pack('<4I', self.session.game_id, 7, leg.target_id, 0))
        finally:
            self.docking_guard = None
        deadline = self.active_time() + 30
        while True:
            self.checkpoint()
            arrival = self.station_arrival
            if arrival is not None and arrival[2] >= submitted:
                if normalized(arrival[0]) != normalized(leg.source) or normalized(arrival[1]) != normalized(leg.name):
                    raise RuntimeError(f'Unexpected docking arrival: {arrival[1]} ({arrival[0]}); no retry')
                self.cargo.close()
                self.jumps += 1
                log_event(f'Station docking confirmed: station={arrival[1]}, sector={arrival[0]}')
                return
            if self.active_time() >= deadline:
                raise TimeoutError(f'Station docking unconfirmed: {leg.name}; no retry')
            time.sleep(.05)

    def command_checkpoint(self) -> None:
        super().command_checkpoint()
        if self.docking_guard is not None:
            self.validate_selected_target(*self.docking_guard)
        if self.landing_guard is not None:
            source, target = self.landing_guard
            if self.world.sector != source or self.cargo.inventory is None:
                raise RuntimeError('Sector changed before planet landing; stopped')
            self.validate_selected_target(source, target)
            if self.landing_ready_after is not None:
                if self.landing_attribute(source, target, self.landing_ready_after) != 0:
                    raise RuntimeError('Fresh server landing availability is no longer enabled; no landing sent')

    def landing_attribute(self, source: str, target: int, after: float) -> int | None:
        observed = self.latest_verbs
        now = time.time()
        if (observed is None or observed.sector != source or observed.update.target_id != target
                or observed.received_at < after or not 0 <= now - observed.received_at <= 3):
            return None
        return observed.update.attribute(2)

    def approach_planet(self, leg: NavigationLeg) -> None:
        self.landing_guard = leg.source, leg.target_id
        self.landing_ready_after = None
        started = time.time()
        log_event(f'Planet approach started: sector={leg.source}, planet={leg.name}, id={leg.target_id:#x}')
        self.send(0x9b, struct.pack('<IH', self.session.game_id & 0xffffff, 1)
                  + struct.pack('<I', leg.target_id))
        deadline = self.active_time() + 240
        next_query = 0.
        while True:
            self.checkpoint()
            self.validate_selected_target(leg.source, leg.target_id)
            attribute = self.landing_attribute(leg.source, leg.target_id, started)
            if attribute == 0:
                log_event(f'Planet landing available: id={leg.target_id:#x}, source=server verb 2 enabled')
                return
            if attribute is not None and attribute != 2:
                raise RuntimeError(f'Server landing verb unavailable: attribute={attribute}; no landing sent')
            now = self.active_time()
            if now >= deadline:
                raise TimeoutError('Planet approach did not receive enabled landing availability; no landing sent')
            if now >= next_query:
                # Read-only availability refresh; the approach and landing are never retried.
                self.send(0x5a, struct.pack('>II', self.session.game_id, leg.target_id)
                          + struct.pack('<I', 1))
                next_query = self.active_time() + 2
            time.sleep(.05)

    def navigate(self, destination: str, seed: Path | None, initial_sector: str | None,
                 *, plan_only: bool = False, dock: bool = False) -> None:
        reason = 'destination reached'
        try:
            self.focus = SharedFocus(f'navigate_{self.name}', self.name)
            self.focus.start_comms(self.stop_requested.set)
            self.output = self.capture_path.open('a', encoding='utf-8')
            self.receiver.open()
            if seed is not None:
                for row in seed_rows(seed, self.session):
                    self.observe(row)
            self.poll()
            if self.pending is not None:
                raise RuntimeError('A gate transition is already pending; wait for arrival before navigating')
            if initial_sector is not None:
                if self.world.sector and normalized(self.world.sector) != normalized(initial_sector):
                    raise RuntimeError(f'Current capture says {self.world.sector}, differs from --from-sector')
                self.world.sector = resolve_sector(initial_sector, self.destination_graph.sectors())
            else:
                # Read the active owned client, even when a seed was supplied:
                # a recording can end before the player's latest transition.
                sector = read_current_sector(self.session.client_pid, self.session.client_born,
                                             self.session.game_id)
                self.world.sector = resolve_sector(sector, self.destination_graph.sectors())
                log_event(f'Navigation current sector: {self.world.sector}; source=live client')
            source: str | None = self.world.sector
            if source is None:
                raise RuntimeError('Current sector unavailable in the active client')
            route = self.destination_graph.route(source, destination, dock=dock)
            log_event(f'Navigation started: sector={source}, destination={destination}, transitions={len(route)}, plan_only={plan_only}')
            log_event('Navigation plan: ' + ' -> '.join([source, *(leg.destination for leg in route)]))
            for leg in ([] if plan_only else route):
                self.transfer(leg)
            if plan_only:
                reason = 'route preview; no submissions'
        except BaseException as error:
            reason = f'{type(error).__name__}: {error}'
            raise
        finally:
            self.receiver.close()
            self.cargo.close()
            self.stream.close()
            if self.output is not None:
                self.output.close()
            self.control.close()
            if self.focus is not None:
                self.focus.close()
            self.graph.save()
            log_event(f'Navigation stopped: transitions={self.jumps}, reason={reason}')


def resolve_session(value: str) -> tuple[str, VendorSession]:
    from arrange_windows import client_path
    from game_instance import parent_process_id
    from gui import find_window_title
    from kill_player import resolve_player
    import win32process

    name = resolve_player(value)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('Invalid character name')
    window = find_window_title(f'ENB_{name}')
    if not window:
        raise RuntimeError(f'No game window titled ENB_{name}; no game started')
    pid = win32process.GetWindowThreadProcessId(window)[1]
    born = process_identity(pid)
    proxy = parent_process_id(pid)
    proxy_born = process_identity(proxy) if proxy is not None else None
    if (not born or proxy is None or not proxy_born or proxy_born >= born
            or client_path(pid).name.casefold() != 'client.exe'
            or client_path(proxy).name.casefold() != 'net7proxy.exe'):
        raise RuntimeError('Exact character client/proxy identities unavailable')
    return name, VendorSession.from_processes(pid, born, proxy, proxy_born)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', help='Character, ENB_<name>, or player config; already logged in')
    parser.add_argument('destination', help='Recorded destination sector, quoted if it contains spaces')
    parser.add_argument('--from-sector', help='Override automatic live-client sector detection with a manually verified sector')
    parser.add_argument('--seed', type=Path, help='Optional current-session packet history; current location comes from the live client')
    parser.add_argument('--graph', type=Path, action='append', help='Additional graph input; repeat to merge into the shared graph')
    parser.add_argument('--transfers', type=Path, default=ROOT / 'navigation/sector_transfers.json',
                        help='Confirmed planet transfers; defaults to navigation/sector_transfers.json')
    parser.add_argument('--plan', action='store_true', help='Print the shortest recorded route; send no game commands')
    parser.add_argument('--dock', action='store_true', help='Dock at the first recorded station in the destination sector')
    args = parser.parse_args(argv)
    try:
        from chatproc import require_admin
        require_admin()
        name, session = resolve_session(args.name)
        configure_event_log(ROOT / f'macro_{name}.log', player=name)
        paths: list[Path] = args.graph or []
        graph = SectorGraph(ROOT / 'navigation/sector_graph.json', tuple(paths))
        graph.refresh_peers()
        destination_graph = DestinationGraph(graph, args.transfers, StationCatalog(ROOT / 'navigation/stations.json'))
        seed = args.seed or automatic_seed(ROOT / 'diagnostics', name, session, time.time())
        capture = ROOT / f'diagnostics/navigate_{name}_{time.time_ns()}.jsonl'
        navigator = DestinationNavigator(session, name, capture, destination_graph)
        navigator.navigate(args.destination, seed, args.from_sector, plan_only=args.plan, dock=args.dock)
        return 0
    except (OSError, ValueError, RuntimeError, InterruptedError) as error:
        print(f'Navigation failed: {error}; no retry', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
