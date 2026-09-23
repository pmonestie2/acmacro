"""Extract length-checked decrypted client frames from the proxy's diagnostic log."""
import argparse
import json
from pathlib import Path
import re
import struct


def extract(text):
    frames = []
    mode = None
    pending = bytearray()
    expected = None
    header = None
    encrypted_header = None
    timestamp = None
    for line in text.splitlines():
        match = re.match(r'(\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) (.*)', line)
        if not match:
            continue
        when, message = match.groups()
        if message.startswith('Received 4 byte header ('):
            if mode is not None:
                raise ValueError('Interrupted header or body dump')
            mode = 'encrypted' if '(encrypted)' in message else 'decrypted'
            pending = bytearray()
            expected = 4
            timestamp = when
        elif packet := re.fullmatch(r'Received (\d+) byte packet', message):
            if header is None:
                continue
            expected = int(packet[1])
            if expected != struct.unpack_from('<H', header)[0] - 4:
                raise ValueError('Body size disagrees with decrypted frame header')
            mode = 'body'
            pending = bytearray()
        elif mode and re.fullmatch(r'(?:[0-9A-Fa-f]{2}\s*)+', message):
            pending.extend(bytes.fromhex(message))
        else:
            continue
        if mode and len(pending) > expected:
            raise ValueError('Hex dump exceeds expected length')
        if mode and len(pending) == expected:
            if mode == 'encrypted':
                encrypted_header = bytes(pending)
            elif mode == 'decrypted':
                header = bytes(pending)
                if struct.unpack_from('<H', header)[0] < 4:
                    raise ValueError('Invalid decrypted packet length')
            else:
                length, opcode = struct.unpack('<HH', header)
                record = {'time': timestamp, 'length': length, 'opcode': f'0x{opcode:04x}',
                          'frame_hex': (header + pending).hex(),
                          'encrypted_header_hex': encrypted_header.hex() if encrypted_header else None}
                if opcode == 0x9b:
                    record['route'] = decode_route(pending)
                frames.append(record)
                header = encrypted_header = None
            mode = None
    if mode is not None or header is not None:
        raise ValueError('Trace ends with an incomplete frame')
    return frames


def decode_route(body):
    if len(body) < 6:
        raise ValueError('Truncated route prefix')
    entity, count = struct.unpack_from('<IH', body)
    if len(body) != 6 + count * 4:
        raise ValueError('Route count disagrees with payload size')
    ids = struct.unpack_from('<' + 'I' * count, body, 6)
    return {'entity_or_token': f'0x{entity:08x}', 'waypoint_ids': [f'0x{x:08x}' for x in ids]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        frames = extract(args.log.read_text())
        args.output.write_text(''.join(json.dumps(frame) + '\n' for frame in frames))
        print(json.dumps({'frames': len(frames), 'route_frames': sum('route' in f for f in frames), 'output': str(args.output)}))
    except (OSError, ValueError) as error:
        parser.exit(1, f'Extraction failed: {error}\n')


if __name__ == '__main__':
    main()
