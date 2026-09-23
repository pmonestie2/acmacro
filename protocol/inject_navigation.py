"""One-shot navigation submission for the fingerprinted Net-7 proxy build."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
from pathlib import Path
import struct
import time

FINGERPRINT = 'dbcece6433882234e842696713fef5454da91b90d3fa199e327e3301cd7faf87'
SUCCESS = 0x4e415631


def validate_frame(frame):
    if len(frame) < 14:
        raise ValueError('Navigation frame is too short')
    length, opcode, entity, count = struct.unpack_from('<HHIH', frame)
    if length != len(frame) or opcode != 0x9b:
        raise ValueError('Only an exact-length 0x009b navigation frame is accepted')
    if not 1 <= count <= 64 or len(frame) != 10 + count * 4:
        raise ValueError('Invalid waypoint count')
    waypoints = struct.unpack_from('<' + 'I' * count, frame, 10)
    if not entity or any(x == 0 or x == 0xffffffff for x in waypoints):
        raise ValueError('Invalid entity or waypoint ID')
    return entity, waypoints


def validate_command(frame):
    if len(frame) < 4:
        raise ValueError('Truncated command')
    length, opcode = struct.unpack_from('<HH', frame)
    if length != len(frame):
        raise ValueError('Frame length mismatch')
    if opcode == 0x9b:
        entity, targets = validate_frame(frame)
    elif opcode == 0x5a and len(frame) == 16:
        entity, target = struct.unpack_from('>II', frame, 4)
        action = struct.unpack_from('<I', frame, 12)[0]
        if action != 1:
            raise ValueError('Only action 1 object-information requests are supported')
        entity &= 0xffffff
        targets = (target,)
    elif opcode == 0x17 and len(frame) == 12:
        raise ValueError('Selection injection disabled: live wire output did not match the intended command; investigate before retrying')
    else:
        raise ValueError('Only navigation, target selection, and action 1 information requests are supported')
    if not entity or any(t in (0, 0xffffffff) for t in targets):
        raise ValueError('Invalid entity or target')
    return opcode, entity, targets


def build_stub(root, connection, sender, scratch, body_length, entity, opcode=0x9b):
    """x86 stdcall thread entry; invoke the known thiscall navigation handler once."""
    code = bytearray(b'\x53\x56\x57\x55')  # preserve EBX, ESI, EDI, EBP
    branches = []
    def guard(address, value):
        code.extend(b'\x81\x3d' + struct.pack('<II', address, value))
        code.extend(b'\x0f\x85\x00\x00\x00\x00')
        branches.append(len(code) - 4)
    guard(0x44e620, root)
    guard(root + 0xb10, connection)
    guard(root + 0x22e, sender)
    guard(connection, 0x43fc1c)
    guard(connection + 0xc717, 3)
    code.extend(b'\xa1' + struct.pack('<I', sender + 0x10))
    code.extend(b'\x25\xff\xff\xff\x00\x3d' + struct.pack('<I', entity))
    code.extend(b'\x0f\x85\x00\x00\x00\x00')
    branches.append(len(code)-4)
    code.extend(b'\x68' + struct.pack('<I', body_length))
    code.extend(b'\x68' + struct.pack('<I', opcode) + b'\xb9' + struct.pack('<I', scratch))
    code.extend(b'\xb8\x20\x28\x40\x00\xff\xd0')
    # The real handler clears this byte on its connection object for opcode 0x9b.
    if opcode == 0x9b:
        code.extend(b'\xc6\x05' + struct.pack('<I', connection + 0x232) + b'\x00')
    code.extend(b'\xb8' + struct.pack('<I', SUCCESS) + b'\xeb\x05')
    failure = len(code)
    code.extend(b'\xb8\x01\x00\xad\xde')
    code.extend(b'\x5d\x5f\x5e\x5b\xc2\x04\x00')
    for offset in branches:
        struct.pack_into('<i', code, offset, failure - offset - 4)
    return bytes(code)


class Process:
    def __init__(self, pid):
        self.k = C.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'OpenProcess': ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            'ReadProcessMemory': ([W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], W.BOOL),
            'WriteProcessMemory': ([W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], W.BOOL),
            'VirtualAllocEx': ([W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD, W.DWORD], C.c_void_p),
            'VirtualFreeEx': ([W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD], W.BOOL),
            'VirtualProtectEx': ([W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD, C.POINTER(W.DWORD)], W.BOOL),
            'FlushInstructionCache': ([W.HANDLE, C.c_void_p, C.c_size_t], W.BOOL),
            'CreateRemoteThread': ([W.HANDLE, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)], W.HANDLE),
            'WaitForSingleObject': ([W.HANDLE, W.DWORD], W.DWORD),
            'GetExitCodeThread': ([W.HANDLE, C.POINTER(W.DWORD)], W.BOOL),
            'CloseHandle': ([W.HANDLE], W.BOOL),
            'QueryFullProcessImageNameW': ([W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result
        self.handle = self.k.OpenProcess(0x143a, False, pid)
        self.check(self.handle)

    @staticmethod
    def check(value):
        if not value:
            raise C.WinError(C.get_last_error())
        return value

    def read(self, address, size):
        buffer = C.create_string_buffer(size)
        count = C.c_size_t()
        self.check(self.k.ReadProcessMemory(self.handle, address, buffer, size, C.byref(count)))
        if count.value != size:
            raise RuntimeError('Short process read')
        return buffer.raw

    def u32(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def write(self, address, data):
        buffer = C.create_string_buffer(data)
        count = C.c_size_t()
        self.check(self.k.WriteProcessMemory(self.handle, address, buffer, len(data), C.byref(count)))
        if count.value != len(data):
            raise RuntimeError('Short process write')


def run(args, report):
    frame = args.frame.read_bytes()
    opcode, entity, waypoints = validate_command(frame)
    process = Process(args.pid)
    scratch = code_address = thread = None
    finished = False
    try:
        image_name = C.create_unicode_buffer(32768)
        size = W.DWORD(len(image_name))
        process.check(process.k.QueryFullProcessImageNameW(process.handle, 0, image_name, C.byref(size)))
        if hashlib.sha256(Path(image_name.value).read_bytes()).hexdigest() != FINGERPRINT:
            raise RuntimeError('Unsupported proxy executable fingerprint')
        if process.read(0x40412d, 6) != bytes.fromhex('c7061cfc4300') or process.read(0x402820, 6) != bytes.fromhex('558bec535657'):
            raise RuntimeError('Runtime code signature mismatch')
        root = process.u32(0x44e620)
        connection = process.u32(root + 0xb10)
        sender = process.u32(root + 0x22e)
        if process.u32(connection) != 0x43fc1c or process.u32(connection + 0xc717) != 3:
            raise RuntimeError('No supported live game connection')
        if process.read(connection + 0x230, 2) != b'\x01\x01':
            raise RuntimeError('Game connection is inactive')
        token = process.u32(sender + 0x10)
        if token & 0xffffff != entity or process.read(sender + 0x380cd, 1) != b'\x01':
            raise RuntimeError('Captured frame does not belong to the active session')
        deadline = time.monotonic() + 5
        while True:
            before = process.u32(sender + 0x381e7)
            time.sleep(0.25)
            if process.u32(sender + 0x381e7) == before:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Command sender stayed busy; no injection attempted')
        report.update(state='validated', opcode=hex(opcode), connection=hex(connection), entity=hex(entity),
                      waypoints=[hex(x) for x in waypoints], sequence_before=before)
        if not args.send:
            return
        # Private scratch object: never overwrite the live TCP receive buffer or RC4 state.
        scratch_data = bytearray(process.read(connection, 0xcfb0))
        scratch_data[0x237:0x237+len(frame)-4] = frame[4:]
        scratch = process.check(process.k.VirtualAllocEx(process.handle, None, len(scratch_data), 0x3000, 4))
        if scratch > 0xffffffff:
            raise RuntimeError('Allocation is outside x86 address space')
        process.write(scratch, bytes(scratch_data))
        stub = build_stub(root, connection, sender, scratch, len(frame)-4, entity, opcode)
        code_address = process.check(process.k.VirtualAllocEx(process.handle, None, len(stub), 0x3000, 4))
        if code_address > 0xffffffff:
            raise RuntimeError('Code allocation is outside x86 address space')
        process.write(code_address, stub)
        old = W.DWORD()
        process.check(process.k.VirtualProtectEx(process.handle, code_address, len(stub), 0x20, C.byref(old)))
        process.check(process.k.FlushInstructionCache(process.handle, code_address, len(stub)))
        thread_id = W.DWORD()
        thread = process.check(process.k.CreateRemoteThread(process.handle, None, 0, code_address, None, 0, C.byref(thread_id)))
        report.update(state='submitted', thread_id=thread_id.value)
        if process.k.WaitForSingleObject(thread, 5000) != 0:
            report.update(state='unknown', error='Native call did not finish within five seconds; no retry, allocations retained')
            return
        finished = True
        exit_code = W.DWORD()
        process.check(process.k.GetExitCodeThread(thread, C.byref(exit_code)))
        report.update(state='handler_returned' if exit_code.value == SUCCESS else 'failed',
                      thread_exit=hex(exit_code.value), sequence_after=process.u32(sender + 0x381e7))
        if exit_code.value != SUCCESS:
            raise RuntimeError('Native handler guard failed or thread terminated abnormally')
    finally:
        if not thread or finished:
            for allocation in (code_address, scratch):
                if allocation:
                    process.k.VirtualFreeEx(process.handle, allocation, 0, 0x8000)
        if thread:
            process.k.CloseHandle(thread)
        process.k.CloseHandle(process.handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--frame', type=Path, required=True)
    parser.add_argument('--send', action='store_true', help='Submit exactly once; default only validates')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = {'time': time.time(), 'pid': args.pid, 'send_requested': args.send, 'state': 'preparing'}
    try:
        run(args, report)
    except Exception as error:
        report.update(state='failed', error=str(error))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    if report['state'] not in ('validated', 'handler_returned'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
