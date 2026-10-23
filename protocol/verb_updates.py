"""Decode observed 0x005c target verb availability (Player::UpdateVerbs)."""
from __future__ import annotations

from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class VerbState:
    verb: int
    attribute: int


@dataclass(frozen=True)
class VerbUpdate:
    target_id: int
    defaults: tuple[VerbState, ...]
    current: tuple[VerbState, ...]

    def attribute(self, verb: int) -> int | None:
        return next((state.attribute for state in self.current if state.verb == verb), None)


def decode_verb_update(body: bytes) -> VerbUpdate:
    if len(body) < 12:
        raise ValueError('Truncated verb update')
    target = struct.unpack_from('>I', body)[0]
    if target in (0, 0xffffffff):
        raise ValueError('Invalid verb-update target')
    offset = 4
    groups: list[tuple[VerbState, ...]] = []
    for _ in range(2):
        if offset + 4 > len(body):
            raise ValueError('Truncated verb count')
        count = struct.unpack_from('>I', body, offset)[0]
        offset += 4
        if count > 64 or offset + count * 4 > len(body):
            raise ValueError('Invalid verb count')
        states: list[VerbState] = []
        seen: set[int] = set()
        for _ in range(count):
            attribute, verb = struct.unpack_from('<HH', body, offset)
            offset += 4
            if not verb or verb in seen:
                raise ValueError('Invalid or duplicate verb ID')
            seen.add(verb)
            states.append(VerbState(verb, attribute))
        groups.append(tuple(states))
    if offset != len(body):
        raise ValueError('Unexpected bytes after verb update')
    return VerbUpdate(target, groups[0], groups[1])
