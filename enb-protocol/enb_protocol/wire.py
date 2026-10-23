"""Validated little-endian frames and sequenced UDP batches."""
from dataclasses import dataclass
import struct

FRAME_HEADER = struct.Struct('<HH')
UDP_HEADER = struct.Struct('<HHII')


@dataclass(frozen=True)
class Frame:
    opcode: int
    body: bytes

    def encode(self) -> bytes:
        length = FRAME_HEADER.size + len(self.body)
        if length > 65535:
            raise ValueError('Frame exceeds the wire length field')
        return FRAME_HEADER.pack(length, self.opcode) + self.body


@dataclass(frozen=True)
class Batch:
    player_id: int
    sequence: int
    frames: tuple[Frame, ...]


def decode_frame(data: bytes) -> Frame:
    if len(data) < FRAME_HEADER.size:
        raise ValueError('Truncated frame header')
    size, opcode = FRAME_HEADER.unpack_from(data)
    if size != len(data):
        raise ValueError('Frame length does not match available bytes')
    return Frame(opcode, data[FRAME_HEADER.size:])


def decode_batch(data: bytes) -> Batch:
    outer = decode_frame(data)
    if outer.opcode != 0x2016 or len(data) < UDP_HEADER.size:
        raise ValueError('Not a complete sequenced UDP batch')
    _, _, player_id, sequence = UDP_HEADER.unpack_from(data)
    frames: list[Frame] = []
    offset = UDP_HEADER.size
    while offset < len(data):
        if offset + FRAME_HEADER.size > len(data):
            raise ValueError('Truncated inner frame header')
        size, _ = FRAME_HEADER.unpack_from(data, offset)
        if size < FRAME_HEADER.size or offset + size > len(data):
            raise ValueError('Invalid inner length')
        frames.append(decode_frame(data[offset:offset + size]))
        offset += size
    return Batch(player_id, sequence, tuple(frames))


def encode_udp(opcode: int, player_id: int, body: bytes = b'', sequence: int = 0) -> bytes:
    size = UDP_HEADER.size + len(body)
    if size > 65507:
        raise ValueError('Datagram exceeds the UDP payload limit')
    return UDP_HEADER.pack(size, opcode, player_id, sequence) + body
