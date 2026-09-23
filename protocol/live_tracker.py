"""Continuous Windows raw-IP observation, filtered to current game UDP endpoints."""
import argparse
import ctypes
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import select
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from protocol.decode_capture import packets, udp
from protocol.world_tracker import WorldTracker

FOLDER = Path(__file__).resolve().parent / 'captures'
STOP = FOLDER / 'tracker.stop'
ENDPOINT_SCRIPT = r'''
$p = @(Get-Process client,net7proxy -ErrorAction SilentlyContinue)
$ids = @($p.Id)
$u = @(Get-NetUDPEndpoint -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -in $ids } | Select-Object LocalPort,OwningProcess)
$ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -ExpandProperty IPAddress)
@{ports=$u;ips=$ips;processes=@($p | Select-Object Id,ProcessName,@{N='started';E={$_.StartTime.ToUniversalTime().ToString('o')}})} | ConvertTo-Json -Depth 4 -Compress
'''


def atomic(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    # Readers on Windows can briefly prevent replacement of an open file.
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def endpoints():
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', ENDPOINT_SCRIPT],
                            capture_output=True, text=True, timeout=20, creationflags=0x08000000)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    data = json.loads(result.stdout)
    data['processes'] = sorted(data['processes'], key=lambda x:x['Id'])
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=Path, action='append', default=[])
    args = parser.parse_args()
    FOLDER.mkdir(exist_ok=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel.CreateMutexW(None, False, 'Local\\AcMacroWorldTracker')
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(mutex)
        raise SystemExit('A tracker is already running')
    logger = logging.getLogger('tracker')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(FOLDER / 'tracker.log', maxBytes=2_000_000, backupCount=2)
    logger.addHandler(handler)
    packet_logger = logging.getLogger('tracker_packets')
    packet_logger.setLevel(logging.INFO)
    packet_logger.addHandler(RotatingFileHandler(FOLDER / 'packet_stream.jsonl', maxBytes=10_000_000, backupCount=3))
    sockets = {}
    state = WorldTracker()
    status = {'state': 'starting', 'pid': __import__('os').getpid(), 'backend': 'Windows raw IPv4 sockets'}
    updates = queue.Queue()
    done = threading.Event()
    def refresh():
        while not done.is_set():
            try:
                updates.put(endpoints())
            except Exception as error:
                logger.warning('Endpoint refresh failed: %s', error)
            done.wait(10)
    try:
        STOP.unlink(missing_ok=True)
        previous_status = json.loads((FOLDER / 'tracker_status.json').read_text()) if (FOLDER / 'tracker_status.json').exists() else {}
        atomic(FOLDER / 'tracker_status.json', status)
        current = endpoints()
        local_ips = set(current['ips'])
        ports = {x['LocalPort'] for x in current['ports']}
        process_identity = current['processes']
        session_start = max((datetime.fromisoformat(x['started'].replace('Z', '+00:00')).timestamp() for x in process_identity), default=time.time())
        status['processes'] = process_identity
        previous_state = FOLDER / 'tracker_cache.json'
        if not previous_state.exists():
            previous_state = FOLDER / 'world_state.json'
        if previous_status.get('processes') == process_identity and previous_state.exists():
            saved = json.loads(previous_state.read_text())
            state.objects = {x['id']:x for x in saved['objects'] if x['last_seen'] >= session_start}
            state.player = saved['player'] if saved['player'] and saved['player']['last_seen'] >= session_start else None
            state.token = saved.get('token')
            state.datagrams = saved['datagrams']
            state.started = saved['started']
        # Seeds remain historical until a new live observation confirms them.
        for path in args.seed:
            if path.stat().st_mtime < session_start:
                logger.info('Skipped capture from before current process session: %s', path.name)
                continue
            for packet in packets(path):
                item = udp(packet)
                if item:
                    outgoing = item['source'].rsplit(':', 1)[0] in local_ips
                    state.apply(item['payload'], outgoing, path.stat().st_mtime, historical=True)
        threading.Thread(target=refresh, daemon=True).start()
        last_save = 0
        seen = {}
        while not STOP.exists():
            try:
                current = updates.get_nowait()
                if current['processes'] != process_identity:
                    state = WorldTracker()
                    process_identity = current['processes']
                    status['processes'] = process_identity
                    seen.clear()
                    logger.info('Process/session changed; observations reset')
                local_ips = set(current['ips'])
                ports = {x['LocalPort'] for x in current['ports']}
            except queue.Empty:
                pass
            for address in local_ips - sockets.keys():
                sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
                sock.bind((address, 0))
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
                # Windows RCVALL_IPLEVEL: host IP traffic, without NIC promiscuous mode.
                sock.ioctl(socket.SIO_RCVALL, 3)
                sock.setblocking(False)
                sockets[address] = sock
            for address in sockets.keys() - local_ips:
                sockets.pop(address).close()
            readable = select.select(list(sockets.values()), [], [], 0.2)[0] if sockets else []
            if not sockets:
                done.wait(0.2)
            for sock in readable:
                raw = sock.recv(65535)
                item = udp(bytes(12) + b'\x08\x00' + raw)
                if not item:
                    continue
                source_ip, source_port = item['source'].rsplit(':', 1)
                destination_ip, destination_port = item['destination'].rsplit(':', 1)
                outgoing = source_ip in local_ips and int(source_port) in ports
                incoming = destination_ip in local_ips and int(destination_port) in ports
                if not (incoming or outgoing):
                    continue
                now = time.time()
                key = (item['source'], item['destination'], item['payload'])
                previous = seen.get(key, 0)
                seen[key] = now
                if now - previous < 0.05:
                    continue
                if len(seen) > 4096:
                    seen = {k:v for k,v in seen.items() if now-v < 2}
                state.apply(item['payload'], outgoing, now)
                packet_logger.info(json.dumps({'time': now, 'source': item['source'], 'destination': item['destination'],
                                              'outgoing': outgoing, 'payload_hex': item['payload'].hex()}))
            if time.time() - last_save >= 1:
                last_save = time.time()
                evidence_path = Path(__file__).resolve().parent / 'level_evidence.json'
                if evidence_path.exists():
                    try:
                        state.level_evidence = json.loads(evidence_path.read_text())
                    except (ValueError, OSError):
                        logger.warning('Could not read level evidence; retaining previous values')
                state.process_identity = process_identity
                snapshot = state.snapshot(last_save)
                status.update(state='running', updated=last_save, ports=sorted(ports), interfaces=sorted(sockets),
                              datagrams=snapshot['datagrams'], counts=snapshot['counts'])
                atomic(FOLDER / 'world_state.json', snapshot)
                atomic(FOLDER / 'tracker_cache.json', dict(snapshot, objects=list(state.objects.values())))
                atomic(FOLDER / 'tracker_status.json', status)
        status['state'] = 'stopped'
    except Exception as error:
        logger.exception('Tracker failed')
        status.update(state='failed', error=str(error))
    finally:
        done.set()
        for sock in sockets.values():
            sock.close()
        status['updated'] = time.time()
        atomic(FOLDER / 'world_state.json', state.snapshot())
        atomic(FOLDER / 'tracker_cache.json', dict(state.snapshot(), objects=list(state.objects.values())))
        atomic(FOLDER / 'tracker_status.json', status)
        kernel.CloseHandle(mutex)


if __name__ == '__main__':
    main()
