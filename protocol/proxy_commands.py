"""Guarded native game-command submission through the fingerprinted Net-7 proxy."""
from __future__ import annotations
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
from pathlib import Path
import struct
import time
from contextlib import contextmanager

import win32event
import win32api
from collections.abc import Callable, Iterator
from typing import TypedDict, TypeVar, cast
from focus import process_identity

FINGERPRINT = 'dbcece6433882234e842696713fef5454da91b90d3fa199e327e3301cd7faf87'
SUCCESS = 0x4e415631
GUARD_NAMES = ('root', 'connection', 'sender', 'connection vtable', 'connection type', 'session entity')
# Keep the historical directory and mutex: running older macros share these guards.
ARENA_FOLDER = Path(__file__).resolve().parent / 'captures' / 'navigation_arenas'
CODE_PAGE = 4096
SUPPORTED_VENDOR_NPC_IDS = frozenset((*range(1, 601), 636, 832))
FORWARDED_OPCODES = frozenset((0x4e, 0x55, 0x27, 0x7b, 0x7e))
ARENA_SIZE = 16 * 1024 * 1024


class CodeArena(TypedDict):
    base: int
    next_page: int


def proxy_key(pid: int) -> str:
    born = process_identity(pid)
    if born is None:
        raise RuntimeError('Proxy exited before command submission')
    return f'{pid}_{hashlib.sha256(born.encode()).hexdigest()[:16]}'


@contextmanager
def submission_lock(pid: int) -> Iterator[None]:
    handle = win32event.CreateMutex(None, False, 'Global\\AcMacroNavigation_' + proxy_key(pid))
    acquired = False
    try:
        acquired = win32event.WaitForSingleObject(handle, 10000) in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED)
        if not acquired:
            raise RuntimeError('Another proxy command submission is busy; no command attempted')
        yield
    finally:
        if acquired:
            win32event.ReleaseMutex(handle)
        win32api.CloseHandle(handle)


def allocate_code(process: Process, pid: int, size: int) -> int:
    """Burn a fresh code address per submission, including across macro restarts.

    On this ARM64 VM, freeing/reusing x86 stub addresses executed stale code
    despite byte readback and FlushInstructionCache. Reserve a per-proxy arena;
    decommit completed pages but never release/reuse their addresses. The proxy's
    exit frees its reservations. Caller must hold submission_lock.
    """
    if size > CODE_PAGE:
        raise RuntimeError('Command stub exceeds its code page')
    ARENA_FOLDER.mkdir(parents=True, exist_ok=True)
    path = ARENA_FOLDER / (proxy_key(pid) + '.json')
    arena = cast(CodeArena, json.loads(path.read_text())) if path.exists() else None
    if not arena or arena['next_page'] >= ARENA_SIZE // CODE_PAGE:
        # Start above the old allocator's reused addresses. Do not reuse even
        # old arenas: Windows will reject attempts to reserve occupied regions.
        base = None
        for candidate in range(0x52000000, 0x70000000, ARENA_SIZE):
            base = process.k.VirtualAllocEx(process.handle, candidate, ARENA_SIZE, 0x2000, 4)
            if base:
                break
        if not base:
            raise RuntimeError('No fresh command code arena available; no command attempted')
        arena = {'base': base, 'next_page': 0}
    address = arena['base'] + arena['next_page'] * CODE_PAGE
    arena['next_page'] += 1
    # Persist the consumed address before committing/executing it. A failed
    # attempt or killed macro must never cause the slot to be reused.
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(arena))
    temporary.replace(path)
    committed = process.check(process.k.VirtualAllocEx(process.handle, address, CODE_PAGE, 0x1000, 4))
    if committed != address:
        raise RuntimeError('Command code committed at an unexpected address')
    return address


def validate_frame(frame: bytes) -> tuple[int, tuple[int, ...]]:
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


def validate_command(frame: bytes) -> tuple[int, int, tuple[int, ...]]:
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
    elif opcode == 0x2c and len(frame) == 20:
        game_id, action, target, optional = struct.unpack_from('<4I', frame, 4)
        if game_id & 0xff000000 != 0x40000000 or action != 2 or optional != 0:
            raise ValueError('Only captured space-object dialogue action 2 is supported')
        entity = game_id & 0xffffff
        targets = (target,)
    elif opcode == 0x4e and len(frame) == 13:
        entity, target, action = struct.unpack_from('<IIB', frame, 4)
        if not 0 < entity <= 0xffffff or action not in (4, 6, 7, 8, 9):
            raise ValueError('Only captured job-terminal/vendor starbase actions are supported')
        if action == 4 and target not in SUPPORTED_VENDOR_NPC_IDS:
            raise ValueError('NPC ID is not enabled for guarded vendor probing')
        if action in (6, 9):
            if target != 0:
                raise ValueError('Open/close job-terminal target must be zero')
            targets = ()
        else:
            targets = (target,)
    elif opcode == 0x55 and len(frame) == 9:
        entity, choice = struct.unpack_from('<IB', frame, 4)
        if not 0 < entity <= 0xffffff or choice not in (0, 2, 3, 4, 230):
            raise ValueError('Only captured vendor trade/close dialog choices are supported')
        targets = ()
    elif opcode in (0x7b, 0x7e) and len(frame) == 12:
        entity, value = struct.unpack_from('>II', frame, 4)
        if not 0 < entity <= 0xffffff or (opcode == 0x7b and value != 3199) or (opcode == 0x7e and value not in (0, 2)):
            raise ValueError('Only captured Callipus M2 selection and manufacture start/close are supported')
        targets = ()
    elif opcode == 0x27 and len(frame) == 28:
        game_id, source, slot, destination, destination_slot, quantity = struct.unpack_from('>6I', frame, 4)
        is_purchase = (source == 4 and slot in (0, 2, 3, 23, 57) and destination == 1 and
                       destination_slot == 0xffffffff and
                       (quantity == 1 or (slot in (0, 2, 3) and 1 <= quantity <= 40)))
        is_sale = (source == 1 and 0 <= slot < 40 and destination == 4 and
                   destination_slot == 0 and quantity == 1)
        is_vault_deposit = (source == 1 and 0 <= slot < 40 and destination == 3 and
                            0 <= destination_slot < 96 and 1 <= quantity <= 0x7fffffff)
        is_vault_withdrawal = (source == 3 and 0 <= slot < 96 and destination == 1 and
                               0 <= destination_slot < 40 and quantity == 1)
        if game_id & 0xff000000 != 0x40000000 or not (
                is_purchase or is_sale or is_vault_deposit or is_vault_withdrawal):
            raise ValueError('Only bounded vendor transfers, cargo-to-vault stacks or single-unit vault withdrawals are supported')
        entity, targets = game_id & 0xffffff, ()
    elif opcode == 0x5d and len(frame) == 10:
        # Captured Kraken L3 activation: full little-endian player GameID,
        # equipment inventory 2, inventory slot 1 (UI hotkey 4).
        # Restrict this experiment to the exact observed equipment slot.
        game_id, inventory, slot = struct.unpack_from('<IBB', frame, 4)
        if game_id & 0xff000000 != 0x40000000 or (inventory, slot) != (2, 1):
            raise ValueError('Only the captured Kraken equipment slot is supported')
        entity = game_id & 0xffffff
        targets = (game_id,)
    else:
        raise ValueError('Unsupported native command opcode or size')
    if not entity or any(t in (0, 0xffffffff) for t in targets):
        raise ValueError('Invalid entity or target')
    return opcode, entity, targets


def build_stub(root: int, connection: int, sender: int, scratch: int,
               body_length: int, entity: int, opcode: int = 0x9b,
               phase_address: int | None = None) -> bytes:
    """x86 stdcall thread entry; invoke the selected native game-command handler once."""
    code = bytearray(b'\x53\x56\x57\x55')  # preserve EBX, ESI, EDI, EBP
    branches: list[int] = []
    def guard(address: int, value: int) -> None:
        if phase_address is not None:
            code.extend(b'\xc7\x05' + struct.pack('<II', phase_address, 10 + len(branches)))
        code.extend(b'\x81\x3d' + struct.pack('<II', address, value))
        code.extend(b'\x0f\x85\x00\x00\x00\x00')
        branches.append(len(code) - 4)
    guard(0x44e620, root)
    guard(root + 0xb10, connection)
    guard(root + 0x22e, sender)
    guard(connection, 0x43fc1c)
    guard(connection + 0xc717, 3)
    if phase_address is not None:
        code.extend(b'\xc7\x05' + struct.pack('<II', phase_address, 15))
    code.extend(b'\xa1' + struct.pack('<I', sender + 0x10))
    code.extend(b'\x25\xff\xff\xff\x00\x3d' + struct.pack('<I', entity))
    code.extend(b'\x0f\x85\x00\x00\x00\x00')
    branches.append(len(code)-4)
    if phase_address is not None:
        # Written only after every guard passes, before invoking native code.
        code.extend(b'\xc7\x05' + struct.pack('<II', phase_address, 1))
    if opcode in FORWARDED_OPCODES:
        # Captured starbase actions 4/6..9, dialog choices and vendor transfers
        # take the dispatcher's ordinary forwarding path. The starbase local
        # special case is action 1, which our validator rejects.
        code.extend(b'\x68' + struct.pack('<I', scratch + 0x237))
        code.extend(b'\x68' + struct.pack('<I', body_length))
        code.extend(b'\x68' + struct.pack('<I', opcode) + b'\xb9' + struct.pack('<I', sender))
        code.extend(b'\xb8\x20\xf2\x40\x00\xff\xd0')
    else:
        code.extend(b'\x68' + struct.pack('<I', body_length))
        code.extend(b'\x68' + struct.pack('<I', opcode) + b'\xb9' + struct.pack('<I', scratch))
        code.extend(b'\xb8\x20\x28\x40\x00\xff\xd0')
    if phase_address is not None:
        code.extend(b'\xc7\x05' + struct.pack('<II', phase_address, 2))
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


T = TypeVar("T")


class Process:
    """Windows process-memory access shared by proxy and client readers."""
    def __init__(self, pid: int) -> None:
        self.k: C.WinDLL = C.WinDLL('kernel32', use_last_error=True)
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
        self.handle: int = self.k.OpenProcess(0x143a, False, pid)
        self.check(self.handle)

    @staticmethod
    def check(value: T) -> T:
        if not value:
            raise C.WinError(C.get_last_error())
        return value

    def read(self, address: int, size: int) -> bytes:
        buffer = C.create_string_buffer(size)
        count = C.c_size_t()
        self.check(self.k.ReadProcessMemory(self.handle, address, buffer, size, C.byref(count)))
        if count.value != size:
            raise RuntimeError('Short process read')
        return buffer.raw

    def u32(self, address: int) -> int:
        return struct.unpack('<I', self.read(address, 4))[0]

    def write(self, address: int, data: bytes) -> None:
        buffer = C.create_string_buffer(data)
        count = C.c_size_t()
        self.check(self.k.WriteProcessMemory(self.handle, address, buffer, len(data), C.byref(count)))
        if count.value != len(data):
            raise RuntimeError('Short process write')


def submit(pid: int, frame: bytes, report: dict[str, object], *, send: bool = True,
           before_submit: Callable[[], None] | None = None) -> None:
    """Validate and optionally submit one command; retain uncertain outcomes in report.

    The caller owns reply correlation and must not retry an uncertain submission.
    CLI preflight passes send=False; application callers submit explicitly supplied bytes.
    """
    with submission_lock(pid):
        _submit_locked(pid, frame, report, send=send, before_submit=before_submit)


def _submit_locked(pid: int, frame: bytes, report: dict[str, object], *, send: bool,
                   before_submit: Callable[[], None] | None = None) -> None:
    opcode, entity, waypoints = validate_command(frame)
    process = Process(pid)
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
        if opcode in FORWARDED_OPCODES and process.read(0x40f220, 7) != bytes.fromhex('558bec83791000'):
            raise RuntimeError('Native forwarding code signature mismatch')
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
        if not send:
            return
        # Private scratch object: never overwrite the live TCP receive buffer or RC4 state.
        scratch_data = bytearray(process.read(connection, 0xcfb0))
        scratch_data[0x237:0x237+len(frame)-4] = frame[4:]
        phase_offset = len(scratch_data)
        scratch_data.extend(b'\x00' * 4)
        scratch = process.check(process.k.VirtualAllocEx(process.handle, None, len(scratch_data), 0x3000, 4))
        if scratch > 0xffffffff:
            raise RuntimeError('Allocation is outside x86 address space')
        process.write(scratch, bytes(scratch_data))
        stub = build_stub(root, connection, sender, scratch, len(frame)-4, entity, opcode,
                          phase_address=scratch + phase_offset)
        code_address = allocate_code(process, pid, len(stub))
        if code_address > 0xffffffff:
            raise RuntimeError('Code allocation is outside x86 address space')
        process.write(code_address, stub)
        old = W.DWORD()
        process.check(process.k.VirtualProtectEx(process.handle, code_address, len(stub), 0x20, C.byref(old)))
        process.check(process.k.FlushInstructionCache(process.handle, code_address, len(stub)))
        if process.read(code_address, len(stub)) != stub:
            raise RuntimeError('Remote command stub readback mismatch; no thread started')
        report.update(stub_hex=stub.hex(), scratch=hex(scratch), code_address=hex(code_address),
                      phase_address=hex(scratch + phase_offset))
        thread_id = W.DWORD()
        if before_submit is not None:
            before_submit()
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
        if opcode in FORWARDED_OPCODES:
            report.update(forwarding_handler='0x40f220', connection_after=hex(process.u32(root + 0xb10)))
        phase = process.u32(scratch + phase_offset)
        report['stub_unchanged'] = process.read(code_address, len(stub)) == stub
        report['native_phase'] = phase
        report['native_handler_attempted'] = phase in (1, 2)
        if exit_code.value == 0xdead0001 and 10 <= phase <= 15:
            guard_name = GUARD_NAMES[phase - 10]
            report.update(state='not_sent', failed_guard=guard_name,
                          error=f'Native preflight guard rejected {guard_name} before handler invocation')
            return
        if exit_code.value != SUCCESS:
            raise RuntimeError(f'Native thread exited {exit_code.value:#x}; phase={phase}')
    finally:
        if not thread or finished:
            if code_address:
                # MEM_DECOMMIT releases physical memory but keeps the virtual
                # address reserved, preventing stale translated-code reuse.
                process.k.VirtualFreeEx(process.handle, code_address, CODE_PAGE, 0x4000)
            if scratch:
                process.k.VirtualFreeEx(process.handle, scratch, 0, 0x8000)
        if thread:
            process.k.CloseHandle(thread)
        process.k.CloseHandle(process.handle)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--frame', type=Path, required=True)
    parser.add_argument('--send', action='store_true', help='Submit exactly once; default only validates')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report: dict[str, object] = {'time': time.time(), 'pid': args.pid, 'send_requested': args.send, 'state': 'preparing'}
    try:
        submit(args.pid, args.frame.read_bytes(), report, send=args.send)
    except Exception as error:
        report.update(state='failed', error=str(error))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    if report['state'] not in ('validated', 'handler_returned'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
