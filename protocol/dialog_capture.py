"""Preserve NPC text and choices from the existing passive per-proxy capture."""
import json
from pathlib import Path
import struct

from protocol.decode_capture import decode_datagram


def decode_talk_tree(body):
    end = body.index(0)
    text = body[:end].decode('cp1252')
    if end + 2 >= len(body):
        raise ValueError('Truncated talk-tree header')
    count = body[end + 2]
    offset = end + 3
    replies = []
    for _ in range(count):
        if offset + 4 > len(body):
            raise ValueError('Truncated talk-tree branch')
        destination = struct.unpack_from('<I', body, offset)[0]
        offset += 4
        end = body.index(0, offset)
        replies.append({'selection':destination, 'text':body[offset:end].decode('cp1252')})
        offset = end + 1
    return {'text':text, 'replies':replies}


def preserve(proxy_pid, destination, since=0):
    folder = Path(__file__).resolve().parent / 'captures' / 'multi_world'
    output = Path(destination)
    saved = json.loads(output.read_text()) if output.exists() else []
    known = {(x['time'], x['opcode'], x['body_hex']) for x in saved}
    for path in sorted(folder.glob(f'packets_{proxy_pid}.jsonl*'), key=lambda p:p.stat().st_mtime):
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if row['time'] < since:
                        continue
                    packet = decode_datagram(bytes.fromhex(row['payload_hex']))
                    for frame in packet.get('frames', [packet]):
                        opcode = frame['opcode']
                        if opcode not in ('0x0054', '0x0055', '0x0056', '0x004e'):
                            continue
                        body = bytes.fromhex(frame['body_hex'])
                        # Direct outbound datagrams include owner and sequence
                        # before their normal application body.
                        if row['outgoing']:
                            body = body[8:]
                        event = {'time':row['time'], 'epoch':row['epoch'], 'opcode':opcode,
                                 'outgoing':row['outgoing'], 'body_hex':body.hex()}
                        identity = (event['time'], opcode, event['body_hex'])
                        if identity in known:
                            continue
                        if opcode == '0x0054':
                            event.update(decode_talk_tree(body))
                        elif opcode == '0x004e' and len(body) == 9:
                            player, npc, action = struct.unpack('<IIB', body)
                            event.update(player_id=hex(player), npc_id=npc, action=action)
                        elif opcode == '0x0055' and len(body) == 5:
                            player, choice = struct.unpack('<IB', body)
                            event.update(player_id=hex(player), selection=choice)
                        saved.append(event)
                        known.add(identity)
                except (ValueError, KeyError, struct.error):
                    continue
    saved.sort(key=lambda x:x['time'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(saved, indent=2), encoding='utf-8')
    return saved


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('proxy_pid', type=int)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--since', type=float, default=0)
    args = parser.parse_args()
    events = preserve(args.proxy_pid, args.destination, args.since)
    for event in events[-4:]:
        print(json.dumps(event, ensure_ascii=False))
