"""Replay cached item definitions locally from Net7Proxy to its game client.

This invokes the receive-side 0x2025 handler, not the outgoing command sender.
The verified handler reads ItemCache and emits a 0x25 definition to the client.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct

from protocol.proxy_commands import CODE_PAGE, FINGERPRINT, SUCCESS, Process, allocate_code, submission_lock
from protocol.vendor_trading import VendorSession


HANDLER = 0x40f7d0
SCRATCH_SIZE = 8192
OUTPUT_OFFSET = 0x100


@dataclass(frozen=True)
class ReplayContext:
    root: int
    connection: int
    sender: int
    token: int
    cache: int
    table: int
    entry: int
    length: int
    data: int

    def guards(self) -> tuple[tuple[int, int], ...]:
        return ((0x44e620, self.root), (self.root + 0xb10, self.connection),
                (self.root + 0x22e, self.sender), (self.connection, 0x43fc1c),
                (self.connection + 0xc717, 3), (self.sender + 0x10, self.token),
                (0x44e62c, self.cache), (self.cache + 0xd, self.table),
                (self.entry, self.length), (self.entry + 4, self.data))


def validate_context(process: Process, session: VendorSession, item_id: int) -> ReplayContext:
    image = C.create_unicode_buffer(32768)
    size = W.DWORD(len(image))
    process.check(process.k.QueryFullProcessImageNameW(process.handle, 0, image, C.byref(size)))
    if hashlib.sha256(Path(image.value).read_bytes()).hexdigest() != FINGERPRINT:
        raise RuntimeError('Unsupported proxy executable fingerprint')
    if (process.read(HANDLER, 9) != bytes.fromhex('558bec81ec08080000') or
            process.read(0x404f30, 9) != bytes.fromhex('558bec8b410d8b550c')):
        raise RuntimeError('Item-definition handler/cache signature mismatch')
    root = process.u32(0x44e620)
    connection, sender = process.u32(root + 0xb10), process.u32(root + 0x22e)
    if (process.u32(connection) != 0x43fc1c or process.u32(connection + 0xc717) != 3 or
            process.read(connection + 0x230, 2) != b'\x01\x01'):
        raise RuntimeError('No supported active client connection for item definition')
    token = process.u32(sender + 0x10)
    if token & 0xffffff != session.game_id & 0xffffff:
        raise RuntimeError('Item definition belongs to a different character session')
    cache = process.u32(0x44e62c)
    maximum_id = process.u32(cache + 9)
    if not 0 < item_id <= maximum_id < 0x100000:
        raise ValueError(f'Item {item_id} is outside the proxy item cache')
    table = process.u32(cache + 0xd)
    entry = table + item_id * 8
    length, data = struct.unpack('<II', process.read(entry, 8))
    # The native handler decodes into a fixed 2048-byte local buffer.
    if not table or not 0 < length < 0x800 or not 0x10000 <= data < 0x80000000:
        raise RuntimeError(f'Item {item_id} has no supported cached definition')
    return ReplayContext(root, connection, sender, token, cache, table, entry, length, data)


def build_replay_stub(context: ReplayContext, scratch: int) -> bytes:
    """Guard pointers, then call thiscall handler(body, output, output_length)."""
    code = bytearray(bytes.fromhex('53565755'))
    branches: list[int] = []
    for address, value in context.guards():
        code.extend(b'\x81\x3d' + struct.pack('<II', address, value))
        code.extend(b'\x0f\x85\x00\x00\x00\x00')
        branches.append(len(code) - 4)
    code.extend(b'\xc7\x05' + struct.pack('<II', scratch + 8, 1))
    for value in (scratch + 4, scratch + OUTPUT_OFFSET, scratch):
        code.extend(b'\x68' + struct.pack('<I', value))
    code.extend(b'\xb9' + struct.pack('<I', context.sender))
    code.extend(b'\xb8' + struct.pack('<I', HANDLER) + b'\xff\xd0')
    code.extend(b'\xc7\x05' + struct.pack('<II', scratch + 8, 2))
    code.extend(b'\xb8' + struct.pack('<I', SUCCESS) + b'\xeb\x05')
    failure = len(code)
    code.extend(bytes.fromhex('b80100adde5d5f5e5bc20400'))
    for offset in branches:
        struct.pack_into('<i', code, offset, failure - offset - 4)
    return bytes(code)


def replay_item_definition(session: VendorSession, item_id: int, *,
                           before_submit: Callable[[], None]) -> None:
    """Load one cached definition into this client; never retry an uncertain call."""
    if not 0 < item_id < 0xfffffffe:
        raise ValueError('Item definition requires a positive item ID')
    session.check()
    with submission_lock(session.proxy_pid):
        _replay_locked(session, item_id, before_submit)


def _replay_locked(session: VendorSession, item_id: int, before_submit: Callable[[], None]) -> None:
    session.check()
    process = Process(session.proxy_pid)
    scratch: int | None = None
    code_address: int | None = None
    thread: int | None = None
    finished = False
    try:
        context = validate_context(process, session, item_id)
        scratch = process.check(process.k.VirtualAllocEx(process.handle, None, SCRATCH_SIZE, 0x3000, 4))
        if scratch > 0xffffffff:
            raise RuntimeError('Item-definition scratch is outside x86 address space')
        process.write(scratch, struct.pack('<III', item_id, 0, 0) + bytes(SCRATCH_SIZE - 12))
        stub = build_replay_stub(context, scratch)
        code_address = allocate_code(process, session.proxy_pid, len(stub))
        if code_address > 0xffffffff:
            raise RuntimeError('Item-definition stub is outside x86 address space')
        process.write(code_address, stub)
        old = W.DWORD()
        process.check(process.k.VirtualProtectEx(process.handle, code_address, len(stub), 0x20, C.byref(old)))
        process.check(process.k.FlushInstructionCache(process.handle, code_address, len(stub)))
        if process.read(code_address, len(stub)) != stub:
            raise RuntimeError('Item-definition stub readback mismatch; no thread started')
        before_submit()  # Honor pause/stop and check source/destination before native execution.
        session.check()
        thread_id = W.DWORD()
        thread = process.check(process.k.CreateRemoteThread(
            process.handle, None, 0, code_address, None, 0, C.byref(thread_id)))
        if process.k.WaitForSingleObject(thread, 5000) != 0:
            raise TimeoutError(f'Item-definition replay unconfirmed: item={item_id}; no retry; allocations retained')
        finished = True
        exit_code = W.DWORD()
        process.check(process.k.GetExitCodeThread(thread, C.byref(exit_code)))
        if exit_code.value != SUCCESS or process.u32(scratch + 8) != 2:
            raise RuntimeError(f'Item-definition replay rejected or failed: item={item_id}; no retry')
        length, opcode = struct.unpack('<HH', process.read(scratch + OUTPUT_OFFSET, 4))
        if opcode != 0x25 or not 4 < length <= 0x804:
            raise RuntimeError(f'Item-definition replay produced no valid definition: item={item_id}; no retry')
        session.check()
    finally:
        if not thread or finished:
            if code_address:
                process.k.VirtualFreeEx(process.handle, code_address, CODE_PAGE, 0x4000)
            if scratch:
                process.k.VirtualFreeEx(process.handle, scratch, 0, 0x8000)
        if thread:
            process.k.CloseHandle(thread)
        process.k.CloseHandle(process.handle)
