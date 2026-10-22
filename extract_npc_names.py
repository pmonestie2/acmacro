"""Build a name index from captured packets, cached client avatars and source rosters.

This only reads files and client memory. It never sends game packets or input.
Historical source IDs and sector-local space object IDs are not station NPC IDs.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import struct
from typing import TypedDict, cast

from focus import process_identity
from protocol.proxy_commands import Process
from protocol.npc_names import NpcFrames, avatar_name, station_names
from protocol.world_tracker import WorldTracker

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class NameObservation:
    kind: str
    name: str
    source: str
    npc_id: int | None = None
    object_id: int | None = None
    sector: str | None = None
    location: str | None = None
    object_name: str | None = None


class CaptureRow(TypedDict):
    time: float
    proxy: int
    born: str
    outgoing: bool
    payload_hex: str


class NpcNameIndex:
    def __init__(self) -> None:
        self.observations: set[NameObservation] = set()
        self.unsupported_rosters: int = 0

    def load(self, path: Path) -> None:
        if not path.exists():
            return
        report = json.loads(path.read_text(encoding='utf-8'))
        for row in report['observations']:
            self.observations.add(NameObservation(**row))

    def read_capture(self, path: Path) -> None:
        worlds: dict[tuple[int, str], WorldTracker] = {}
        frames: dict[tuple[int, str], NpcFrames] = {}
        targets: dict[tuple[int, str], int] = {}
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict) or not all(key in value for key in CaptureRow.__annotations__):
                        continue
                    row = cast(CaptureRow, value)  # Capture JSON is the external schema boundary.
                    key = (row['proxy'], row['born'])
                    payload = bytes.fromhex(row['payload_hex'])
                    if key not in worlds:
                        worlds[key] = WorldTracker()
                        frames[key] = NpcFrames()
                    world = worlds[key]
                    previous_sector = world.sector
                    world.apply(payload, row['outgoing'], row['time'])
                    if previous_sector != world.sector:
                        targets.pop(key, None)
                    if row['outgoing']:
                        if len(payload) >= 28 and struct.unpack_from('<H', payload, 2)[0] == 0x2c:
                            _, action, target, _ = struct.unpack_from('<4I', payload, 12)
                            if action == 2:
                                targets[key] = target
                        continue
                    assembler = frames[key]
                    for frame in assembler.feed(payload, row['time']):
                        if frame.opcode == 0x52:
                            self.add_roster(frame.body, str(path), historical=False)
                        elif frame.opcode == 0x61 and len(frame.body) >= 245:
                            avatar_id = struct.unpack_from('<i', frame.body)[0]
                            if avatar_id not in (-1, -2):
                                continue  # Ordinary player portraits are not NPCs.
                            target_id = targets.get(key)
                            obj = world.objects.get(f'0x{target_id:08x}', {}) if target_id is not None else {}
                            self.observations.add(NameObservation(
                                'space_npc', avatar_name(frame.body, 4), str(path),
                                object_id=target_id, sector=world.sector, object_name=obj.get('name')))
                except (ValueError, KeyError, TypeError, struct.error):
                    continue

    def add_roster(self, body: bytes, source: str, *, historical: bool) -> None:
        try:
            names = station_names(body)
        except ValueError:
            self.unsupported_rosters += 1
            return
        for npc in names:
            self.observations.add(NameObservation(
                'historical_station_npc' if historical else 'station_npc', npc.name, source,
                npc_id=npc.npc_id, location=f'room {npc.room}, position {npc.location}'))

    def read_source_roster(self, path: Path) -> None:
        data = path.read_bytes()
        if len(data) < 4:
            return
        size, opcode = struct.unpack_from('<HH', data)
        if opcode == 0x52 and 4 <= size <= len(data):
            self.add_roster(data[4:size], str(path), historical=True)

    def write(self, destination: Path) -> None:
        rows = sorted(self.observations, key=lambda item: (item.name.casefold(), item.kind, item.source))
        report: dict[str, object] = {
            'notes': ['Only loaded/captured names are available from a client.',
                      'Historical source IDs must not be used as current server NPC IDs.',
                      'Space object IDs are local to a sector; client avatar names alone have no confirmed NPC ID.'],
            'unique_names': len({row.name.casefold() for row in rows}),
            'unsupported_rosters': self.unsupported_rosters,
            'observations': [asdict(row) for row in rows],
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        temporary.replace(destination)


class MemoryRegion(C.Structure):
    _fields_ = [('base', C.c_void_p), ('allocation', C.c_void_p), ('allocation_protection', W.DWORD),
                ('partition', W.WORD), ('size', C.c_size_t), ('state', W.DWORD),
                ('protection', W.DWORD), ('kind', W.DWORD)]


AVATAR_SIGNATURE = re.compile(rb'[\x00-\x64]\x00{4}\x02[\x00-\x02]\x00{3}[\x00-\x02]\x00{3}[\x00-\x01]\x00{3}')
MISSION_REFERENCE = re.compile(rb'Report to ([A-Z][ -~]{2,70}?) (?:on|at) ([A-Z][ -~]{2,70}?)\.')


def names_in_memory(data: bytes, source: str) -> set[NameObservation]:
    result: set[NameObservation] = set()
    for match in AVATAR_SIGNATURE.finditer(data):
        start = match.start() - 40
        if start < 0:
            continue
        first, last = data[start:start + 20], data[start + 20:start + 40]
        if any(b'\0' not in field or not re.fullmatch(rb"[A-Za-z][A-Za-z0-9 .'\-]{0,18}", field.split(b'\0', 1)[0])
               for field in (first, last)):
            continue  # Require two terminated name fields, not arbitrary display text.
        if any(field.split(b'\0', 1)[1].strip(b'\0') for field in (first, last) if b'\0' in field):
            continue
        try:
            name = avatar_name(data, start)
        except ValueError:
            continue
        result.add(NameObservation('client_avatar', name, source))
    for match in MISSION_REFERENCE.finditer(data):
        result.add(NameObservation('mission_reference', match[1].decode('cp1252'), source,
                                   location=match[2].decode('cp1252')))
    return result


def read_client_names(index: NpcNameIndex, pid: int, born: str) -> None:
    if process_identity(pid) != born:
        raise RuntimeError('Client identity changed; no memory read')
    process = Process(pid)
    query = process.k.VirtualQueryEx
    query.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MemoryRegion), C.c_size_t]
    query.restype = C.c_size_t
    source = f'client {pid}, born {born}'
    address = 0
    try:
        while address < 0x80000000:
            if process_identity(pid) != born:
                raise RuntimeError('Client exited or changed during name extraction')
            region = MemoryRegion()
            if not query(process.handle, address, C.byref(region), C.sizeof(region)):
                break
            base: int = region.base or 0
            address = base + region.size
            if region.state != 0x1000 or region.protection & 0x101:
                continue
            for offset in range(0, region.size, 4 * 1024 * 1024):
                start = max(0, offset - 256)
                size = min(region.size - start, 4 * 1024 * 1024 + 256)
                try:
                    data: bytes = process.read(base + start, size)
                except OSError:
                    continue
                index.observations.update(names_in_memory(data, source))
        if process_identity(pid) != born:
            raise RuntimeError('Client identity changed during name extraction')
    finally:
        process.k.CloseHandle(process.handle)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-dir', type=Path, default=ROOT / 'diagnostics')
    parser.add_argument('--source-dir', type=Path, default=ROOT / 'diagnostics/npc_source')
    parser.add_argument('--client-pid', type=int)
    parser.add_argument('--client-born')
    parser.add_argument('--output', type=Path, default=ROOT / 'protocol/npc_name_catalog.json')
    args = parser.parse_args()
    if (args.client_pid is None) != (args.client_born is None):
        parser.error('--client-pid and --client-born must be supplied together')
    index = NpcNameIndex()
    index.load(args.output)
    for count, path in enumerate(sorted(args.capture_dir.glob('*.jsonl')), 1):
        index.read_capture(path)
        if count % 20 == 0:
            print(f'Processed {count} captures; {len(index.observations)} name observations', flush=True)
    for path in args.source_dir.glob('*LoungeNPC*.dat'):
        index.read_source_roster(path)
    if args.client_pid is not None:
        print(f'Reading cached avatar names from client {args.client_pid}', flush=True)
        read_client_names(index, args.client_pid, args.client_born)
    index.write(args.output)
    print(f'Saved {len(index.observations)} observations to {args.output}', flush=True)
    for row in sorted(index.observations, key=lambda value: value.name):
        if any(word in row.name.casefold() for word in ('dionys', 'kerr', 'anjuren')):
            print(json.dumps(asdict(row), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
