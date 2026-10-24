"""Independent native client instances sharing only immutable installed assets.

Each owns its writable configuration, proxy files, loopback listeners and job.
Closing one launcher cannot close another client's process tree or undo its INIs.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from build import ROOT
from client_config import local_configuration

CLIENT_SHA256 = '249f852496f341c00361be95b66e7c0becc2c39e7cba026f9227e38b602651c4'


def isolate_client_mutex(client: Path, original: Path, name: str) -> None:
    """Give a private, known client build its own singleton identity.

    The installed executable is read-only. Only the 14-byte CreateMutexA name
    changes in its private copy; executable instructions remain identical.
    Refuse an unknown build or a private executable edited by another program.
    """
    if os.path.samefile(client, original):
        raise RuntimeError('Client instance executable must be an independent copy')
    source = original.read_bytes()
    marker = b'enb_mutex_lock\0'
    if hashlib.sha256(source).hexdigest() != CLIENT_SHA256 or source.count(marker) != 1:
        raise RuntimeError('Unsupported client build; no instance executable changed')
    identity = b'enb_' + hashlib.sha256(name.lower().encode('ascii')).hexdigest()[:10].encode('ascii') + b'\0'
    applied = source.replace(marker, identity)
    current = client.read_bytes()
    if current == applied:
        return
    if current != source:
        raise RuntimeError('Private client executable changed; refusing to overwrite it')
    client.write_bytes(applied)


@dataclass(frozen=True)
class ClientProfile:
    name: str
    address: str
    directory: Path

    @property
    def client(self) -> Path: return self.directory / 'game/release/client.exe'

    @property
    def proxy(self) -> Path: return self.directory / 'proxy/net7proxy.exe'

    @property
    def hostname(self) -> str: return f'{self.name}.local.net-7.org'


@contextmanager
def named_mutex(name: str, timeout: int = 15000) -> Iterator[None]:
    import win32api
    import win32event
    handle = win32event.CreateMutex(None, False, name)
    acquired = False
    try:
        result = win32event.WaitForSingleObject(handle, timeout)
        acquired = result in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED)
        if not acquired: raise RuntimeError('This client profile is already running or another launch is preparing it')
        yield
    finally:
        if acquired: win32event.ReleaseMutex(handle)
        win32api.CloseHandle(handle)


def copy_client(source: Path, destination: Path) -> None:
    """Hard-link immutable game assets; copy writable settings and executables."""
    immutable = {'.mix', '.pld', '.w3d', '.dds', '.tga', '.mp3', '.wav', '.bmp'}
    for directory, _, files in os.walk(source):
        relative = Path(directory).relative_to(source)
        target = destination / relative
        target.mkdir(parents=True, exist_ok=True)
        for filename in files:
            original, copied = Path(directory) / filename, target / filename
            if copied.exists(): continue
            parts = {part.lower() for part in relative.parts}
            if 'data' in parts and not parts.intersection({'ini', 'output', 'logs'}) and original.suffix.lower() in immutable:
                try:
                    os.link(original, copied)
                    continue
                except OSError: pass  # Another volume/filesystem: ordinary copy.
            shutil.copy2(original, copied)


def prepare_profile(name: str, client: Path, proxy: Path) -> ClientProfile:
    name = name.lower()
    if re.fullmatch(r'[a-z0-9_]{1,24}', name) is None:
        raise ValueError('Instance name must contain 1-24 letters, digits or underscores')
    parent = ROOT / 'state/clients'
    with named_mutex('Global\\AcMacroRustClientProfiles'):
        parent.mkdir(parents=True, exist_ok=True)
        directory = parent / name
        metadata = directory / 'profile.local.json'
        if metadata.exists():
            record = json.loads(metadata.read_text())
            address = str(record['address'])
            if str(record['source_client']) != str(client) or str(record['source_proxy']) != str(proxy):
                raise ValueError('This instance was prepared from a different installation')
        else:
            reserved = {str(json.loads(path.read_text())['address']) for path in parent.glob('*/profile.local.json')}
            address = next((f'127.0.0.{slot}' for slot in range(2,10) if f'127.0.0.{slot}' not in reserved), '')
            if not address: raise RuntimeError('All eight additional native-client profiles are allocated')
            directory.mkdir(parents=True, exist_ok=True)
            record = {'version': 1, 'address': address, 'source_client': str(client),
                      'source_proxy': str(proxy), 'prepared': False}
            metadata.write_text(json.dumps(record, indent=2))
        profile = ClientProfile(name, address, directory)
        if not record['prepared']:
            print(f'Preparing private game files for {name}; immutable assets use hard links', flush=True)
            copy_client(client.parent.parent, directory / 'game')
            profile.proxy.parent.mkdir(parents=True, exist_ok=True)
            for path in proxy.parent.iterdir():
                if path.is_file() and path.suffix.lower() in {'.exe','.dll','.dat','.cer','.pem','.ini','.cfg'}:
                    shutil.copy2(path, profile.proxy.parent / path.name)
            record['prepared'] = True
            metadata.write_text(json.dumps(record, indent=2))
        for relative, kind in [('Data/client/ini/auth.ini','auth'), ('Data/common/network.ini','network'),
                               ('Data/client/ini/rg_regdata.ini','registration')]:
            path = directory / 'game' / relative
            path.write_bytes(local_configuration(path.read_bytes(), kind, hostname=profile.hostname))
        return profile


class ProfileDns:
    def __init__(self, profile: ClientProfile) -> None:
        self.profile = profile
        self.path = Path(os.environ.get('SystemRoot','C:/Windows')) / 'System32/drivers/etc/hosts'
        self.marker = f'# acmacro-local-instance {profile.name}'.encode('ascii')
        self.line = f'{profile.address}\t{profile.hostname}\t'.encode('ascii') + self.marker + b'\r\n'

    def __enter__(self) -> 'ProfileDns':
        with named_mutex('Global\\AcMacroRustHosts'):
            original = self.path.read_bytes()
            # The profile lifetime mutex proves an old same-profile entry has
            # no active launcher. Never remove an unowned user hosts entry.
            lines = [line for line in original.splitlines(keepends=True) if not self.owns_line(line)]
            for line in lines:
                if self.profile.hostname.encode('ascii') in line.partition(b'#')[0].split():
                    raise RuntimeError('Instance hostname already has an unowned hosts entry')
            (self.profile.directory / f'hosts-before-{uuid.uuid4().hex}.bin').write_bytes(original)
            try:
                self.path.write_bytes(b''.join(lines).rstrip(b'\r\n') + b'\r\n' + self.line)
                self.flush()
            except BaseException:
                self.path.write_bytes(b''.join(line for line in self.path.read_bytes().splitlines(keepends=True)
                                              if not self.owns_line(line)))
                raise
        return self

    def owns_line(self, line: bytes) -> bool:
        return line.partition(b'#')[2].strip() == self.marker[2:]

    def flush(self) -> None:
        subprocess.run([str(Path(os.environ.get('SystemRoot','C:/Windows')) / 'System32/ipconfig.exe'), '/flushdns'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=True)

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        with named_mutex('Global\\AcMacroRustHosts'):
            current = self.path.read_bytes()
            self.path.write_bytes(b''.join(line for line in current.splitlines(keepends=True) if not self.owns_line(line)))
            self.flush()


def launch_profile(name: str, server: str, client: Path, proxy: Path, *, no_build: bool) -> None:
    if os.name != 'nt': raise RuntimeError('Native game profiles require Windows')
    if re.fullmatch(r'[a-zA-Z0-9_]{1,24}', name) is None: raise ValueError('Invalid instance name')
    from local_windows import ProxyJob, ensure_no_game
    from proxy_certificate import LocalProxyCertificate
    from local_relays import ProfileRelays
    from start_proxy import ProxyPlan, ensure_server
    with named_mutex('Global\\AcMacroRustClient_' + name.lower(), timeout=0):
        profile = prepare_profile(name, client, proxy)
        ensure_no_game(profile.client, profile.proxy)
        isolate_client_mutex(profile.client, client, profile.name)
        ensure_server(server, no_build)
        plan = ProxyPlan(server, profile.client, profile.proxy)
        with ProfileDns(profile), LocalProxyCertificate(profile.proxy, instances=True):
            job = ProxyJob()
            relays: ProfileRelays | None = None
            try:
                isolate_existing_windows(client)
                job.start(plan.arguments(), profile.proxy.parent, loopback=profile.address)
                if job.pid is None: raise RuntimeError('Private proxy process was not created')
                relays = ProfileRelays(job.pid,profile.address)
                relays.start()
                (profile.directory / 'launch.local.json').write_text(json.dumps({'proxy_pid': job.pid, 'server': server}))
                print(f'Client {profile.name}: proxy PID {job.pid}, local interface {profile.address}. Log in with that account; keep this launcher open.', flush=True)
                job.wait()
            finally:
                if relays is not None: relays.close()
                job.close()


def isolate_existing_windows(original_client: Path) -> None:
    """Prevent the legacy proxy attaching to somebody else's canonical window.

    This only changes titles, without focus/input or game state. The process
    handle and repeated window PID check pin each rename to the inspected game.
    """
    import win32api
    import win32gui
    import win32process
    from local_windows import process_path

    def rename(hwnd: int, context: object) -> None:
        if win32gui.GetWindowText(hwnd) != 'Earth & Beyond': return
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        try: handle = win32api.OpenProcess(0x1000, False, pid)
        except (OSError, win32api.error): return
        try:
            path = Path(process_path(int(handle))).resolve()
            if path != original_client and not path.is_relative_to(ROOT / 'state/clients'):
                raise RuntimeError('Another unowned Earth & Beyond window is starting; no proxy launched')
            children: list[int] = []
            win32gui.EnumChildWindows(hwnd, lambda child, extra: children.append(child), None)
            if any(win32gui.GetWindowText(child) == 'I Agree' for child in children):
                raise RuntimeError('Finish the existing client startup dialog before launching another profile')
            if win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                win32gui.SetWindowText(hwnd, f'ENB_local_{pid}')
        finally: handle.Close()

    win32gui.EnumWindows(rename, None)
