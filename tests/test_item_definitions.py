import ctypes as C
from ctypes import wintypes as W
import struct
import unittest
from unittest.mock import Mock, patch

from protocol import item_definitions as definitions
from protocol.vendor_trading import VendorSession


SESSION = VendorSession(1, 'client', 2, 'proxy', 0x4003c204)
CONTEXT = definitions.ReplayContext(0x10000, 0x20000, 0x30000, SESSION.game_id,
                                    0x40000, 0x50000, 0x55450, 355, 0x60000)


class ItemDefinitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.process = Mock()
        self.process.check.side_effect = lambda value: value
        self.process.k.VirtualAllocEx.return_value = 0x100000
        self.process.k.CreateRemoteThread.return_value = 7
        self.process.k.WaitForSingleObject.return_value = 0
        self.process.u32.return_value = 2
        self.writes: dict[int, bytes] = {}
        self.process.write.side_effect = self.writes.__setitem__
        self.process.read.side_effect = lambda address, size: (
            struct.pack('<HH', 359, 0x25) if address == 0x100100 else self.writes[address][:size])
        def exit_code(thread: int, pointer: object) -> int:
            C.cast(pointer, C.POINTER(W.DWORD)).contents.value = definitions.SUCCESS
            return 1
        self.process.k.GetExitCodeThread.side_effect = exit_code
        self.callback = Mock()
        for target, values in (
                ('Process', {'return_value': self.process}),
                ('validate_context', {'return_value': CONTEXT}),
                ('allocate_code', {'return_value': 0x52000000}),
                ('submission_lock', {})):
            mocker = patch.object(definitions, target, **values)
            mocker.start()
            self.addCleanup(mocker.stop)
        session_check = patch.object(VendorSession, 'check')
        session_check.start()
        self.addCleanup(session_check.stop)

    def test_replay_uses_cached_id_and_frees_completed_allocations(self) -> None:
        definitions.replay_item_definition(SESSION, 2698, before_submit=self.callback)
        self.assertEqual(struct.unpack('<I', self.writes[0x100000][:4])[0], 2698)
        self.callback.assert_called_once()
        self.process.k.CreateRemoteThread.assert_called_once()
        self.assertEqual(self.process.k.VirtualFreeEx.call_count, 2)
        self.assertEqual(self.process.k.CloseHandle.call_count, 2)

    def test_native_timeout_retains_allocations_and_never_retries(self) -> None:
        self.process.k.WaitForSingleObject.return_value = 258
        with self.assertRaisesRegex(TimeoutError, 'no retry'):
            definitions.replay_item_definition(SESSION, 2698, before_submit=self.callback)
        self.process.k.CreateRemoteThread.assert_called_once()
        self.process.k.VirtualFreeEx.assert_not_called()
        self.assertEqual(self.process.k.CloseHandle.call_count, 2)

    def test_cancellation_prevents_thread_creation_and_frees_scratch(self) -> None:
        self.callback.side_effect = InterruptedError('stop')
        with self.assertRaises(InterruptedError):
            definitions.replay_item_definition(SESSION, 2698, before_submit=self.callback)
        self.process.k.CreateRemoteThread.assert_not_called()
        self.assertEqual(self.process.k.VirtualFreeEx.call_count, 2)

    def test_missing_cache_entry_prevents_native_replay(self) -> None:
        with patch.object(definitions, 'validate_context', side_effect=RuntimeError('missing cache entry')):
            with self.assertRaisesRegex(RuntimeError, 'missing cache'):
                definitions.replay_item_definition(SESSION, 2698, before_submit=self.callback)
        self.process.k.CreateRemoteThread.assert_not_called()
        self.process.k.VirtualAllocEx.assert_not_called()
        self.process.k.CloseHandle.assert_called_once()

    def test_invalid_output_is_reported_after_handler_returns(self) -> None:
        original_read = self.process.read.side_effect
        self.process.read.side_effect = lambda address, size: (
            struct.pack('<HH', 4, 0x25) if address == 0x100100 else original_read(address, size))
        with self.assertRaisesRegex(RuntimeError, 'no valid definition'):
            definitions.replay_item_definition(SESSION, 2698, before_submit=self.callback)
        self.process.k.CreateRemoteThread.assert_called_once()
        self.assertEqual(self.process.k.VirtualFreeEx.call_count, 2)

    def test_stub_guards_all_context_before_calling_receive_handler(self) -> None:
        code = definitions.build_replay_stub(CONTEXT, 0x100000)
        handler = code.index(b'\xb8' + struct.pack('<I', definitions.HANDLER) + b'\xff\xd0')
        failure = code.index(bytes.fromhex('b80100adde'))
        for address, value in CONTEXT.guards():
            self.assertLess(code.index(b'\x81\x3d' + struct.pack('<II', address, value)), handler)
        offset = 0
        branches = 0
        while (offset := code.find(b'\x0f\x85', offset)) >= 0:
            self.assertEqual(offset + 6 + struct.unpack_from('<i', code, offset + 2)[0], failure)
            branches += 1
            offset += 6
        self.assertEqual(branches, len(CONTEXT.guards()))


class ItemCacheValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.process = Mock()
        self.process.check.side_effect = lambda value: value
        self.values = {
            0x44e620: CONTEXT.root, CONTEXT.root + 0xb10: CONTEXT.connection,
            CONTEXT.root + 0x22e: CONTEXT.sender, CONTEXT.connection: 0x43fc1c,
            CONTEXT.connection + 0xc717: 3, CONTEXT.sender + 0x10: SESSION.game_id,
            0x44e62c: CONTEXT.cache, CONTEXT.cache + 9: 7000, CONTEXT.cache + 0xd: CONTEXT.table,
        }
        self.process.u32.side_effect = self.values.__getitem__
        self.reads = {
            definitions.HANDLER: bytes.fromhex('558bec81ec08080000'),
            0x404f30: bytes.fromhex('558bec8b410d8b550c'),
            CONTEXT.connection + 0x230: b'\x01\x01',
            CONTEXT.table + 2698 * 8: struct.pack('<II', 355, CONTEXT.data),
        }
        self.process.read.side_effect = lambda address, size: self.reads[address][:size]
        for mocker in (patch('pathlib.Path.read_bytes', return_value=b'proxy'),
                       patch.object(definitions.hashlib, 'sha256',
                                    return_value=Mock(hexdigest=Mock(return_value=definitions.FINGERPRINT)))):
            mocker.start()
            self.addCleanup(mocker.stop)

    def test_validates_cached_definition_for_owned_character(self) -> None:
        context = definitions.validate_context(self.process, SESSION, 2698)
        self.assertEqual((context.entry, context.length), (CONTEXT.table + 2698 * 8, 355))

    def test_item_id_outside_table_is_rejected_before_entry_read(self) -> None:
        self.values[CONTEXT.cache + 9] = 100
        with self.assertRaisesRegex(ValueError, 'outside the proxy item cache'):
            definitions.validate_context(self.process, SESSION, 2698)
        self.assertFalse(any(call.args[0] == CONTEXT.table + 2698 * 8
                             for call in self.process.read.call_args_list))

    def test_empty_or_oversized_entry_is_rejected(self) -> None:
        for length in (0, 0x800, 0xffffffff):
            with self.subTest(length=length):
                self.reads[CONTEXT.table + 2698 * 8] = struct.pack('<II', length, CONTEXT.data)
                with self.assertRaisesRegex(RuntimeError, 'no supported cached definition'):
                    definitions.validate_context(self.process, SESSION, 2698)

    def test_changed_character_or_handler_is_rejected(self) -> None:
        self.values[CONTEXT.sender + 0x10] += 1
        with self.assertRaisesRegex(RuntimeError, 'different character'):
            definitions.validate_context(self.process, SESSION, 2698)
        self.values[CONTEXT.sender + 0x10] = SESSION.game_id
        self.reads[definitions.HANDLER] = bytes(9)
        with self.assertRaisesRegex(RuntimeError, 'signature mismatch'):
            definitions.validate_context(self.process, SESSION, 2698)


if __name__ == '__main__':
    unittest.main()
