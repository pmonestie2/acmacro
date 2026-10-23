"""Offline pcapng/JSONL ingestion and bounded IPv4 fragment reassembly."""
from collections.abc import Iterator
from dataclasses import dataclass, field
import json
from pathlib import Path
import socket
import struct


def captured_ipv4(path: Path) -> Iterator[bytes]:
    if path.suffix == '.jsonl':
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                record = json.loads(line)
                yield bytes.fromhex(record['packet_hex'])
        return
    interfaces: list[int] = []
    with path.open('rb') as stream:
        while header := stream.read(8):
            if len(header) != 8:
                raise ValueError('Truncated pcapng header')
            kind, size = struct.unpack('<II', header)
            if size < 12 or size % 4 or size > 16 * 1024 * 1024:
                raise ValueError('Unsupported pcapng block')
            body = stream.read(size - 8)
            if len(body) != size - 8 or struct.unpack_from('<I', body, len(body) - 4)[0] != size:
                raise ValueError('Truncated pcapng block')
            if kind == 0x0a0d0d0a:
                if body[:4] != bytes.fromhex('4d3c2b1a'):
                    raise ValueError('Only little-endian pcapng is supported')
                interfaces.clear()
            elif kind == 1:
                if len(body) < 12:
                    raise ValueError('Truncated interface description')
                interfaces.append(struct.unpack_from('<H', body)[0])
            elif kind == 6:
                if len(body) < 24:
                    raise ValueError('Truncated packet block')
                interface, _, _, length, _ = struct.unpack_from('<IIIII', body)
                if interface >= len(interfaces) or length > len(body) - 24:
                    raise ValueError('Invalid captured packet length/interface')
                packet = body[20:20 + length]
                if interfaces[interface] == 1 and len(packet) >= 34 and packet[12:14] == b'\x08\x00':
                    yield packet[14:]


@dataclass
class Fragments:
    chunks: dict[int, bytes] = field(default_factory=dict)
    header: bytes | None = None
    length: int | None = None


class IPv4Reassembler:
    def __init__(self) -> None:
        self.pending: dict[tuple[bytes, bytes, int, int], Fragments] = {}

    def feed(self, packet: bytes) -> bytes | None:
        if len(packet) < 20 or packet[0] >> 4 != 4:
            return None
        header_size = (packet[0] & 15) * 4
        total, identity, flags = struct.unpack_from('!HHH', packet, 2)
        if header_size < 20 or not header_size <= total <= len(packet):
            return None
        packet = packet[:total]
        if not flags & 0x3fff:
            return packet
        offset = (flags & 0x1fff) * 8
        more = bool(flags & 0x2000)
        payload = packet[header_size:]
        key = (packet[12:16], packet[16:20], packet[9], identity)
        if not payload or offset + len(payload) > 65515 or more and len(payload) % 8:
            self.pending.pop(key, None)
            return None
        if key not in self.pending:
            if len(self.pending) >= 256:
                self.pending.pop(next(iter(self.pending)))
            self.pending[key] = Fragments()
        item = self.pending[key]
        for start, old in item.chunks.items():
            if start == offset and old == payload:
                return None
            if max(start, offset) < min(start + len(old), offset + len(payload)):
                self.pending.pop(key)
                return None
        item.chunks[offset] = payload
        if offset == 0:
            item.header = packet[:header_size]
        if not more:
            item.length = offset + len(payload)
        if item.header is None or item.length is None:
            return None
        assembled = bytearray()
        for start, chunk in sorted(item.chunks.items()):
            if start != len(assembled):
                return None
            assembled.extend(chunk)
        if len(assembled) != item.length:
            return None
        self.pending.pop(key)
        result = bytearray(item.header + assembled)
        if len(result) > 65535:
            return None
        struct.pack_into('!H', result, 2, len(result))
        struct.pack_into('!H', result, 6, 0)
        return bytes(result)


def server_datagrams(path: Path, server: str) -> Iterator[bytes]:
    reassembler = IPv4Reassembler()
    for packet in captured_ipv4(path):
        full = reassembler.feed(packet)
        if full is None or full[9] != 17 or socket.inet_ntoa(full[12:16]) != server:
            continue
        offset = (full[0] & 15) * 4
        if len(full) < offset + 8:
            continue
        length = struct.unpack_from('!H', full, offset + 4)[0]
        if 8 <= length <= len(full) - offset:
            yield full[offset + 8:offset + length]
