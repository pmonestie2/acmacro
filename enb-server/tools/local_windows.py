"""Windows-only isolation for the optional original game/proxy launcher.

The Rust server never imports or depends on this module. A job object owns only
the proxy launched here and its descendants. Closing it ends those processes
before the temporary local-certificate DNS override is restored.
"""
from __future__ import annotations

import os
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import uuid
import hashlib
import ipaddress

import win32api
import win32con
import win32event
import win32job
import win32process

from build import ROOT

MARKER = b'acmacro-local-sandbox'


def process_path(handle: int) -> str:
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    query = kernel.QueryFullProcessImageNameW
    query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buffer))
    if not query(handle, 0, buffer, ctypes.byref(size)):
        raise OSError(ctypes.get_last_error(), 'Cannot read process image')
    return buffer.value


def local_hosts(original: bytes, marker: bytes) -> bytes:
    """Replace only the local development hostname; keep other aliases intact."""
    if MARKER in original:
        raise RuntimeError('A local sandbox DNS override is already active; close that launcher first')
    output: list[bytes] = []
    for line in original.splitlines(keepends=True):
        content, separator, comment = line.partition(b'#')
        fields = content.split()
        if len(fields) >= 2 and any(name.lower() == b'local.net-7.org' for name in fields[1:]):
            aliases = [name for name in fields[1:] if name.lower() != b'local.net-7.org']
            if aliases:
                output.append(fields[0] + b'\t' + b' '.join(aliases) + (b' #' + comment.rstrip(b'\r\n') if separator else b'') + b'\r\n')
            elif separator:
                output.append(b'#' + comment.rstrip(b'\r\n') + b'\r\n')
        else:
            output.append(line)
    return b''.join(output).rstrip(b'\r\n') + b'\r\n127.0.0.1\tlocal.net-7.org\t# ' + marker + b'\r\n'


class LocalCertificateDns:
    def __init__(self) -> None:
        self.path = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/drivers/etc/hosts'
        self.original = b''
        self.applied = b''
        self.backup = ROOT / 'logs' / f'hosts-before-{uuid.uuid4().hex}.bin'

    def __enter__(self) -> 'LocalCertificateDns':
        from local_profiles import named_mutex
        with named_mutex('Global\\AcMacroRustHosts'):
            return self.install()

    def install(self) -> 'LocalCertificateDns':
        self.original = self.path.read_bytes()
        self.applied = local_hosts(self.original, MARKER + b' ' + self.backup.stem.encode('ascii'))
        self.backup.parent.mkdir(exist_ok=True)
        self.backup.write_bytes(self.original)
        try:
            self.path.write_bytes(self.applied)
            self.flush()
        except PermissionError:
            raise PermissionError('Run local_game.cmd as administrator so its temporary local certificate DNS entry can be installed') from None
        except BaseException:
            if self.path.read_bytes() == self.applied:
                self.path.write_bytes(self.original)
            raise
        return self

    def flush(self) -> None:
        subprocess.run([str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/ipconfig.exe'), '/flushdns'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, check=True)

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        from local_profiles import named_mutex
        with named_mutex('Global\\AcMacroRustHosts'):
            self.restore()

    def restore(self) -> None:
        current = self.path.read_bytes()
        restored = restore_local_hosts(self.original, self.applied, current)
        if restored is not None:
            self.path.write_bytes(restored)
            self.flush()
        else:
            # Do not overwrite another program's concurrent hosts-file edits.
            # Retain the backup and identify the recovery path explicitly.
            print(f'Hosts file changed while the sandbox ran; restore its local.net-7.org entry using {self.backup}', flush=True)


def restore_local_hosts(original: bytes, applied: bytes, current: bytes) -> bytes | None:
    """Undo the primary override while preserving live private client aliases.

    Another profile can start or close during this launcher's lifetime. Those
    owned lines are expected concurrent edits. Unknown edits remain untouched.
    """
    if current == applied:
        return original

    def profile_line(line: bytes) -> bool:
        return line.partition(b'#')[2].strip().startswith(b'acmacro-local-instance ')

    current_lines = current.splitlines(keepends=True)
    current_base = b''.join(line for line in current_lines if not profile_line(line))
    applied_base = b''.join(line for line in applied.splitlines(keepends=True) if not profile_line(line))
    if current_base.rstrip(b'\r\n') != applied_base.rstrip(b'\r\n'):
        return None
    base = b''.join(line for line in original.splitlines(keepends=True) if not profile_line(line))
    profiles = b''.join(line for line in current_lines if profile_line(line))
    return base.rstrip(b'\r\n') + (b'\r\n' if profiles else b'') + profiles


class ProxyJob:
    def __init__(self) -> None:
        self.job: int | None = None
        self.process: int | None = None
        self.pid: int | None = None

    def start(self, arguments: list[str], directory: Path, *, loopback: str | None = None) -> None:
        job = win32job.CreateJobObject(None, '')
        settings = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        settings['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, settings)
        self.job = job.Detach()
        startup = win32process.STARTUPINFO()
        startup.dwFlags = win32con.STARTF_USESHOWWINDOW
        startup.wShowWindow = win32con.SW_HIDE
        # Suspend before assigning the job so the game cannot spawn outside our
        # owned process tree between CreateProcess and AssignProcessToJobObject.
        process, thread, pid, _ = win32process.CreateProcess(arguments[0], subprocess.list2cmdline(arguments), None, None, False,
            win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW, None, str(directory), startup)
        try:
            if loopback is not None:
                configure_proxy_loopback(int(process), Path(arguments[0]), loopback)
            win32job.AssignProcessToJobObject(self.job, process)
            win32process.ResumeThread(thread)
            self.process, self.pid = process.Detach(), pid
        except BaseException:
            win32api.TerminateProcess(process, 1)
            process.Close()
            raise
        finally:
            thread.Close()

    def wait(self) -> int:
        if self.process is None:
            raise RuntimeError('No owned local proxy process')
        while win32event.WaitForSingleObject(self.process, 250) == win32event.WAIT_TIMEOUT:
            pass
        return win32process.GetExitCodeProcess(self.process)

    def close(self) -> None:
        if self.job is not None:
            win32api.CloseHandle(self.job)
            self.job = None
        if self.process is not None:
            win32event.WaitForSingleObject(self.process, 5000)
            win32api.CloseHandle(self.process)
            self.process = None


def configure_proxy_loopback(process: int, image: Path, address: str) -> None:
    """Give the suspended, owned 2.46 proxy a private localhost interface.

    /LADDRESS binds wildcard listeners in this build. Changing only the private
    default literal keeps HTTPS/global/master/sector listeners on one interface.
    The executable file and every instruction byte remain unchanged.
    """
    if (not ipaddress.IPv4Address(address).is_loopback or not address.startswith('127.0.0.')
            or not 2 <= int(address.rsplit('.', 1)[1]) <= 9):
        raise ValueError('Instance address must be 127.0.0.2 through 127.0.0.9')
    expected = 'dbcece6433882234e842696713fef5454da91b90d3fa199e327e3301cd7faf87'
    if hashlib.sha256(image.read_bytes()).hexdigest() != expected:
        raise RuntimeError('Unsupported proxy build; no instance process resumed')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.WriteProcessMemory.argtypes = kernel.ReadProcessMemory.argtypes
    kernel.VirtualProtectEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    pointer = 0x4402e8
    original = ctypes.create_string_buffer(10)
    count = ctypes.c_size_t()
    if not kernel.ReadProcessMemory(process, pointer, original, 10, ctypes.byref(count)) or original.raw != b'127.0.0.1\0':
        raise RuntimeError('Proxy localhost literal differs; process will not run')
    previous = wintypes.DWORD()
    if not kernel.VirtualProtectEx(process, pointer, 10, 4, ctypes.byref(previous)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        value = ctypes.create_string_buffer(address.encode('ascii') + b'\0', 10)
        if not kernel.WriteProcessMemory(process, pointer, value, 10, ctypes.byref(count)) or count.value != 10:
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.ReadProcessMemory(process, pointer, original, 10, ctypes.byref(count)) or original.raw != value.raw:
            raise RuntimeError('Proxy loopback readback mismatch; process will not run')
    finally:
        restored = wintypes.DWORD()
        if not kernel.VirtualProtectEx(process, pointer, 10, previous.value, ctypes.byref(restored)):
            raise ctypes.WinError(ctypes.get_last_error())


def ensure_no_game(client: Path, proxy: Path) -> None:
    expected = {os.path.normcase(str(client)), os.path.normcase(str(proxy))}
    for pid in win32process.EnumProcesses():
        try:
            handle = win32api.OpenProcess(0x1000, False, pid)
        except (OSError, win32api.error):
            continue
        try:
            path = process_path(int(handle))
            if os.path.normcase(path) in expected:
                raise RuntimeError('Close existing game clients and proxies before starting the isolated local sandbox')
        except (OSError, win32api.error):
            continue
        finally:
            handle.Close()
