"""Read only known connection structures; never writes to the target process."""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import struct
import sys
import time


class MBI(C.Structure):
    _fields_ = [("base", C.c_void_p), ("allocation", C.c_void_p),
                ("allocation_protect", W.DWORD), ("partition", W.WORD),
                ("size", C.c_size_t), ("state", W.DWORD),
                ("protect", W.DWORD), ("type", W.DWORD)]


def main():
    pid = int(sys.argv[1])
    out = Path(sys.argv[2])
    k = C.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = W.HANDLE
    k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
    k.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t]
    k.VirtualQueryEx.restype = C.c_size_t
    k.CloseHandle.argtypes = [W.HANDLE]
    handle = k.OpenProcess(0x1010, False, pid)
    if not handle:
        raise C.WinError(C.get_last_error())
    def read(address, size):
        buf = C.create_string_buffer(size)
        count = C.c_size_t()
        if not k.ReadProcessMemory(handle, address, buf, size, C.byref(count)):
            return b''
        return buf.raw[:count.value]
    try:
        # Verify installed build's connection constructor before using fixed offsets.
        if read(0x40412d, 6) != bytes.fromhex('c7061cfc4300'):
            raise RuntimeError('Proxy code signature differs; refusing fixed-offset inspection')
        candidates = set()
        address = 0
        while address < 0x80000000:
            mbi = MBI()
            if not k.VirtualQueryEx(handle, address, C.byref(mbi), C.sizeof(mbi)):
                break
            base, size = mbi.base or 0, mbi.size
            if not size:
                break
            if mbi.state == 0x1000 and mbi.type == 0x20000 and not mbi.protect & 0x101:
                for offset in range(0, size, 1024 * 1024):
                    data = read(base + offset, min(size - offset, 1024 * 1024 + 3))
                    pos = data.find(b'\x1c\xfc\x43\x00')
                    while pos >= 0:
                        candidates.add(base + offset + pos)
                        pos = data.find(b'\x1c\xfc\x43\x00', pos + 4)
            address = base + size
        connections = []
        for address in sorted(candidates):
            data = read(address, 0xc735)
            if len(data) != 0xc735:
                continue
            kind = struct.unpack_from('<I', data, 0xc717)[0]
            if not 1 <= kind <= 10:
                continue
            states = {}
            for name, offset in [('receive', 4), ('send', 0x10a)]:
                s = data[offset + 4:offset + 260]
                if len(set(s)) == 256:
                    states[name] = {'s': s.hex(), 'i': data[offset + 260], 'j': data[offset + 261]}
            connections.append({'address': hex(address), 'type': kind,
                                'socket': struct.unpack_from('<I', data, 0x22c)[0],
                                'active': list(data[0x230:0x232]), 'states': states})
        out.parent.mkdir(parents=True, exist_ok=True)
        root = struct.unpack('<I', read(0x44e620, 4))[0]
        game_connection = struct.unpack('<I', read(root + 0xb10, 4))[0] if root else 0
        log_prefix = read(0x44e500, 256).split(b'\0', 1)[0].decode('ascii', errors='replace')
        out.write_text(json.dumps({'time': time.time(), 'pid': pid, 'connections': connections,
                                   'game_connection': hex(game_connection), 'log_prefix': log_prefix}, indent=2))
    finally:
        k.CloseHandle(handle)


if __name__ == '__main__':
    main()
