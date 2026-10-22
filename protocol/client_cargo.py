"""Read current cargo from the pinned client's AUX cache, without game input.

Offsets are for the fingerprinted client build. The ship GameID, inventory
groups, item properties and array size are checked before returning slot IDs.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
from pathlib import Path
import struct
import time
from collections.abc import Callable

from focus import process_identity
from protocol.proxy_commands import Process
from protocol.vendor_cargo import CargoItem


CLIENT_FINGERPRINT = '249f852496f341c00361be95b66e7c0becc2c39e7cba026f9227e38b602651c4'


class CargoChanging(RuntimeError):
    """A read overlapped an inventory update; no mutation was attempted."""


class MemoryRegion(C.Structure):
    _fields_ = [('base', C.c_void_p), ('allocation', C.c_void_p),
                ('allocation_protection', W.DWORD), ('partition', W.WORD),
                ('size', C.c_size_t), ('state', W.DWORD),
                ('protection', W.DWORD), ('kind', W.DWORD)]


class ClientCargo:
    def __init__(self, pid: int, born: str, game_id: int) -> None:
        self.pid = pid
        self.born = born
        self.game_id = game_id
        self.process: Process | None = None
        self.inventory: int | None = None

    def check(self) -> None:
        if process_identity(self.pid) != self.born:
            raise RuntimeError('Cargo client exited or changed; no further sales')

    def open(self) -> None:
        if self.process is not None:
            raise RuntimeError('Cargo reader is already open')
        self.check()
        self.process = Process(self.pid)
        try:
            name = C.create_unicode_buffer(32768)
            size = W.DWORD(len(name))
            self.process.check(self.process.k.QueryFullProcessImageNameW(self.process.handle, 0, name, C.byref(size)))
            if hashlib.sha256(Path(name.value).read_bytes()).hexdigest() != CLIENT_FINGERPRINT:
                raise RuntimeError('Unsupported client build; cargo offsets not used')
            self.inventory = self.find_inventory()
            self.snapshot()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.process is not None:
            self.process.k.CloseHandle(self.process.handle)
            self.process = None
        self.inventory = None

    def memory(self) -> Process:
        self.check()
        if self.process is None:
            raise RuntimeError('Cargo reader is closed')
        return self.process

    def validate_inventory(self, inventory: int) -> int:
        process = self.memory()
        ship = inventory - 0x1990
        if (process.u32(ship) != 0xb0094c or process.u32(ship + 0x9c) != self.game_id
                or process.u32(inventory) != 0xb00c7c or process.u32(inventory + 0x3f4) != 0xb00f54):
            raise RuntimeError('Owned cargo structure changed; no sale sent')
        return inventory + 0x3f4

    def find_inventory(self) -> int:
        process = self.memory()
        query = process.k.VirtualQueryEx
        query.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MemoryRegion), C.c_size_t]
        query.restype = C.c_size_t
        candidates: set[int] = set()
        address = 0
        while address < 0x80000000:
            self.check()
            region = MemoryRegion()
            if not query(process.handle, address, C.byref(region), C.sizeof(region)):
                break
            base = int(region.base or 0)
            address = base + region.size
            if region.state != 0x1000 or region.kind != 0x20000 or region.protection & 0x101:
                continue
            for offset in range(0, region.size, 1024 * 1024):
                start = max(0, offset - 3)
                try:
                    data: bytes = process.read(base + start, min(region.size - start, 1024 * 1024 + 3))
                except OSError:
                    continue
                at = 0
                while (at := data.find(struct.pack('<I', 0xb00c7c), at)) >= 0:
                    inventory = base + start + at
                    at += 4
                    try:
                        self.validate_inventory(inventory)
                    except (OSError, RuntimeError):
                        continue
                    candidates.add(inventory)
        if len(candidates) != 1:
            raise RuntimeError(f'Expected one owned cargo inventory, found {len(candidates)}; no sales sent')
        return candidates.pop()

    def read_slots(self) -> dict[int, CargoItem]:
        process = self.memory()
        if self.inventory is None:
            raise RuntimeError('Owned cargo inventory not located')
        group = self.validate_inventory(self.inventory)
        begin, end = struct.unpack('<II', process.read(group + 0x88, 8))
        if not begin or end - begin != 40 * 4:
            raise RuntimeError('Cargo array changed; no sale sent')
        pointers = process.read(begin, 40 * 4)
        slots: dict[int, CargoItem] = {}
        for slot, (item,) in enumerate(struct.iter_unpack('<I', pointers)):
            if (not item or process.u32(item) != 0xb0111c or
                    process.u32(item + 0x9c) != 0xb00d34 or process.u32(item + 0x124) != 0xb00d34):
                raise RuntimeError(f'Unrecognized cargo slot {slot}; no sale sent')
            slots[slot] = CargoItem(process.u32(item + 0x120), process.u32(item + 0x1a8))
        if pointers != process.read(begin, 40 * 4):
            raise CargoChanging('Cargo array changed during read')
        self.validate_inventory(self.inventory)
        return slots

    def capacity(self) -> int:
        """Read usable CargoSpace; the backing array always has forty entries."""
        process = self.memory()
        if self.inventory is None:
            raise RuntimeError('Owned cargo inventory not located')
        self.validate_inventory(self.inventory)
        if process.u32(self.inventory + 0x9c) != 0xb00d34:
            raise RuntimeError('Unrecognized cargo capacity property; no purchase sent')
        capacity = process.u32(self.inventory + 0x120)
        if not 1 <= capacity <= 40:
            raise RuntimeError(f'Invalid cargo capacity {capacity}; no purchase sent')
        self.check()
        return capacity

    def snapshot(self, *, checkpoint: Callable[[], None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> dict[int, CargoItem]:
        deadline = clock() + 1.0
        while True:
            if checkpoint is not None:
                checkpoint()
            self.check()
            try:
                first = self.read_slots()
                if first == self.read_slots():
                    self.check()
                    return first
            except CargoChanging:
                pass
            if clock() >= deadline:
                raise RuntimeError('Cargo reads did not settle within 1 second; no further sale sent')
            time.sleep(.01)
