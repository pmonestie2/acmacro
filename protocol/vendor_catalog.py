"""Decode observed vendor stock deltas and preserve fragmented AUX frames.

Array flags select changed slots; each selected item has its own field mask.
Unknown field masks fail explicitly rather than producing an invented catalog.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import TypedDict


class TradePair(TypedDict):
    item_id: int
    item_name: str
    buy_npc_id: int
    sell_npc_id: int
    buy_price: int
    sell_price: int
    profit_per_unit: int
    source: str


def trade_pair_markdown(pairs: list[TradePair]) -> list[str]:
    if not pairs:
        return []
    lines = ['## Marked trade pairs', '',
             '| Item | Buy from NPC | Sell to NPC | Buy | Sell | Profit/unit |',
             '|---|---:|---:|---:|---:|---:|']
    for pair in pairs:
        lines.append(f"| {pair['item_name']} | {pair['buy_npc_id']} | {pair['sell_npc_id']} | "
                     f"{pair['buy_price']:,} | {pair['sell_price']:,} | {pair['profit_per_unit']:,} |")
    lines += ['', 'Prices are the observed quotes recorded for these trades.', '', '## NPC inventories', '']
    return lines


@dataclass
class StockItem:
    slot: int
    item_id: int | None = None
    price: int | None = None
    quantity: int | None = None
    quality: float | None = None
    structure: float | None = None
    average_cost: float | None = None


def array_flags(body: bytes, offset: int, capacity: int) -> tuple[int, list[int]]:
    size = (capacity + 11) // 8
    if offset + size > len(body):
        raise ValueError('Truncated inventory array flags')
    flags = int.from_bytes(body[offset:offset + size], 'little')
    if flags & 0x04:
        raise ValueError('Extended inventory arrays are not supported')
    slots = [slot for slot in range(capacity) if flags & (1 << (slot + 4))]
    return offset + size, slots


def item_fields(body: bytes, offset: int, slot: int) -> tuple[int, StockItem]:
    if offset + 2 > len(body):
        raise ValueError('Truncated item field mask')
    mask = struct.unpack_from('<H', body, offset)[0]
    offset += 2
    if mask & ~0x3ff2:
        raise ValueError(f'Unknown item mask {mask:#x}')
    item = StockItem(slot)
    for bit, name, fmt, size in (
        (0x10, 'item_id', 'I', 4), (0x20, 'quantity', 'I', 4),
        (0x40, 'price', 'Q', 8), (0x80, 'average_cost', 'f', 4),
        (0x100, 'structure', 'f', 4), (0x200, 'quality', 'f', 4),
    ):
        if mask & bit:
            if offset + size > len(body):
                raise ValueError('Truncated item record')
            setattr(item, name, struct.unpack_from('<' + fmt, body, offset)[0])
            offset += size
    for bit in (0x400, 0x800, 0x1000, 0x2000):
        if mask & bit:
            if offset + 2 > len(body):
                raise ValueError('Truncated item string length')
            size = struct.unpack_from('<H', body, offset)[0]
            offset += 2 + size
            if offset > len(body):
                raise ValueError('Truncated item string')
    return offset, item


class VendorStock:
    def __init__(self, baseline: list[StockItem]) -> None:
        self.slots: list[StockItem] = [StockItem(**vars(item)) for item in baseline]

    def apply(self, body: bytes) -> list[StockItem] | None:
        if len(body) < 10 or body[:4] != bytes(4) or not body[7] & 0x80:
            return None
        offset = 10
        if body[7] & 0x40:
            offset, secure_slots = array_flags(body, offset, 96)
            for slot in secure_slots:
                offset, _ = item_fields(body, offset, slot)
        offset, changed_slots = array_flags(body, offset, 128)
        # Apply transactionally: a truncated update must not corrupt the cache.
        slots = [StockItem(**vars(item)) for item in self.slots]
        for slot in changed_slots:
            offset, update = item_fields(body, offset, slot)
            if slot >= len(self.slots) and update.item_id is None:
                raise ValueError('Vendor delta refers to an unknown cached item')
            while len(slots) <= slot:
                slots.append(StockItem(len(slots), 0xfffffffe))
            for name, value in vars(update).items():
                if name != 'slot' and value is not None:
                    setattr(slots[slot], name, value)
        if offset != len(body):
            raise ValueError('Unexpected data after vendor inventory')
        active = [item for item in slots
                  if item.item_id not in (0, 0xffffffff, 0xfffffffe)]
        if any(item.item_id is None or item.price is None for item in active):
            raise ValueError('Vendor delta refers to an unknown cached item')
        self.slots = slots
        return active


class AuxFragments:
    def __init__(self) -> None:
        self.pending: dict[tuple[int, int], tuple[int, bytes]] = {}

    def feed(self, raw: bytes, undecodable: bool) -> bytes | None:
        if len(raw) < 16:
            return None
        opcode = struct.unpack_from('<H', raw, 2)[0]
        owner, sequence = struct.unpack_from('<II', raw, 4)
        if opcode == 0x2016 and undecodable:
            length, inner = struct.unpack_from('<HH', raw, 12)
            if inner == 0x1b and 4 <= length <= 65535 and len(raw) - 12 < length:
                self.pending[(owner, sequence + 1)] = (length, raw[12:])
        elif opcode == 0x201a and (owner, sequence) in self.pending:
            length, data = self.pending.pop((owner, sequence))
            data += raw[12:]
            if len(data) == length:
                return data[4:]
            if len(data) < length:
                self.pending[(owner, sequence + 1)] = (length, data)
        return None
