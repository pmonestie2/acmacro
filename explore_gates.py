"""Explore sector gates with shared progress and an undirected planning graph."""
from __future__ import annotations
import argparse
from collections import deque
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import struct
import time
from threading import Event
from typing import TypedDict, cast

from focus import SharedFocus, process_identity
from macro_control import PauseControl, atomic_json, request_lock
from protocol import proxy_commands
from protocol.client_cargo import ClientCargo
from protocol.vendor_trading import VendorSession
from protocol.world_tracker import WorldTracker
from protocol.decode_capture import decode_datagram
from protocol.verb_updates import decode_verb_update
from util import configure_event_log, log_event

ROOT = Path(__file__).resolve().parent

@dataclass
class Gate:
    item_id: int
    name: str
    destination: str | None = None
    confirmed_at: float | None = None
    inferred_reverse: bool = False
    unavailable_reason: str | None = None

class GateCancelled(RuntimeError):
    """A matching server cancellation closed the attempted gate."""

@dataclass(frozen=True)
class RouteLeg:
    source: str
    gate_name: str
    destination: str

@dataclass
class TargetSelection:
    sector: str
    target: int
    requested_at: float
    client_target_before: int
    request_observed: bool = False
    probe_requested_at: float | None = None
    probe_observed: bool = False
    acknowledged_at: float | None = None
    evidence: str | None = None
    changed: bool = False

class PacketRow(TypedDict):
    time: float
    proxy: int
    born: str
    outgoing: bool
    payload_hex: str

class ExplorationClaim(TypedDict):
    pid: int
    born: str
    sector: str
    gate_id: int
    destination: str

def gate_label(name: str) -> bool:
    label = name.casefold()
    return not label.startswith('nav ') and ('gate' in label or label.startswith('accelerator to '))

class SectorGraph:
    def __init__(self, path: Path, peers: tuple[Path, ...] = ()) -> None:
        self.path = path
        self.peers = peers
        self.sectors: dict[str, dict[int, Gate]] = {}
        if path.exists():
            saved = json.loads(path.read_text())
            for sector, gates in saved['sectors'].items():
                self.sectors[sector] = {int(g['item_id']): Gate(**g) for g in gates if gate_label(g['name'])}
        self.infer_return_gates()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with request_lock(self.path.parent / 'sector_graph_lock'):
            if self.path.exists():
                self.merge(SectorGraph(self.path))
            atomic_json(self.path, {'updated': time.time(), 'directed': False,
                'coverage': 'Confirmed crossings; reverse gates inferred under the user-selected undirected model.',
                'sectors': {s: [asdict(g) for g in gates.values()] for s, gates in self.sectors.items()}})

    def gate(self, sector: str, item_id: int, name: str) -> Gate:
        gates = self.sectors.setdefault(sector, {})
        if item_id not in gates:
            gates[item_id] = Gate(item_id, name)
            self.infer_return_gates()
        return gates[item_id]

    def confirm(self, source: str, item_id: int, destination: str, when: float) -> None:
        gate = self.sectors[source][item_id]
        if gate.destination is not None and gate.destination != destination:
            raise RuntimeError(f'Gate destination changed: {source}/{item_id}; stopped')
        gate.destination, gate.confirmed_at = destination, when
        gate.inferred_reverse = False
        gate.unavailable_reason = None
        self.sectors.setdefault(destination, {})
        self.infer_return_gates()
        self.save()

    def labelled_destination(self, gate: Gate) -> str | None:
        label = gate.name.casefold().partition('to ')[2].removesuffix(' sector').strip()
        return next((sector for sector in self.sectors if sector.casefold() == label), None)

    def infer_return_gates(self) -> None:
        # New observations can make a previously unique named pair ambiguous.
        # Recompute inferences without changing confirmed crossings.
        for gates in self.sectors.values():
            for gate in gates.values():
                if gate.inferred_reverse and gate.confirmed_at is None:
                    gate.destination, gate.inferred_reverse = None, False
        connections = {frozenset((source, gate.destination))
                       for source, gates in self.sectors.items() for gate in gates.values()
                       if gate.destination is not None and gate.confirmed_at is not None}
        named_arrivals: dict[tuple[str, str], set[str]] = {}
        for source, gates in self.sectors.items():
            for gate in gates.values():
                if gate.destination is not None and gate.confirmed_at is not None:
                    key = gate.destination, gate.name.casefold()
                    named_arrivals.setdefault(key, set()).add(source)
        for source, gates in self.sectors.items():
            for gate in gates.values():
                if gate.destination is not None:
                    continue
                destination = self.labelled_destination(gate)
                if destination is not None and frozenset((source, destination)) in connections:
                    gate.destination, gate.inferred_reverse = destination, True
                    continue
                # Named gates such as "Vishao's Gate" do not name their
                # destination. Pair the locally observed gate with a confirmed
                # arrival bearing the same name, only when both are unique.
                origins = named_arrivals.get((source, gate.name.casefold()), set())
                same_name = sum(other.name.casefold() == gate.name.casefold() for other in gates.values())
                if len(origins) == 1 and same_name == 1:
                    gate.destination, gate.inferred_reverse = next(iter(origins)), True

    def refresh_peers(self) -> None:
        for path in self.peers:
            try:
                peer = SectorGraph(path)
            except (FileNotFoundError, PermissionError):
                continue
            self.merge(peer)

    def merge(self, peer: SectorGraph) -> None:
        for sector, gates in peer.sectors.items():
            for observed in gates.values():
                gate = self.gate(sector, observed.item_id, observed.name)
                if observed.unavailable_reason and gate.confirmed_at is None:
                    gate.unavailable_reason = observed.unavailable_reason
                if observed.confirmed_at is not None:
                    if gate.destination is not None and gate.destination != observed.destination:
                        raise RuntimeError(f'Peer graph destination conflict: {sector}/{gate.item_id}')
                    gate.destination = observed.destination
                    gate.confirmed_at = max(gate.confirmed_at or 0., observed.confirmed_at)
                    gate.inferred_reverse = False
                    gate.unavailable_reason = None
        self.infer_return_gates()

    def next_gate(self, sector: str, excluded: frozenset[tuple[str, int]] = frozenset()) -> Gate | None:
        self.refresh_peers()
        # Reverse gates count as mapped; backtrack only to reach a frontier.
        queue: deque[tuple[str, Gate | None]] = deque([(sector, None)])
        seen = {sector}
        while queue:
            current, first = queue.popleft()
            gates = self.sectors.get(current, {})
            # Prefer a gate labelled for an unseen sector before immediately
            # taking the return gate. The label is a hint, never a confirmed edge.
            candidates = sorted(gates.values(), key=self.destination_already_seen)
            for gate in candidates:
                if (gate.destination is None and not gate.unavailable_reason
                        and (current, gate.item_id) not in excluded):
                    return first or gate
            for gate in gates.values():
                if gate.destination and not gate.unavailable_reason and gate.destination not in seen:
                    seen.add(gate.destination)
                    queue.append((gate.destination, first or gate))
        return None

    def destination_already_seen(self, gate: Gate) -> bool:
        return self.labelled_destination(gate) is not None

class ExplorationCoordinator:
    """Reserve a branch across explorer processes before submitting a command."""

    def __init__(self, name: str, root: Path = ROOT / 'diagnostics/exploration_coordination') -> None:
        import os
        self.name, self.root = name, root
        self.pid: int = os.getpid()
        self.born: str = process_identity(self.pid)
        self.path = root / 'claims.json'

    def claims(self) -> dict[str, ExplorationClaim]:
        if not self.path.exists():
            return {}
        saved = cast(dict[str, ExplorationClaim], json.loads(self.path.read_text()))
        return {name: claim for name, claim in saved.items()
                if process_identity(claim['pid']) == claim['born']}

    def choose(self, graph: SectorGraph, sector: str) -> Gate | None:
        with request_lock(self.root):
            claims = self.claims()
            claims.pop(self.name, None)
            graph.refresh_peers()
            excluded = frozenset((source, gate.item_id)
                for source, gates in graph.sectors.items() for gate in gates.values()
                if any((claim['sector'] == source and claim['gate_id'] == gate.item_id)
                    or (claim['destination'] and claim['destination'] ==
                        gate.name.casefold().partition('to ')[2]) for claim in claims.values()))
            gate = graph.next_gate(sector, excluded)
            if gate:
                claims[self.name] = dict(pid=self.pid, born=self.born, sector=sector,
                    gate_id=gate.item_id, destination=gate.name.casefold().partition('to ')[2])
            atomic_json(self.path, claims)
            return gate

    def release(self) -> None:
        with request_lock(self.root):
            claims = self.claims()
            claims.pop(self.name, None)
            atomic_json(self.path, claims)

class GateExplorer:
    stop_on_private_message: bool = True

    def __init__(self, session: VendorSession, name: str, capture: Path, graph: SectorGraph,
                 *, control: PauseControl | None = None) -> None:
        self.session, self.name, self.capture_path, self.graph = session, name, capture, graph
        self.world = WorldTracker()
        self.cargo = ClientCargo(session.client_pid, session.client_born, session.game_id)
        self.control = control if control is not None else PauseControl(name)
        self.stop = ROOT / f'explore_{name}.stop'
        self.pending: tuple[str, int] | None = None
        self.selection: TargetSelection | None = None
        self.cancelled_transition: str | None = None
        self.pending_submitted_at = 0.0
        self.last_message = ''
        self.last_message_time = 0.0
        self.paused_seconds = 0.0
        self.jumps = 0
        self.stream = capture.open(encoding='utf-8')
        self.focus: SharedFocus | None = None
        self.stop_requested = Event()
        self.coordinator = ExplorationCoordinator(name)

    def active_time(self) -> float:
        return time.monotonic() - self.paused_seconds

    def observe(self, row: PacketRow) -> None:
        if row['proxy'] != self.session.proxy_pid or row['born'] != self.session.proxy_born:
            return
        data = bytes.fromhex(row['payload_hex'])
        previous = self.world.sector
        try:
            decoded = decode_datagram(data)
            frames = decoded.get('frames', [decoded])
        except ValueError:
            frames = []
        if row['outgoing'] and previous:
            for frame in frames:
                body = bytes.fromhex(frame.get('body_hex', ''))
                if frame['opcode'] == '0x0017' and len(body) == 16 and self.selection:
                    owner, _, game_id, target = struct.unpack('<4I', body)
                    if (owner == game_id == self.session.game_id
                            and row['time'] >= self.selection.requested_at):
                        if target != self.selection.target:
                            self.selection.changed = True
                        else:
                            self.selection.request_observed = True
                if frame['opcode'] == '0x005a' and len(body) == 20 and self.selection:
                    owner = struct.unpack_from('<I', body)[0]
                    subject, target = struct.unpack_from('>II', body, 8)
                    action = struct.unpack_from('<I', body, 16)[0]
                    if (self.selection.request_observed and self.selection.probe_requested_at is not None
                            and row['time'] >= self.selection.probe_requested_at
                            and owner == subject == self.session.game_id
                            and target == self.selection.target and action == 1):
                        self.selection.probe_observed = True
                if frame['opcode'] == '0x002c' and len(body) == 24:
                    game_id, action, target, optional = struct.unpack_from('<4I', body, 8)
                    if (game_id == self.session.game_id and action == 18 and optional == 0
                            and target in self.graph.sectors.get(previous, {})):
                        self.pending = previous, target
                        self.cancelled_transition = None
                        self.pending_submitted_at = row['time']
        self.world.apply(data, row['outgoing'], row['time'])
        sector: str | None = self.world.sector
        if sector != previous:
            self.selection = None
        if sector:
            for obj in self.world.objects.values():
                name = str(obj.get('name') or '')
                if (obj.get('sector') == sector and obj.get('kind') == 'map_object'
                    and obj.get('nav_type') in (1, 2) and obj.get('navigation_present')
                    and not obj.get('removal_observed')
                    and obj.get('object_type') == 11 and gate_label(name)):
                    self.graph.gate(sector, int(obj['id'], 16), name)
        if sector and sector != previous and self.pending:
            source, gate_id = self.pending
            if sector != source:
                self.graph.confirm(source, gate_id, sector, row['time'])
                log_event(f'Gate edge confirmed: {source} --{gate_id:#x}--> {sector}')
                self.pending = None
        # Record rejection text and private messages without generating chat replies.
        for frame in ([] if row['outgoing'] else frames):
            if frame['opcode'] == '0x0019' and self.selection:
                body = bytes.fromhex(frame.get('body_hex', ''))
                if (len(body) == 8 and self.selection.request_observed
                        and row['time'] >= self.selection.requested_at):
                    target, target_of_target = struct.unpack('<II', body)
                    # Observed reply: SendSetTarget(requested_target, -1).
                    # Other target/target-of-target formats are not acknowledgments.
                    if target_of_target == 0xffffffff:
                        if target == self.selection.target and not self.selection.changed:
                            self.selection.acknowledged_at = row['time']
                            self.selection.evidence = 'server target reply 0x19'
                        else:
                            self.selection.changed = True
            if frame['opcode'] == '0x005c' and self.selection:
                selection = self.selection
                if (selection.probe_observed and selection.probe_requested_at is not None
                        and row['time'] >= selection.probe_requested_at):
                    try:
                        update = decode_verb_update(bytes.fromhex(frame.get('body_hex', '')))
                    except ValueError:
                        continue
                    if update.target_id == selection.target and not selection.changed:
                        selection.acknowledged_at = row['time']
                        selection.evidence = 'server selected-target verbs 0x5c'
                    else:
                        selection.changed = True
            chat = frame.get('chat')
            if (self.stop_on_private_message and chat and chat.get('event') == 3
                    and str(chat.get('channel', '')).strip().casefold() == 'private'):
                raise InterruptedError('Incoming private message; gate explorer stopped')
            if frame['opcode'] == '0x001d':
                body = bytes.fromhex(frame.get('body_hex', ''))
                self.last_message = body[3:].split(b'\0', 1)[0].decode('cp1252', errors='replace')
                self.last_message_time = row['time']
            if frame['opcode'] == '0x0031' and self.pending:
                body = bytes.fromhex(frame.get('body_hex', ''))
                if len(body) == 8:
                    gate_id, state = struct.unpack('<II', body)
                    if (gate_id == self.pending[1] and state == 3
                            and self.last_message.casefold() == 'current target canceled.'
                            and self.last_message_time >= self.pending_submitted_at
                            and 0 <= row['time'] - self.last_message_time <= 5):
                        source, target = self.pending
                        reason = 'Server canceled target and closed the attempted gate'
                        self.graph.sectors[source][target].unavailable_reason = reason
                        self.cancelled_transition = reason
                        self.pending = None
                        self.graph.save()
                        log_event(f'Gate skipped: sector={source}, gate={target:#x}, reason={reason}')

    def poll(self) -> None:
        for _ in range(2048):
            position = self.stream.tell()
            line = self.stream.readline()
            if not line:
                break
            if not line.endswith('\n'):
                self.stream.seek(position)
                break
            self.observe(cast(PacketRow, json.loads(line)))

    def checkpoint(self) -> None:
        self.session.check()
        if self.stop.exists() or self.stop_requested.is_set():
            raise InterruptedError('Gate explorer stop requested')
        self.poll()
        if self.control.requested():
            started = time.monotonic()
            self.control.acknowledge(True)
            try:
                while self.control.requested():
                    self.session.check()
                    if self.stop.exists() or self.stop_requested.is_set():
                        raise InterruptedError('Gate explorer stop requested')
                    self.poll()
                    time.sleep(.1)
            finally:
                self.paused_seconds += time.monotonic() - started
                self.control.acknowledge(False)

    def send(self, opcode: int, body: bytes) -> None:
        self.checkpoint()
        report: dict[str, object] = {}
        proxy_commands.submit(self.session.proxy_pid, proxy_commands.pack_command(opcode, body),
                              report, before_submit=self.command_checkpoint)
        if report.get('state') != 'handler_returned':
            raise RuntimeError(f'Gate command uncertain: {report.get("state")}; no retry')

    def command_checkpoint(self) -> None:
        self.checkpoint()
        if self.cancelled_transition:
            raise GateCancelled(self.cancelled_transition)
        if self.pending is not None:
            source, target = self.pending
            if self.world.sector != source or self.cargo.inventory is None:
                raise RuntimeError('Sector changed before gate activation; stopped')
            self.validate_selected_target(source, target)

    def validate_selected_target(self, source: str, target: int) -> None:
        if self.world.sector != source or self.cargo.inventory is None:
            raise RuntimeError('Sector changed before target activation; stopped')
        self.cargo.validate_inventory(self.cargo.inventory)
        current = self.cargo.memory().u32(self.cargo.inventory - 0x1990 + 0x130c)
        selection = self.selection
        if selection is None:
            if current != target:
                raise RuntimeError('Selected gate changed before activation; stopped')
            return
        if ((selection.sector, selection.target) != (source, target) or selection.changed
                or not selection.request_observed or selection.acknowledged_at is None
                or selection.acknowledged_at < selection.requested_at
                or current not in (target, selection.client_target_before)):
            raise RuntimeError('Selected gate changed or server selection is unconfirmed; stopped')

    def select_target(self, source: str, target: int, label: str) -> None:
        self.checkpoint()
        if self.world.sector != source:
            raise RuntimeError('Sector changed before target selection; stopped')
        self.cargo.close()
        self.cargo.open()
        assert self.cargo.inventory is not None
        target_address = self.cargo.inventory - 0x1990 + 0x130c
        self.selection = TargetSelection(source, target, time.time(), self.cargo.memory().u32(target_address))
        log_event(f'Gate exploration selecting: sector={source}, gate={label}, id={target:#x}')
        self.send(0x17, struct.pack('<II', self.session.game_id, target))
        self.checkpoint()
        if self.selection is not None and self.selection.acknowledged_at is None:
            # Servers may suppress an identical SetTarget reply when the same
            # object was selected in an earlier run. A fresh read-only verb
            # request returns availability for the server's selected object.
            self.selection.probe_requested_at = time.time()
            self.send(0x5a, struct.pack('>II', self.session.game_id, target) + struct.pack('<I', 1))
        deadline = self.active_time() + 5
        while True:
            self.checkpoint()
            selection = self.selection
            if self.world.sector != source or selection is None or selection.changed:
                raise RuntimeError('Target changed during selection; no activation sent')
            if selection.acknowledged_at is not None:
                self.validate_selected_target(source, target)
                current = self.cargo.memory().u32(target_address)
                log_event(f'Target selection acknowledged: id={target:#x}, client_target={current:#x}, source={selection.evidence}')
                return
            if self.active_time() >= deadline:
                raise RuntimeError('Gate selection not confirmed by server; '
                    f'request_observed={selection.request_observed}, probe_observed={selection.probe_observed}, '
                    f'client_target={self.cargo.memory().u32(target_address):#x}; no gate activation sent')
            time.sleep(.05)

    def traverse(self, gate: Gate) -> None:
        self.checkpoint()
        if self.pending is not None:
            raise RuntimeError('Previous gate transition is still pending; no new command sent')
        self.cancelled_transition = None
        source: str = self.world.sector
        self.select_target(source, gate.item_id, gate.name)
        if gate.item_id not in self.graph.sectors[source]:
            raise RuntimeError('Gate disappeared from source sector')
        self.pending = source, gate.item_id
        self.cancelled_transition = None
        submitted = time.time()
        self.pending_submitted_at = submitted
        self.send(0x2c, struct.pack('<4I', self.session.game_id, 18, gate.item_id, 0))
        self.wait_for_transition(submitted)
        self.cargo.close()
        self.wait_for_gate_catalog()
        self.jumps += 1
        self.graph.save()

    def wait_for_transition(self, submitted: float) -> None:
        pending = self.pending
        deadline = self.active_time() + 60
        while self.pending is not None:
            self.checkpoint()
            if self.last_message_time >= submitted and any(s in self.last_message.casefold()
                    for s in ('cannot initiate gate', 'unable to gate', 'cannot gate', 'gate is closed')):
                raise RuntimeError(self.last_message)
            if self.active_time() >= deadline:
                raise TimeoutError(f'Gate transition unconfirmed: {pending}; no retry')
            time.sleep(.05)
        if self.cancelled_transition:
            raise GateCancelled(self.cancelled_transition)

    def wait_for_gate_catalog(self) -> None:
        deadline = self.active_time() + 30
        previous: frozenset[str] = frozenset()
        changed_at = self.active_time()
        while True:
            self.checkpoint()
            gates = frozenset(str(obj['id']) for obj in self.world.objects.values()
                   if obj.get('sector') == self.world.sector and obj.get('kind') == 'map_object'
                   and obj.get('navigation_present') and obj.get('nav_type') in (1, 2)
                   and obj.get('object_type') == 11 and gate_label(str(obj.get('name', ''))))
            now = self.active_time()
            if gates != previous:
                previous, changed_at = gates, now
            if gates and now - changed_at >= .35:
                return
            if now >= deadline:
                raise TimeoutError('Arrival confirmed but sector gate catalog did not arrive')
            time.sleep(.05)

    def follow_route(self, route: list[RouteLeg]) -> None:
        for leg in route:
            self.checkpoint()
            self.graph.refresh_peers()
            if self.world.sector != leg.source:
                raise RuntimeError(f'Route expected {leg.source}, received {self.world.sector}')
            gates = [gate for gate in self.graph.sectors.get(leg.source, {}).values()
                     if leg.gate_name.casefold() in gate.name.casefold() and not gate.unavailable_reason]
            if len(gates) != 1:
                raise RuntimeError(f'Route gate is unavailable or ambiguous: {leg.source}/{leg.gate_name}')
            self.traverse(gates[0])
            if self.world.sector != leg.destination:
                raise RuntimeError(f'Route arrived at {self.world.sector}, expected {leg.destination}')
        log_event(f'Requested route arrived: sector={self.world.sector}')

    def run(self, seed: Path, max_jumps: int, initial_sector: str | None = None,
            route: list[RouteLeg] | None = None) -> None:
        reason = 'observed reachable gate frontier exhausted'
        try:
            self.focus = SharedFocus(f'gate_explore_{self.name}', self.name)
            self.focus.start_comms(self.stop_requested.set)
            for row in cast(list[PacketRow], json.loads(seed.read_text())):
                self.observe(row)
            self.poll()
            while self.stream.tell() < self.capture_path.stat().st_size:
                position = self.stream.tell()
                self.poll()
                if self.stream.tell() == position:
                    break
            if self.pending is not None:
                log_event(f'Waiting for previously submitted gate transition: {self.pending}; no resubmission')
                try:
                    self.wait_for_transition(time.time())
                except GateCancelled as error:
                    log_event(f'Previous gate canceled; continuing: {error}')
                self.wait_for_gate_catalog()
            if initial_sector and not self.world.sector:
                self.world.sector = initial_sector
                catalog = json.loads((ROOT / 'navigation/navpoints.json').read_text())
                for obj in catalog['sectors'].get(initial_sector, {}).values():
                    name = str(obj.get('name') or '')
                    if 'gate to ' in name.casefold() or name.casefold().startswith('accelerator to '):
                        self.graph.gate(initial_sector, int(obj['id'], 16), name)
            self.graph.save()
            log_event(f'Gate exploration started: sector={self.world.sector}, graph={self.graph.path}, max_jumps={max_jumps or "all"}')
            if route is not None:
                self.follow_route(route)
                reason = 'requested route completed'
                return
            while not max_jumps or self.jumps < max_jumps:
                self.checkpoint()
                if not self.world.sector:
                    raise RuntimeError('Current sector is not packet-confirmed')
                gate = self.coordinator.choose(self.graph, self.world.sector)
                if gate is None:
                    break
                try:
                    self.traverse(gate)
                except GateCancelled as error:
                    log_event(f'Gate exploration continuing after canceled gate: {error}')
                    self.cargo.close()
                finally:
                    self.coordinator.release()
            if max_jumps and self.jumps >= max_jumps:
                reason = 'jump limit reached'
        except BaseException as error:
            reason = f'{type(error).__name__}: {error}'
            raise
        finally:
            self.cargo.close()
            self.stream.close()
            self.control.close()
            self.coordinator.release()
            if self.focus:
                self.focus.close()
            self.graph.save()
            log_event(f'Gate exploration stopped: jumps={self.jumps}, sectors={len(self.graph.sectors)}, reason={reason}')

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', type=int, required=True)
    parser.add_argument('--proxy', type=int, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--seed', type=Path, required=True)
    parser.add_argument('--max-jumps', type=int, default=0)
    parser.add_argument('--initial-sector', help='Sector independently verified in the current client; bootstrap from saved gate catalog')
    parser.add_argument('--graph', type=Path, help='Separate graph output for this explorer')
    parser.add_argument('--peer-graph', type=Path, action='append', default=[],
                        help='Read another explorer\'s progress before choosing each gate')
    parser.add_argument('--route', type=Path, help='JSON list of source/gate_name/destination legs; stop on arrival')
    args = parser.parse_args()
    if args.max_jumps < 0:
        parser.error('--max-jumps must be nonnegative')
    configure_event_log(ROOT / f'macro_{args.name}.log', player=args.name)
    session = VendorSession.from_processes(args.client, process_identity(args.client), args.proxy, process_identity(args.proxy))
    explorer = GateExplorer(session, args.name, args.capture,
                            SectorGraph(args.graph or ROOT / 'navigation/sector_graph.json', tuple(args.peer_graph)))
    try:
        route = [RouteLeg(**leg) for leg in json.loads(args.route.read_text())] if args.route else None
        explorer.run(args.seed, args.max_jumps, args.initial_sector, route)
        return 0
    except (OSError, RuntimeError, InterruptedError) as error:
        log_event(f'Gate explorer failed: {error}')
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
