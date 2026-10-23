"""Read the owned actor's current Player AUX sector in the supported client."""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import struct
from typing import Iterator

from protocol.client_cargo import ClientCargo, MemoryRegion

ACTOR_VTABLE = 0xb03240
PLAYER_VTABLE = 0xb03360
STRING_PROPERTY_VTABLE = 0xaeb0e4
INTEGER_PROPERTY_VTABLE = 0xb00d34
ACTOR_PLAYER = 0x12c0
ACTOR_NETWORK_GAME_ID = 0x12e8
SECTOR_NAME_PROPERTY = 0x1294
SECTOR_NUMBER_PROPERTY = 0x131c
PROPERTY_VALUE = 0x84


@dataclass(frozen=True)
class SectorSnapshot:
    actor: int
    player: int
    name_pointer: int
    name: str
    number: int


class ClientSectorReader:
    """Follow an actor bound to the same full GameID as the owned cargo reader."""

    def __init__(self, cargo: ClientCargo) -> None:
        self.cargo = cargo

    def regions(self) -> Iterator[tuple[int, int]]:
        process = self.cargo.memory()
        query = process.k.VirtualQueryEx
        query.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(MemoryRegion), C.c_size_t]
        query.restype = C.c_size_t
        address = 0
        while address < 0x80000000:
            self.cargo.check()
            region = MemoryRegion()
            if not query(process.handle, address, C.byref(region), C.sizeof(region)):
                break
            base = int(region.base or 0)
            following = base + region.size
            if following <= address:
                raise RuntimeError('Invalid client memory region while locating current sector')
            address = following
            if (region.state == 0x1000 and region.kind == 0x20000
                    and not region.protection & 0x101):
                yield base, int(region.size)

    def snapshot(self, actor: int) -> SectorSnapshot:
        self.cargo.check()
        process = self.cargo.memory()
        if (process.u32(actor) != ACTOR_VTABLE
                or process.read(actor + ACTOR_NETWORK_GAME_ID, 4) != struct.pack('>I', self.cargo.game_id)):
            raise RuntimeError('Current-sector actor does not belong to the owned character')
        player = process.u32(actor + ACTOR_PLAYER)
        if (not player or process.u32(player) != PLAYER_VTABLE or process.u32(player + 0x9c) != 0
                or process.u32(player + SECTOR_NAME_PROPERTY) != STRING_PROPERTY_VTABLE
                or process.u32(player + SECTOR_NUMBER_PROPERTY) != INTEGER_PROPERTY_VTABLE):
            raise RuntimeError('Unrecognized owned Player AUX sector properties')
        pointer = process.u32(player + SECTOR_NAME_PROPERTY + PROPERTY_VALUE)
        if not pointer:
            raise RuntimeError('Current sector name is unavailable')
        raw = process.read(pointer, 128)
        end = raw.find(b'\0')
        if not 0 < end < 128 or any(character < 32 or character > 126 for character in raw[:end]):
            raise RuntimeError('Invalid current sector name in owned Player AUX')
        number = process.u32(player + SECTOR_NUMBER_PROPERTY + PROPERTY_VALUE)
        if not 0 < number < 0x1000000:
            raise RuntimeError('Invalid current sector number in owned Player AUX')
        return SectorSnapshot(actor, player, pointer, raw[:end].decode('ascii'), number)

    def candidate_actors(self) -> set[int]:
        process = self.cargo.memory()
        needle = struct.pack('<I', ACTOR_VTABLE)
        found: set[int] = set()
        for base, size in self.regions():
            for offset in range(0, size, 1024 * 1024):
                start = max(0, offset - 3)
                try:
                    data = process.read(base + start, min(size - start, 1024 * 1024 + 3))
                except OSError:
                    continue
                at = 0
                while (at := data.find(needle, at)) >= 0:
                    actor = base + start + at
                    at += 4
                    try:
                        self.snapshot(actor)
                    except (OSError, RuntimeError):
                        continue
                    found.add(actor)
        return found

    def read(self) -> str:
        if self.cargo.inventory is None:
            raise RuntimeError('Owned cargo reader must be open to identify the current sector')
        self.cargo.validate_inventory(self.cargo.inventory)
        candidates = self.candidate_actors()
        if len(candidates) != 1:
            raise RuntimeError(f'Expected one owned sector actor, found {len(candidates)}')
        actor = candidates.pop()
        first = self.snapshot(actor)
        self.cargo.validate_inventory(self.cargo.inventory)
        second = self.snapshot(actor)
        self.cargo.check()
        if first != second:
            raise RuntimeError('Current sector changed while reading the owned actor; no navigation sent')
        return second.name


def read_current_sector(client_pid: int, client_born: str, game_id: int) -> str:
    """Own and close a fingerprinted reader; sends no commands to client or server."""
    cargo = ClientCargo(client_pid, client_born, game_id)
    try:
        cargo.open()
        return ClientSectorReader(cargo).read()
    finally:
        cargo.close()
