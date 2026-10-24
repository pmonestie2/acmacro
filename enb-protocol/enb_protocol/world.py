"""Navigable world objects decoded without a game window or client memory."""
from dataclasses import dataclass
import math
import struct

from .wire import Frame, decode_batch, decode_frame


@dataclass(frozen=True)
class NavigationObject:
    object_id: int
    name: str
    object_type: int
    asset_id: int
    position: tuple[float, float, float]
    navigation_present: bool
    visited: bool
    navigation_type: int
    huge: bool

    @property
    def kind(self) -> str:
        return {11: 'gate', 12: 'station'}.get(self.object_type, 'navpoint')


def decode_map_object(body: bytes) -> NavigationObject:
    if len(body) < 60:
        raise ValueError('Truncated map object')
    name_length = struct.unpack_from('<H', body, 58)[0]
    if not name_length or len(body) != 60 + name_length:
        raise ValueError('Map object name length does not match its body')
    x, y, z = struct.unpack_from('<fff', body, 25)
    if not all(math.isfinite(value) and abs(value) < 1e9 for value in (x, y, z)):
        raise ValueError('Invalid map object coordinates')
    flags = body[57]
    return NavigationObject(
        object_id=struct.unpack_from('<I', body)[0],
        name=body[60:].decode('ascii'), object_type=body[4],
        asset_id=struct.unpack_from('<H', body, 5)[0], position=(x, y, z),
        navigation_present=bool(flags & 0x10), visited=bool(flags & 0x80),
        navigation_type=flags & 15, huge=bool(flags & 0x40),
    )


class World:
    def __init__(self) -> None:
        self.sector: str | None = None
        self.objects: dict[int, NavigationObject] = {}

    def apply_frame(self, frame: Frame) -> None:
        if frame.opcode == 0x0097:
            body = frame.body
            if len(body) < 15 or struct.unpack_from('<II', body) != (4, len(body) - 8):
                raise ValueError('Invalid sector description')
            fields = body[12:].split(b'\0')
            if len(fields) < 3 or not fields[1]:
                raise ValueError('Missing sector name')
            sector = fields[1].decode('ascii')
            if sector != self.sector:
                self.objects.clear()
            self.sector = sector
        elif frame.opcode == 0x2018:
            item = decode_map_object(frame.body)
            self.objects[item.object_id] = item
        elif frame.opcode == 0x0007:
            if len(frame.body) != 4:
                raise ValueError('Invalid object removal length')
            self.objects.pop(struct.unpack('<I', frame.body)[0], None)

    def apply_datagram(self, data: bytes) -> None:
        outer = decode_frame(data)
        if outer.opcode == 0x2016:
            for frame in decode_batch(data).frames:
                self.apply_frame(frame)

    def navigable(self) -> list[NavigationObject]:
        return sorted((item for item in self.objects.values() if item.navigation_present),
                      key=lambda item: (item.kind, item.name.casefold(), item.object_id))
