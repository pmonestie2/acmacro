"""Decode observed Net-7 IPv4 UDP frames from an Ethernet pcapng capture."""
import argparse
from collections import Counter
import json
from pathlib import Path
import socket
import struct
from typing import TypedDict

from enb_protocol.wire import decode_batch, decode_frame


class ChatFields(TypedDict):
    event: int
    unknown_u32: int
    name_1: str
    name_2: str
    string_3: str
    channel: str
    message: str
    trailer_hex: str


class DecodedFrame(TypedDict, total=False):
    length: int
    opcode: str
    body_hex: str
    chat: ChatFields


class DecodedDatagram(DecodedFrame, total=False):
    session_or_token: str
    sequence: int
    frames: list[DecodedFrame]


def packets(path):
    """Read little-endian pcapng as produced by this machine's pktmon."""
    interfaces = []
    with Path(path).open('rb') as stream:
        while header := stream.read(8):
            if len(header) != 8:
                raise ValueError('Truncated pcapng block header')
            kind, size = struct.unpack('<II', header)
            if size < 12 or size % 4 or size > 128 * 1024 * 1024:
                raise ValueError('Unsupported byte order or invalid pcapng block size')
            rest = stream.read(size - 8)
            if len(rest) != size - 8 or struct.unpack_from('<I', rest, len(rest)-4)[0] != size:
                raise ValueError('Truncated or invalid pcapng block')
            if kind == 0x0a0d0d0a:
                if rest[:4] != bytes.fromhex('4d3c2b1a'):
                    raise ValueError('Only little-endian pcapng is supported')
                interfaces = []
            elif kind == 1:
                if len(rest) < 12:
                    raise ValueError('Invalid interface block')
                interfaces.append(struct.unpack_from('<H', rest)[0])
            elif kind == 6:
                if len(rest) < 24:
                    raise ValueError('Invalid packet block')
                interface, high, low, captured, original = struct.unpack_from('<IIIII', rest)
                if interface >= len(interfaces) or 20 + captured > len(rest) - 4:
                    raise ValueError('Invalid packet length or interface')
                if interfaces[interface] == 1:
                    yield rest[20:20+captured]


def udp(packet):
    if len(packet) < 34 or packet[12:14] != b'\x08\x00':
        return None
    ip = packet[14:]
    header_size = (ip[0] & 15) * 4
    total, fragment = struct.unpack_from('!HH', ip, 2)[0], struct.unpack_from('!H', ip, 6)[0]
    if ip[0] >> 4 != 4 or header_size < 20 or total > len(ip) or total < header_size + 8:
        return None
    if ip[9] != 17 or fragment & 0x3fff:
        return None  # Fragment reassembly is deliberately not inferred.
    sport, dport, length = struct.unpack_from('!HHH', ip, header_size)
    if length < 8 or header_size + length > total:
        return None
    return {'source': f'{socket.inet_ntoa(ip[12:16])}:{sport}',
            'destination': f'{socket.inet_ntoa(ip[16:20])}:{dport}',
            'payload': ip[header_size+8:header_size+length]}


def chat_fields(body: bytes) -> ChatFields:
    if len(body) < 8:
        raise ValueError('Truncated 0x00a5 prefix')
    event, unknown = struct.unpack_from('<II', body)
    offset = 8
    fields = []
    for _ in range(5):
        if offset + 2 > len(body):
            raise ValueError('Truncated chat string length')
        length = struct.unpack_from('<H', body, offset)[0]
        offset += 2
        if offset + length > len(body):
            raise ValueError('Truncated chat string')
        raw = body[offset:offset+length]
        # Captured samples are ASCII. Escapes preserve unverified high-byte encoding.
        fields.append(raw.decode('ascii', errors='backslashreplace'))
        offset += length
    return {'event': event, 'unknown_u32': unknown,
            'name_1': fields[0], 'name_2': fields[1], 'string_3': fields[2],
            'channel': fields[3], 'message': fields[4], 'trailer_hex': body[offset:].hex()}


def decode_datagram(payload: bytes) -> DecodedDatagram:
    outer = decode_frame(payload)
    result: DecodedDatagram = {'length': len(payload), 'opcode': f'0x{outer.opcode:04x}'}
    if outer.opcode != 0x2016:
        result['body_hex'] = outer.body.hex()
        return result
    batch = decode_batch(payload)
    result['session_or_token'] = f'0x{batch.player_id:08x}'
    result['sequence'] = batch.sequence
    result['frames'] = []
    for inner in batch.frames:
        frame: DecodedFrame = {'length': len(inner.body) + 4, 'opcode': f'0x{inner.opcode:04x}'}
        if inner.opcode == 0xa5:
            frame['chat'] = chat_fields(inner.body)
        else:
            frame['body_hex'] = inner.body.hex()
        result['frames'].append(frame)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records = {}
    errors = Counter()
    packet_count = 0
    try:
        for packet in packets(args.capture):
            packet_count += 1
            item = udp(packet)
            if item is None:
                continue
            key = item['source'], item['destination'], item['payload']
            if key in records:
                records[key]['observations'] += 1
                continue
            try:
                decoded = decode_datagram(item['payload'])
            except ValueError as error:
                errors[str(error)] += 1
                continue
            records[key] = dict(source=item['source'], destination=item['destination'], observations=1, **decoded)
        args.output.write_text(''.join(json.dumps(record) + '\n' for record in records.values()), encoding='utf-8')
        chats = [frame['chat'] for record in records.values() for frame in record.get('frames', []) if 'chat' in frame]
        print(json.dumps({'captured_packets': packet_count, 'distinct_decoded_datagrams': len(records),
                          'chat_events': len(chats), 'nonempty_messages': sum(bool(c['message']) for c in chats),
                          'errors': dict(errors), 'output': str(args.output)}))
    except (OSError, ValueError) as error:
        parser.exit(1, f'Decode failed: {error}\n')


if __name__ == '__main__':
    main()
