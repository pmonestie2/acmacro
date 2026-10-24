"""Bridge legacy localhost galaxy redirects to one private proxy interface.

The old character-list format advertises 127.0.0.1 for the dynamic master port.
Game UDP remains direct: these bounded TCP relays cover only login/fallback links.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import socket
import threading
import time


def proxy_listener_ports(pid: int, address: str) -> list[int]:
    """Query this owned proxy's listeners without WMI or polling packet data."""
    class Row(ctypes.Structure):
        _fields_ = [('state',wintypes.DWORD),('address',wintypes.DWORD),('port',wintypes.DWORD),
                    ('remote_address',wintypes.DWORD),('remote_port',wintypes.DWORD),('pid',wintypes.DWORD)]
    api = ctypes.WinDLL('iphlpapi', use_last_error=True).GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                    wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.restype = wintypes.DWORD
    size = wintypes.DWORD()
    result = api(None, ctypes.byref(size), False, socket.AF_INET, 3, 0)
    if result not in (0,122): raise OSError(result,'Cannot enumerate proxy listeners')
    buffer = ctypes.create_string_buffer(size.value)
    result = api(buffer, ctypes.byref(size), False, socket.AF_INET, 3, 0)
    if result: raise OSError(result,'Cannot enumerate proxy listeners')
    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))[0]
    rows = (Row * count).from_buffer(buffer, ctypes.sizeof(wintypes.DWORD))
    return sorted(socket.ntohs(row.port & 65535) for row in rows if row.pid == pid
        and socket.inet_ntoa(int(row.address).to_bytes(4,'little')) == address
        and socket.ntohs(row.port & 65535) not in (443,3805))


class RelayConnection:
    def __init__(self, client: socket.socket, target: socket.socket) -> None:
        self.sockets = (client,target)
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.remaining = 2
        self.threads = [threading.Thread(target=self.pump,args=(client,target),daemon=True),
                        threading.Thread(target=self.pump,args=(target,client),daemon=True)]

    def start(self) -> None:
        for channel in self.sockets: channel.settimeout(.5)
        for thread in self.threads: thread.start()

    def pump(self, source: socket.socket, target: socket.socket) -> None:
        try:
            while not self.stopped.is_set():
                try: data = source.recv(65536)
                except socket.timeout: continue
                if not data: break
                target.sendall(data)
        except OSError:
            self.stopped.set()
        finally:
            try: target.shutdown(socket.SHUT_WR)
            except OSError: pass
            with self.lock:
                self.remaining -= 1
                if self.remaining == 0:
                    for channel in self.sockets: channel.close()

    def alive(self) -> bool:
        return any(thread.is_alive() for thread in self.threads)

    def close(self) -> None:
        self.stopped.set()
        for channel in self.sockets:
            try: channel.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            channel.close()
        for thread in self.threads:
            if thread.ident is not None: thread.join(timeout=2)


class LoopbackRelay:
    def __init__(self, address: str, port: int, *, target_port: int | None = None) -> None:
        if not address.startswith('127.'): raise ValueError('Only loopback relay targets are supported')
        self.address = address
        self.port = port
        self.target_port = port if target_port is None else target_port
        self.listener = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.accept,daemon=True)
        self.connections: list[RelayConnection] = []
        self.lock = threading.Lock()

    def start(self) -> None:
        self.listener.bind(('127.0.0.1',self.port))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(8)
        self.listener.settimeout(.25)
        self.thread.start()

    def accept(self) -> None:
        while not self.stopped.is_set():
            try: client,_ = self.listener.accept()
            except socket.timeout: continue
            except OSError: break
            try:
                with self.lock:
                    self.connections = [connection for connection in self.connections if connection.alive()]
                    if len(self.connections) >= 16:
                        client.close()
                        continue
                target = socket.create_connection((self.address,self.target_port),timeout=2)
                connection = RelayConnection(client,target)
                with self.lock:
                    if self.stopped.is_set():
                        client.close()
                        target.close()
                        break
                    connection.start()
                    self.connections.append(connection)
            except OSError:
                client.close()

    def close(self) -> None:
        self.stopped.set()
        self.listener.close()
        if self.thread.ident is not None: self.thread.join(timeout=3)
        with self.lock: connections = list(self.connections)
        for connection in connections: connection.close()


class ProfileRelays:
    def __init__(self, pid: int, address: str) -> None:
        self.pid = pid
        self.address = address
        self.relays: list[LoopbackRelay] = []

    def start(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ports = proxy_listener_ports(self.pid,self.address)
            if len(ports) == 2: break
            time.sleep(.1)
        else: raise TimeoutError('Private proxy master/sector listeners did not appear')
        try:
            for port in ports:
                relay = LoopbackRelay(self.address,port)
                self.relays.append(relay)
                relay.start()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        for relay in self.relays: relay.close()
        self.relays.clear()
