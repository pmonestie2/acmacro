"""Read the current player's secure inventory in the fingerprinted client."""
from __future__ import annotations

from collections.abc import Callable
import ctypes as C
from ctypes import wintypes as W
import struct
import time

from protocol.client_cargo import ClientCargo, MemoryRegion
from protocol.vendor_cargo import CargoItem


class ClientVault:
    """Share the cargo reader's checked process handle and ownership lifecycle."""
    def __init__(self, cargo: ClientCargo) -> None:
        self.cargo = cargo
        self.root: int | None = None

    def group(self) -> int:
        process = self.cargo.memory()
        if self.root is None:
            raise RuntimeError('Vault cache not located')
        group = self.root + 0x1c8
        if (process.u32(self.root) != 0xb03360 or process.u32(self.root + 0x74) != 0
                or process.u32(self.root + 0x9c) != 0
                or process.u32(group) != 0xb00f54
                or process.u32(group + 0x74) != self.root
                or process.u32(group + 0x78) != 2):
            raise RuntimeError('Owned player vault structure changed; no transfer sent')
        return group

    def locate(self, checkpoint: Callable[[], None]) -> None:
        process = self.cargo.memory()
        query = process.k.VirtualQueryEx
        query.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MemoryRegion), C.c_size_t]
        query.restype = C.c_size_t
        candidates: set[int] = set()
        address = 0
        while address < 0x80000000:
            checkpoint()
            region = MemoryRegion()
            if not query(process.handle, address, C.byref(region), C.sizeof(region)):
                break
            base = int(region.base or 0)
            address = base + region.size
            if region.state != 0x1000 or region.kind != 0x20000 or region.protection & 0x101:
                continue
            for offset in range(0, region.size, 1024 * 1024):
                checkpoint()
                start = max(0, offset - 3)
                try:
                    data: bytes = process.read(base + start, min(region.size - start, 1024 * 1024 + 3))
                except OSError:
                    continue
                at = 0
                while (at := data.find(struct.pack('<I', 0xb03360), at)) >= 0:
                    self.root = base + start + at
                    at += 4
                    try:
                        self.read_slots()
                    except (OSError, RuntimeError):
                        continue
                    candidates.add(self.root)
        self.root = None
        if len(candidates) != 1:
            raise RuntimeError(f'Expected one owned vault cache, found {len(candidates)}; no transfer sent')
        self.root = candidates.pop()

    def read_slots(self) -> dict[int, CargoItem]:
        process = self.cargo.memory()
        group = self.group()
        begin, end = struct.unpack('<II', process.read(group + 0x88, 8))
        if not begin or end - begin != 96 * 4:
            raise RuntimeError('Vault array changed; no transfer sent')
        pointers = process.read(begin, 96 * 4)
        slots: dict[int, CargoItem] = {}
        for slot, (item,) in enumerate(struct.iter_unpack('<I', pointers)):
            if (not item or process.u32(item) != 0xb0111c
                    or process.u32(item + 0x9c) != 0xb00d34
                    or process.u32(item + 0x124) != 0xb00d34):
                raise RuntimeError(f'Unrecognized vault slot {slot}; no transfer sent')
            slots[slot] = CargoItem(process.u32(item + 0x120), process.u32(item + 0x1a8))
        if pointers != process.read(begin, 96 * 4):
            raise RuntimeError('Vault changed during read; no transfer sent')
        self.group()
        return slots

    def snapshot(self, checkpoint: Callable[[], None], clock: Callable[[], float] = time.monotonic) -> dict[int, CargoItem]:
        deadline = clock() + 1
        while True:
            checkpoint()
            first = self.read_slots()
            if first == self.read_slots():
                self.cargo.check()
                return first
            if clock() >= deadline:
                raise RuntimeError('Vault reads did not settle; no transfer sent')
            time.sleep(.01)
