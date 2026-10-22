"""NPC names from station rosters and space-conversation avatar packets.

Station NPC IDs and sector-local space object IDs are separate namespaces.
The layouts come from PacketStructures.h and SendLoungeNPC/SendPIPAvatar.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class StationNpcName:
    npc_id: int
    name: str
    room: int
    location: int


def avatar_name(body: bytes, offset: int = 0) -> str:
    if offset < 0 or offset + 40 > len(body):
        raise ValueError('Truncated avatar name fields')
    parts: list[str] = []
    for field in (body[offset:offset + 20], body[offset + 20:offset + 40]):
        text = field.split(b'\0', 1)[0]
        if any(char < 32 or char == 127 for char in text):
            raise ValueError('Invalid avatar name characters')
        parts.append(text.decode('cp1252').strip())
    name = ' '.join(part for part in parts if part)
    if not name:
        raise ValueError('Empty avatar name')
    return name


def station_names(body: bytes) -> list[StationNpcName]:
    if len(body) < 16:
        raise ValueError('Truncated station roster')
    _, rooms = struct.unpack_from('<II', body)
    if rooms > 5:
        raise ValueError('Invalid station room count')
    offset = 8 + 28 * rooms
    if offset + 4 > len(body):
        raise ValueError('Truncated station rooms')
    terms = struct.unpack_from('<I', body, offset)[0]
    if terms > 15:
        raise ValueError('Invalid station terminal count')
    offset += 4 + 16 * terms
    if offset + 4 > len(body):
        raise ValueError('Truncated station terminals')
    count = struct.unpack_from('<I', body, offset)[0]
    offset += 4
    if count > 30 or offset + 265 * count != len(body):
        raise ValueError('Unsupported or truncated station NPC records')
    names: list[StationNpcName] = []
    for index in range(count):
        start = offset + 265 * index
        room, location, npc = struct.unpack_from('<iii', body, start)
        if not 0 <= room <= 5 or location < 0 or npc <= 0:
            raise ValueError('Invalid station NPC identity')
        names.append(StationNpcName(npc, avatar_name(body, start + 24), room, location))
    if len({item.npc_id for item in names}) != len(names):
        raise ValueError('Duplicate station NPC IDs')
    return names


@dataclass(frozen=True)
class NamedFrame:
    opcode: int
    body: bytes


@dataclass
class PartialFrame:
    started: float
    data: bytes


class NpcFrames:
    """Reassemble application frames without confusing avatar updates with continuations."""

    def __init__(self) -> None:
        self.pending: dict[tuple[int, int], PartialFrame] = {}

    def feed(self, payload: bytes, now: float) -> list[NamedFrame]:
        self.pending = {key: value for key, value in self.pending.items() if now - value.started <= 10}
        if len(payload) < 12:
            return []
        opcode = struct.unpack_from('<H', payload, 2)[0]
        owner, sequence = struct.unpack_from('<II', payload, 4)
        if opcode == 0x2016:
            data = payload[12:]
        elif opcode == 0x201a:
            partial = self.pending.pop((owner, sequence), None)
            if partial is None:
                return []
            data = partial.data + payload[12:]
        else:
            return []
        frames: list[NamedFrame] = []
        offset = 0
        while offset < len(data):
            if len(data) - offset < 4:
                break
            size, inner = struct.unpack_from('<HH', data, offset)
            if size < 4:
                break
            if offset + size > len(data):
                if len(self.pending) >= 32:
                    self.pending.pop(next(iter(self.pending)))
                self.pending[(owner, sequence + 1)] = PartialFrame(now, data[offset:])
                break
            if inner in (0x52, 0x61):
                frames.append(NamedFrame(inner, data[offset + 4:offset + size]))
            offset += size
        return frames
