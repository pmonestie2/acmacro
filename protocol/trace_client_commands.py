"""Temporarily enable this proxy build's existing packet diagnostics for 30 seconds."""
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import sys
import time


def main():
    pid = int(sys.argv[1])
    folder = Path(__file__).resolve().parent / 'captures'
    folder.mkdir(exist_ok=True)
    status_file = folder / 'client_trace_status.json'
    k = C.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = W.HANDLE
    k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
    k.WriteProcessMemory.argtypes = k.ReadProcessMemory.argtypes
    k.CloseHandle.argtypes = [W.HANDLE]
    handle = k.OpenProcess(0x1038, False, pid)
    if not handle:
        raise C.WinError(C.get_last_error())
    restore = []
    status = {'state': 'preparing', 'pid': pid}
    def save():
        status_file.write_text(json.dumps(status, indent=2))
    def read(address, length):
        data = C.create_string_buffer(length)
        count = C.c_size_t()
        if not k.ReadProcessMemory(handle, address, data, length, C.byref(count)) or count.value != length:
            raise C.WinError(C.get_last_error())
        return data.raw
    def write(address, value):
        data = C.create_string_buffer(value)
        count = C.c_size_t()
        if not k.WriteProcessMemory(handle, address, data, len(value), C.byref(count)) or count.value != len(value):
            raise C.WinError(C.get_last_error())
    def change(address, value):
        original = read(address, len(value))
        restore.append((address, original))
        write(address, value)
    try:
        save()
        if read(0x40412d, 6) != bytes.fromhex('c7061cfc4300'):
            raise RuntimeError('Proxy signature differs')
        root = struct.unpack('<I', read(0x44e620, 4))[0]
        connection = struct.unpack('<I', read(root + 0xb10, 4))[0]
        if not connection or read(connection, 4) != bytes.fromhex('1cfc4300'):
            raise RuntimeError('No valid game connection')
        if struct.unpack('<I', read(connection + 0xc717, 4))[0] != 3 or read(connection + 0x230, 2) != b'\x01\x01':
            raise RuntimeError('Game connection is not an active type 3')
        if read(root + 0xb02, 4) != bytes(4):
            raise RuntimeError('Proxy already has an open diagnostic log; refusing to redirect it')
        prefix = str(folder / ('client_trace_' + datetime.now().strftime('%Y%m%d_%H%M%S')))
        encoded = prefix.encode('ascii') + b'\0'
        if len(encoded) > 256:
            raise RuntimeError('Trace path too long')
        logfile = prefix + datetime.now(timezone.utc).strftime('_%Y_%m_%d.log')
        change(0x44e500, encoded)
        change(0x44e61b, b'\x01')
        change(connection + 0xc731, b'\x01')
        status.update(state='capturing', logfile=logfile, connection=hex(connection), seconds=30)
        save()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        status['state'] = 'complete'
    except Exception as error:
        status.update(state='failed', error=str(error))
    finally:
        errors = []
        for address, original in reversed(restore):
            try:
                write(address, original)
            except Exception as error:
                errors.append(str(error))
        status['restored'] = not errors
        if errors:
            status['restore_errors'] = errors
        save()
        k.CloseHandle(handle)


if __name__ == '__main__':
    main()
