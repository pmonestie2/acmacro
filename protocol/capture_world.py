"""Read-only, per-proxy world catalogs and transition evidence from live UDP."""
import ctypes
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import select
import socket
import struct
import sys
import time
from collections import deque

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from protocol.decode_capture import udp, decode_datagram
from protocol.live_tracker import atomic, endpoints
from protocol.world_tracker import WorldTracker, sector_name, sector_name_201a
from protocol.ip_reassembly import IPv4Reassembler

FOLDER = Path(__file__).resolve().parent / 'captures' / 'multi_world'


def contact_positions(catalogs):
    """Position evidence for possible ships/mobs; no inferred hostility.

    Movement packets also describe players and friendly NPCs. Keep the identity,
    epoch and sector together because object IDs can be reused after a jump.
    """
    contacts = []
    for key, world in catalogs.items():
        groups = [(world['epoch'], world['recent'].get('sector'), world['catalog'])]
        groups += [(a['epoch'], a.get('sector'), a['objects']) for a in world['previous_epochs']]
        for epoch, sector, objects in groups:
            for obj in objects:
                if not obj.get('movement_observed') or 'raw_xyz' not in obj:
                    continue
                contacts.append(dict(obj, proxy_session=key, epoch=epoch, sector=sector,
                                     map_xyz=[v / 1000 for v in obj['raw_xyz']],
                                     hostility=obj.get('hostility', 'unknown')))
    return contacts


class ProxyWorld:
    def __init__(self, process):
        self.process = process
        self.world = WorldTracker()
        self.epoch = 0
        self.archives = []
        self.transitions = []
        self.remote = {}

    def observe(self, item, outgoing, now):
        data = item['payload']
        local = item['source'] if outgoing else item['destination']
        remote = item['destination'] if outgoing else item['source']
        previous = self.remote.get(local)
        if previous and previous != remote:
            self.transitions.append({'time': now, 'kind': 'remote_endpoint_changed',
                                     'previous': previous, 'current': remote,
                                     'confirmed_sector_change': False})
        self.remote[local] = remote
        if not outgoing:
            try:
                record = decode_datagram(data)
                frames = record.get('frames', [])
                if record['opcode'] == '0x201a':
                    frames = [dict(opcode='0x201a', body_hex=record['body_hex']), *frames]
            except ValueError:
                frames = []
            for frame in frames:
                if frame['opcode'] not in ('0x0097', '0x201a'):
                    continue
                decoder = sector_name_201a if frame['opcode'] == '0x201a' else sector_name
                sector = decoder(bytes.fromhex(frame.get('body_hex', '')))
                if sector and sector != self.world.sector:
                    previous_sector = self.world.sector
                    self.archives.append({'epoch': self.epoch, 'token': self.world.token,
                        'sector': previous_sector, 'objects': list(self.world.objects.values())})
                    self.transitions.append({'time': now, 'kind': 'sector_identified',
                        'previous': previous_sector, 'current': sector,
                        'confirmed_sector_change': previous_sector is not None})
                    self.epoch += 1
        # Existing decoder treats this field as session/player token. Its
        # change is evidence of a new session, not a decoded sector name.
        if outgoing and len(data) >= 12:
            token = struct.unpack_from('<I', data, 4)[0]
            if self.world.token is not None and token != self.world.token:
                self.archives.append({'epoch': self.epoch, 'token': self.world.token,
                                      'sector': self.world.sector,
                                      'objects': list(self.world.objects.values())})
                self.transitions.append({'time': now, 'kind': 'session_token_changed',
                                         'previous': self.world.token, 'current': token,
                                         'confirmed_sector_change': False})
                self.epoch += 1
        self.world.apply(data, outgoing, now)

    def report(self):
        return {'process': self.process, 'epoch': self.epoch,
                'recent': self.world.snapshot(),
                'catalog': list(self.world.objects.values()),
                'previous_epochs': self.archives, 'transition_evidence': self.transitions,
                'remote_endpoints': self.remote}


def main():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit('Run this read-only capture from an administrator shell.')
    FOLDER.mkdir(parents=True, exist_ok=True)
    # Keep catalogs from earlier client lifetimes when recording is restarted.
    # Raw packet rotation must not erase the accumulated sector/gate evidence.
    catalog_path = FOLDER / 'catalogs.json'
    saved_catalogs = json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
    stop = FOLDER / 'capture.stop'
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, 'Local\\AcMacroMultiWorldCapture')
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        raise SystemExit('Per-proxy world capture is already running.')
    stop.unlink(missing_ok=True)
    worlds, sockets, port_owner, local_ips, logs = {}, {}, {}, set(), {}
    seen = {}
    contact_seen = {}
    last_refresh = last_save = 0
    reassembler = IPv4Reassembler()
    pending_packets = deque(maxlen=4096)
    status = {'state': 'starting', 'pid': __import__('os').getpid(), 'started': time.time()}
    try:
        while not stop.exists():
            now = time.time()
            if now - last_refresh >= 10:
                current = endpoints()
                # A process can exit between endpoint enumeration and reading
                # StartTime. Never invent its lifetime or crash the recorder.
                proxies = {p['Id']: p for p in current['processes']
                           if p['ProcessName'].casefold() == 'net7proxy' and p.get('started')}
                port_owner = {p['LocalPort']: p['OwningProcess'] for p in current['ports']
                              if p['OwningProcess'] in proxies}
                local_ips = set(current['ips'])
                for pid, process in proxies.items():
                    key = f"{pid}_{process['started']}"
                    if key not in worlds:
                        worlds[key] = ProxyWorld(process)
                        # Resume only this process lifetime, including sector
                        # archives. Replaying logs is local decoding, no traffic.
                        process_start = datetime.fromisoformat(process['started'].replace('Z', '+00:00')).timestamp()
                        paths = sorted(FOLDER.glob(f'packets_{pid}.jsonl*'), key=lambda p: p.stat().st_mtime)
                        for path in paths:
                            for line in path.read_text(errors='replace').splitlines():
                                try:
                                    row = json.loads(line)
                                    if row['time'] < process_start:
                                        continue
                                    item = {field: row[field] for field in ('source', 'destination')}
                                    item['payload'] = bytes.fromhex(row['payload_hex'])
                                    worlds[key].observe(item, row['outgoing'], row['time'])
                                except (ValueError, KeyError):
                                    continue
                        logger = logging.getLogger('world_capture_' + key)
                        logger.setLevel(logging.INFO)
                        handler = RotatingFileHandler(FOLDER / f'packets_{pid}.jsonl',
                                                      maxBytes=10_000_000, backupCount=3)
                        handler.setFormatter(logging.Formatter('%(message)s'))
                        logger.addHandler(handler)
                        logs[key] = logger
                status['active_proxies'] = proxies
                status['ports'] = port_owner
                last_refresh = now
                for address in local_ips - sockets.keys():
                    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
                    sock.bind((address, 0))
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
                    sock.ioctl(socket.SIO_RCVALL, 3)
                    sock.setblocking(False)
                    sockets[address] = sock
                for address in sockets.keys() - local_ips:
                    sockets.pop(address).close()
            ready = select.select(list(sockets.values()), [], [], .2)[0] if sockets else []
            if not sockets:
                time.sleep(.2)
            for sock in ready:
                packet = reassembler.feed(sock.recv(65535))
                if packet is None:
                    continue
                item = udp(bytes(12) + b'\x08\x00' + packet)
                if not item:
                    continue
                pending_packets.append((time.time(),item))
            retained = deque(maxlen=4096)
            for stamp, item in pending_packets:
                source, sport = item['source'].rsplit(':', 1)
                destination, dport = item['destination'].rsplit(':', 1)
                outgoing = source in local_ips and int(sport) in port_owner
                incoming = destination in local_ips and int(dport) in port_owner
                if not (outgoing or incoming):
                    # A new UDP socket can send its complete initial map before
                    # the next endpoint refresh. Keep a bounded 20-second window
                    # so those packets are attributed when ownership is known.
                    if time.time()-stamp < 20 and '216.219.87.147' in (source,destination):
                        retained.append((stamp,item))
                    continue
                pid = port_owner[int(sport if outgoing else dport)]
                process = proxies[pid]
                key = f"{pid}_{process['started']}"
                if stamp < datetime.fromisoformat(process['started'].replace('Z','+00:00')).timestamp():
                    continue  # A newly reused port must not inherit old packets.
                duplicate = (key, item['source'], item['destination'], item['payload'])
                previous = seen.get(duplicate, 0)
                seen[duplicate] = stamp
                if stamp - previous < .05:
                    continue
                if len(seen) > 4096:
                    seen = {k: v for k, v in seen.items() if stamp - v < 2}
                worlds[key].observe(item, outgoing, stamp)
                logs[key].info(json.dumps({'time': stamp, 'epoch': worlds[key].epoch,
                    'source': item['source'], 'destination': item['destination'],
                    'outgoing': outgoing, 'payload_hex': item['payload'].hex()}))
            pending_packets = retained
            if time.time() - last_save >= 2:
                status.update(state='running', updated=time.time(),
                    ip_reassembly={'completed':reassembler.completed,'rejected':reassembler.rejected,
                                   'expired':reassembler.expired,'pending':len(reassembler.pending)},
                    catalogs={key: {'objects': len(world.world.objects),
                        'named': sum(bool(o.get('name')) for o in world.world.objects.values()),
                        'datagrams': world.world.datagrams, 'epochs': world.epoch + 1}
                        for key, world in worlds.items()})
                saved_catalogs.update({key: world.report() for key, world in worlds.items()})
                atomic(catalog_path, saved_catalogs)
                contacts = contact_positions(saved_catalogs)
                atomic(FOLDER / 'contacts.json', contacts)
                with (FOLDER / 'contact_positions.jsonl').open('a', encoding='utf-8') as history:
                    for contact in contacts:
                        identity = (contact['proxy_session'], contact['epoch'], contact['id'])
                        stamp = contact.get('position_seen')
                        if contact_seen.get(identity) != stamp:
                            history.write(json.dumps(contact) + '\n')
                            contact_seen[identity] = stamp
                atomic(FOLDER / 'status.json', status)
                last_save = time.time()
    except BaseException as error:
        status.update(state='error', error=str(error), updated=time.time())
        atomic(FOLDER / 'status.json', status)
        raise
    else:
        status.update(state='stopped', updated=time.time())
        atomic(FOLDER / 'status.json', status)
    finally:
        for sock in sockets.values():
            sock.close()
        kernel.CloseHandle(handle)


if __name__ == '__main__':
    main()
