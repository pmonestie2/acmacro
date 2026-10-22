"""Read cargo identity from normal ship AUX deltas; never infer unknown slots."""
from __future__ import annotations

from dataclasses import dataclass
import struct

from protocol.vendor_catalog import item_fields


@dataclass(frozen=True)
class CargoItem:
    item_id: int | None
    quantity: int | None


class CargoReader:
    def __init__(self, body: bytes, offset: int = 0) -> None:
        self.body = body
        self.offset = offset

    def take(self, size: int) -> bytes:
        end = self.offset + size
        if end > len(self.body):
            raise ValueError('Truncated cargo AUX')
        result = self.body[self.offset:end]
        self.offset = end
        return result

    def flags(self, size: int, fields: int) -> int:
        value = int.from_bytes(self.take(size), 'little')
        if value & 15 != 2 or value >> (fields + 4):
            raise ValueError('Unsupported cargo AUX flags')
        return value >> 4

    def string(self) -> None:
        size = int.from_bytes(self.take(2), 'little')
        self.take(size)


def decode_cargo(body: bytes, game_id: int) -> dict[int, CargoItem] | None:
    if len(body) < 4 or struct.unpack_from('<I', body)[0] != game_id:
        return None
    if len(body) < 15:
        raise ValueError('Truncated owned ship AUX')
    # Field 36 is Inventory. Ignore unrelated ship updates, including extended
    # updates, unless they carry inventory; unsupported inventory fails closed.
    if not body[12] & 1:
        return None
    if body[6] != 1 or body[4:6] not in (bytes(2), struct.pack('<H', len(body) - 6)):
        raise ValueError('Unsupported cargo AUX header')
    reader = CargoReader(body, 7)
    fields = reader.flags(8, 60)
    for index in range(36):
        if not fields & (1 << index):
            continue
        if index in (0, 1, 2, 3, 28, 29):
            reader.string()
        elif index in (4, 6):
            percent = reader.flags(1, 2)
            for bit in (1, 2):
                if percent & bit:
                    reader.take(4)
        elif 16 <= index <= 25:
            reader.take(1)
        elif index == 35:
            raise ValueError('Unsupported shake prefix in cargo AUX')
        else:
            reader.take(4)
    inventory = reader.flags(2, 11)
    if inventory & 1:
        reader.take(4)  # CargoSpace
    if inventory & 2:
        reader.string()  # EquipMountModel
    if inventory & 12:
        raise ValueError('Unsupported mount prefix in cargo AUX')
    for bit in (16, 32):
        if inventory & bit:
            reader.take(4)
    if not inventory & 64:
        return None
    slots = reader.flags(6, 40)
    result: dict[int, CargoItem] = {}
    for slot in range(40):
        if slots & (1 << slot):
            # The common item decoder validates masks and string boundaries.
            start = reader.offset
            mask = int.from_bytes(reader.body[start:start + 2], 'little')
            if mask & 15 != 2:
                raise ValueError('Unsupported cargo item flags')
            reader.offset, item = item_fields(reader.body, start, slot)
            result[slot] = CargoItem(item.item_id, item.quantity)
    # Other inventory sections and later ship fields are independent of cargo.
    # Do not parse or guess their layout after the cargo array.
    return result


class VendorCargo:
    """Track all observed cargo; item identity, never purchase history, allows sales."""
    def __init__(self, game_id: int) -> None:
        self.game_id = game_id
        self.error: str | None = None
        self.slots: dict[int, CargoItem] = {}

    def invalidate(self, reason: str) -> None:
        self.error = reason
        self.slots.clear()

    def apply(self, body: bytes) -> None:
        if self.error:
            return
        try:
            updates = decode_cargo(body, self.game_id)
        except (ValueError, struct.error) as error:
            self.invalidate(str(error))
            return
        for slot, update in (updates or {}).items():
            previous = self.slots.get(slot, CargoItem(None, None))
            # A new identity must include its own quantity before it is sellable.
            quantity = previous.quantity if update.item_id in (None, previous.item_id) else None
            self.slots[slot] = CargoItem(
                update.item_id if update.item_id is not None else previous.item_id,
                update.quantity if update.quantity is not None else quantity)

    def check(self) -> None:
        if self.error:
            raise RuntimeError(f'Cargo identity unavailable: {self.error}; no further sales')

    def matching(self, item_id: int) -> dict[int, int]:
        self.check()
        return {slot: item.quantity for slot, item in self.slots.items()
                if item.item_id == item_id and item.quantity is not None and item.quantity > 0}

    def require(self, slot: int, item_id: int, quantity: int) -> None:
        self.check()
        if self.slots.get(slot) != CargoItem(item_id, quantity):
            raise RuntimeError(f'Cargo slot {slot} changed or is unverified; no sale sent')

    def free_slots(self, capacity: int) -> int:
        self.check()
        # Empty (-1) and unavailable (-2) are different client item IDs.
        return sum(self.slots.get(slot) == CargoItem(0xffffffff, 0)
                   for slot in range(capacity))

    def sale_confirmed(self, slot: int, item_id: int, previous: int) -> bool:
        self.check()
        item = self.slots.get(slot)
        if item is None:
            return False
        if item.item_id in (0, 0xfffffffe, 0xffffffff) and previous == 1:
            return True
        if item == CargoItem(item_id, previous - 1):
            return True
        if item != CargoItem(item_id, previous):
            raise RuntimeError(f'Unexpected cargo change after selling slot {slot}; stopped without retry')
        return False
