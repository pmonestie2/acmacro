"""Bounded passive UDP capture; job-terminal frames by default, or all frames."""
import argparse
import json
from pathlib import Path
import select
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from focus import process_identity
from protocol.decode_capture import decode_datagram, udp
from protocol.game_packet_monitor import udp_ports
from protocol.ip_reassembly import IPv4Reassembler


def write_status(path: Path, status: dict[str, object]) -> bool:
    """Publish atomically, tolerating Windows readers briefly locking the file."""
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(status, indent=2))
    for attempt in range(4):
        try:
            temporary.replace(path)
            return True
        except PermissionError:
            if attempt == 3:
                return False  # Keep capturing; the next refresh tries again.
            time.sleep(.01)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proxy', type=int, required=True)
    parser.add_argument('--seconds', type=float, default=360)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--all-packets', action='store_true', help='Retain all decoded frames, including vendor and trade traffic')
    args = parser.parse_args()
    born = process_identity(args.proxy)
    if born is None:
        raise SystemExit('Proxy is not running')
    addresses = {str(row[4][0]) for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    sockets = []
    reassembler = IPv4Reassembler()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    status_path = args.output.with_suffix('.status.json')
    status = dict(proxy=args.proxy, born=born, started=time.time(), packets=0, frames={}, state='running')
    def save() -> None:
        write_status(status_path, dict(status, updated=time.time()))
    deadline = time.monotonic() + args.seconds
    ports: set[int] = set()
    refreshed = 0.0
    try:
        for address in addresses:
            if address.startswith('127.'):
                continue
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
            sock.bind((address, 0))
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            sock.ioctl(socket.SIO_RCVALL, 3)
            sock.setblocking(False)
            sockets.append(sock)
        if not sockets:
            raise RuntimeError('No capture interface')
        with args.output.open('a', encoding='utf-8') as output:
            save()
            while time.monotonic() < deadline:
                now = time.time()
                if now - refreshed >= .5:
                    if process_identity(args.proxy) != born:
                        status['state'] = 'proxy exited or changed'
                        break
                    ports, refreshed = udp_ports(args.proxy), now
                    status['ports'] = sorted(ports)
                    save()
                for sock in select.select(sockets, [], [], .1)[0]:
                    packet = reassembler.feed(sock.recv(65535))
                    item = udp(bytes(12) + b'\x08\x00' + packet) if packet else None
                    if not item:
                        continue
                    source, sport = item['source'].rsplit(':', 1)
                    destination, dport = item['destination'].rsplit(':', 1)
                    outgoing = source in addresses and int(sport) in ports
                    if not outgoing and not (destination in addresses and int(dport) in ports):
                        continue
                    decode_error: str | None = None
                    try:
                        decoded = decode_datagram(item['payload'])
                    except ValueError as error:
                        if not args.all_packets:
                            continue
                        decode_error = str(error)
                        decoded = {'frames': []}
                    relevant = []
                    for frame in decoded.get('frames', [decoded]):
                        opcode = frame['opcode']
                        if args.all_packets or opcode in ('0x004e', '0x0086', '0x0087', '0x0093', '0x0094', '0x0095', '0x0096'):
                            relevant.append(frame)
                        elif opcode == '0x001b' and frame.get('body_hex', '').startswith('00000000'):
                            relevant.append(frame)
                        elif opcode == '0x00a5' and any(word in frame.get('chat', {}).get('message', '').lower()
                                                     for word in ('job', 'mission', 'cargo', 'completed')):
                            relevant.append(frame)
                    if not relevant and decode_error is None:
                        continue
                    row = dict(time=now, proxy=args.proxy, born=born, outgoing=outgoing,
                               source=item['source'], destination=item['destination'],
                               payload_hex=item['payload'].hex(), frames=relevant)
                    if decode_error is not None:
                        row['decode_error'] = decode_error
                    output.write(json.dumps(row) + '\n')
                    output.flush()
                    status['packets'] += 1
                    for frame in relevant:
                        key = ('out ' if outgoing else 'in ') + frame['opcode']
                        status['frames'][key] = status['frames'].get(key, 0) + 1
    except BaseException as error:
        status.update(state='error', error=str(error))
        raise
    finally:
        for sock in sockets:
            sock.close()
        if status['state'] == 'running':
            status['state'] = 'completed'
        save()


if __name__ == '__main__':
    main()
