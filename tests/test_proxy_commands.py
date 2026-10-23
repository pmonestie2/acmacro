import struct
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import protocol.proxy_commands as injector
from protocol.proxy_commands import validate_frame, validate_command, build_stub, SUCCESS


class ProxyCommandTests(unittest.TestCase):
    def test_submit_keeps_unknown_result_without_retry(self) -> None:
        report: dict[str, object] = {}
        frame = b'captured frame'
        before_submit = Mock()
        def uncertain(pid: int, frame: bytes, report: dict[str, object], **kwargs: object) -> None:
            report.update(state='unknown', thread_id=42)
        with patch.object(injector, 'submission_lock') as lock, \
                patch.object(injector, '_submit_locked', side_effect=uncertain) as native:
            injector.submit(123, frame, report, before_submit=before_submit)
        lock.assert_called_once_with(123)
        native.assert_called_once_with(123, frame, report, send=True, before_submit=before_submit)
        self.assertEqual(report, {'state': 'unknown', 'thread_id': 42})
        lock.return_value.__exit__.assert_called_once()

    def test_submission_lock_failure_does_not_attempt_command(self) -> None:
        with patch.object(injector, 'submission_lock') as lock, \
                patch.object(injector, '_submit_locked') as native:
            lock.return_value.__enter__.side_effect = RuntimeError('busy')
            with self.assertRaisesRegex(RuntimeError, 'busy'):
                injector.submit(123, b'frame', {})
        native.assert_not_called()

    def test_cli_defaults_to_validation_and_requires_send_flag(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            frame = Path(folder) / 'command.bin'
            report = Path(folder) / 'report.json'
            frame.write_bytes(b'captured frame')
            for send in (False, True):
                arguments = ['proxy_commands', '--pid', '123', '--frame', str(frame), '--report', str(report)]
                if send:
                    arguments.append('--send')
                def validate(pid: int, frame: bytes, result: dict[str, object], *, send: bool) -> None:
                    result['state'] = 'handler_returned' if send else 'validated'
                with self.subTest(send=send), patch('sys.argv', arguments), \
                        patch.object(injector, 'submit', side_effect=validate) as submit:
                    injector.main()
                    submit.assert_called_once_with(123, b'captured frame', unittest.mock.ANY, send=send)
                    self.assertEqual(json.loads(report.read_text())['send_requested'], send)

    def test_space_dialogue_and_gate_activation_require_known_actions(self) -> None:
        body = bytes.fromhex('09c2034002000000c486010000000000')
        self.assertEqual(validate_command(struct.pack('<HH', 20, 0x2c) + body),
                         (0x2c, 246281, (100036,)))
        gate = struct.pack('<HH4I', 20, 0x2c, 0x4003c204, 18, 100000, 0)
        self.assertEqual(validate_command(gate), (0x2c, 246276, (100000,)))
        landing = struct.pack('<HH4I', 20, 0x2c, 0x4003c204, 8, 100000, 0)
        self.assertEqual(validate_command(landing), (0x2c, 246276, (100000,)))
        for action in (7, 28):
            docking = injector.pack_command(0x2c, struct.pack('<4I', 0x4003c204, action, 100001, 0))
            self.assertEqual(validate_command(docking), (0x2c, 246276, (100001,)))
        for game_id, action, target, optional in (
                (0x4003c209, 9, 100036, 0), (0x4003c209, 2, 100036, 1),
                (246281, 2, 100036, 0), (0x4003c209, 2, 0, 0),
                (0x4003c209, 18, 0, 0), (0x4003c209, 18, 100000, 1),
                (0x4003c209, 8, 0, 0), (0x4003c209, 8, 100000, 1),
                (0x4003c209, 7, 0, 0), (0x4003c209, 28, 100001, 1),
                (0x4003c209, 19, 100000, 0)):
            with self.assertRaises(ValueError):
                validate_command(struct.pack('<HH4I', 20, 0x2c, game_id, action, target, optional))

    def arena_process(self):
        process = Mock()
        process.k.VirtualAllocEx.side_effect = lambda handle, address, size, kind, protection: address
        def check(value):
            if not value:
                raise RuntimeError('commit failed')
            return value
        process.check.side_effect = check
        return process

    def test_code_addresses_are_not_reused_across_macro_restarts(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(injector, 'ARENA_FOLDER', Path(folder)), patch.object(injector, 'process_identity', return_value='proxy birth'):
            first = injector.allocate_code(self.arena_process(), 123, 200)
            second = injector.allocate_code(self.arena_process(), 123, 200)
            self.assertEqual(first, 0x52000000)
            self.assertEqual(second, first + injector.CODE_PAGE)

    def test_failed_commit_consumes_address_instead_of_reusing_it(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(injector, 'ARENA_FOLDER', Path(folder)), patch.object(injector, 'process_identity', return_value='proxy birth'):
            process = self.arena_process()
            process.k.VirtualAllocEx.side_effect = [0x52000000, 0]
            with self.assertRaisesRegex(RuntimeError, 'commit failed'):
                injector.allocate_code(process, 123, 200)
            self.assertEqual(injector.allocate_code(self.arena_process(), 123, 200), 0x52001000)

    def test_information_request_endianness(self):
        frame = struct.pack('<HH', 16, 0x5a) + struct.pack('>II', 0x4003a74c, 0x18706) + struct.pack('<I', 1)
        self.assertEqual(validate_command(frame), (0x5a, 0x3a74c, (0x18706,)))
        with self.assertRaises(ValueError):
            validate_command(frame[:-4] + struct.pack('<I', 2))

    def test_job_terminal_commands_match_captured_little_endian_fields(self):
        for action, target in ((6, 0), (7, 94769), (8, 94769), (9, 0)):
            frame = struct.pack('<HHIIB', 13, 0x4e, 246276, target, action)
            self.assertEqual(validate_command(frame), (0x4e, 246276, (target,) if target else ()))
        for player, target, action in ((0x4003c204, 0, 6), (246276, 0, 8),
                                       (246276, 94769, 6), (246276, 94769, 1)):
            with self.assertRaises(ValueError):
                validate_command(struct.pack('<HHIIB', 13, 0x4e, player, target, action))

    def test_selection_does_not_clear_navigation_flag(self):
        stub = build_stub(0x1000000, 0x2000000, 0x3000000, 0x4000000, 8, 123, 0x17)
        self.assertNotIn(b'\xc6\x05' + struct.pack('<I', 0x2000232), stub)

    def test_target_selection_validates_full_game_id_and_uses_forwarder(self) -> None:
        self.assertEqual(validate_command(struct.pack('<HHII', 12, 0x17, 0x4003c204, 100023)),
                         (0x17, 246276, (100023,)))
        for player, target in ((246276, 100023), (0x4003c204, 0), (0x4003c204, 0xffffffff)):
            with self.assertRaises(ValueError):
                validate_command(struct.pack('<HHII', 12, 0x17, player, target))
        self.assertIn(0x17, injector.FORWARDED_OPCODES)

    def test_vendor_experiment_restricts_npc_choices_and_single_purchase(self):
        player = 246281
        self.assertEqual(validate_command(struct.pack('<HHIIB', 13, 0x4e, player, 31, 4)), (0x4e, player, (31,)))
        for choice in (0, 2, 3, 4, 230):
            self.assertEqual(validate_command(struct.pack('<HHIB', 9, 0x55, player, choice)), (0x55, player, ()))
        purchase = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c209, 4, 23, 1, 0xffffffff, 1)
        self.assertEqual(validate_command(purchase), (0x27, player, ()))
        self.assertEqual(validate_command(struct.pack('<HHIIB', 13, 0x4e, player, 536, 4)), (0x4e, player, (536,)))
        self.assertEqual(validate_command(struct.pack('<HHIIB', 13, 0x4e, player, 32, 4)), (0x4e, player, (32,)))
        for npc in (1, 33, 34, 35, 36, 50, 70, 71, 72, 100, 150, 200, 500, 600, 636, 832):
            self.assertEqual(validate_command(struct.pack('<HHIIB', 13, 0x4e, player, npc, 4)), (0x4e, player, (npc,)))
        charon_purchase = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c209, 4, 0, 1, 0xffffffff, 1)
        self.assertEqual(validate_command(charon_purchase), (0x27, player, ()))
        self.assertEqual(validate_command(charon_purchase[:-4] + struct.pack('>I', 20)), (0x27, player, ()))
        self.assertEqual(validate_command(charon_purchase[:-4] + struct.pack('>I', 22)), (0x27, player, ()))
        orb_weaver = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c209, 4, 57, 1, 0xffffffff, 1)
        self.assertEqual(validate_command(orb_weaver), (0x27, player, ()))
        with self.assertRaises(ValueError):
            validate_command(orb_weaver[:-4] + struct.pack('>I', 20))
        for frame in (struct.pack('<HHIIB', 13, 0x4e, player, 601, 4),
                      struct.pack('<HHIB', 9, 0x55, player, 250),
                      struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c209, 4, 1, 1, 0xffffffff, 1),
                      purchase[:-4] + struct.pack('>I', 2)):
            with self.assertRaises(ValueError):
                validate_command(frame)
        for quantity in range(1, 41):
            self.assertEqual(validate_command(charon_purchase[:-4] + struct.pack('>I', quantity)), (0x27, player, ()))
        for quantity in (0, 41, 0xffffffff):
            with self.assertRaises(ValueError):
                validate_command(charon_purchase[:-4] + struct.pack('>I', quantity))
        with self.assertRaises(ValueError):
            validate_command(purchase[:-4] + struct.pack('>I', 20))

    def test_raw_data_purchase_slot_and_batch_quantity(self) -> None:
        for quantity in range(1, 41):
            frame = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c204, 4, 3, 1, 0xffffffff, quantity)
            self.assertEqual(validate_command(frame), (0x27, 246276, ()))
        for quantity in (0, 41, 42, 0xffffffff):
            frame = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c204, 4, 3, 1, 0xffffffff, quantity)
            with self.assertRaises(ValueError):
                validate_command(frame)

    def test_chocolate_purchase_uses_observed_slot_and_supported_quantities(self) -> None:
        for quantity in range(1, 41):
            frame = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c204, 4, 2, 1, 0xffffffff, quantity)
            self.assertEqual(validate_command(frame), (0x27, 246276, ()))
        for quantity in (0, 41, 0xffffffff):
            frame = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c204, 4, 2, 1, 0xffffffff, quantity)
            with self.assertRaises(ValueError):
                validate_command(frame)

    def test_vault_deposit_requires_explicit_slots_and_bounded_quantity(self) -> None:
        frame = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c204, 1, 0, 3, 26, 1)
        self.assertEqual(validate_command(frame), (0x27, 246276, ()))
        self.assertEqual(validate_command(frame[:-4] + struct.pack('>I', 700)), (0x27, 246276, ()))
        for source, slot, destination, target, quantity in (
                (1, 40, 3, 26, 1), (1, 0, 3, 96, 1), (1, 0, 3, 0xffffffff, 1),
                (1, 0, 3, 26, 0x80000000), (1, 0, 3, 26, 0), (1, 0, 11, 26, 1)):
            bad = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c204,
                               source, slot, destination, target, quantity)
            with self.subTest(fields=(source, slot, destination, target, quantity)), self.assertRaises(ValueError):
                validate_command(bad)

    def test_vault_withdrawal_requires_explicit_slots_and_one_item(self) -> None:
        frame = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c204, 3, 26, 1, 0, 1)
        self.assertEqual(validate_command(frame), (0x27, 246276, ()))
        for source, target, quantity in ((96, 0, 1), (26, 40, 1), (26, 0xffffffff, 1),
                                          (26, 0, 0), (26, 0, 2)):
            bad = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c204, 3, source, 1, target, quantity)
            with self.subTest(fields=(source, target, quantity)), self.assertRaises(ValueError):
                validate_command(bad)

    def test_vendor_commands_use_the_same_guarded_forwarding_function(self):
        for opcode in (0x55, 0x27):
            stub = build_stub(0x1000000, 0x2000000, 0x3000000, 0x4000000, 24, 123, opcode)
            self.assertIn(bytes.fromhex('b820f24000ffd0'), stub)
            self.assertNotIn(bytes.fromhex('b820284000ffd0'), stub)
            self.assertEqual(stub.count(b'\x0f\x85'), 6)

    def test_captured_vendor_sale_and_invalid_inventory_transfers(self):
        def transfer(source: int, slot: int, destination: int, destination_slot: int, quantity: int) -> bytes:
            return struct.pack('<HH', 28, 0x27) + struct.pack('>6I', 0x4003c209, source, slot, destination, destination_slot, quantity)
        for slot in (0, 21, 31, 32, 39):
            self.assertEqual(validate_command(transfer(1, slot, 4, 0, 1)), (0x27, 246281, ()))
        for fields in ((1, 40, 4, 0, 1), (1, 0, 4, 0, 2), (1, 0, 4, 1, 1),
                       (2, 0, 4, 0, 1), (1, 0, 11, 0, 1)):
            with self.assertRaises(ValueError):
                validate_command(transfer(*fields))

    def test_job_stub_forwards_private_body_with_live_sender_and_all_guards(self):
        stub = build_stub(0x1000000, 0x2000000, 0x3000000, 0x4000000, 9, 123, 0x4e)
        call = (b'\x68' + struct.pack('<I', 0x4000237) + b'\x68' + struct.pack('<I', 9)
                + b'\x68' + struct.pack('<I', 0x4e) + b'\xb9' + struct.pack('<I', 0x3000000)
                + bytes.fromhex('b820f24000ffd0'))
        self.assertIn(call, stub)
        self.assertNotIn(bytes.fromhex('b820284000ffd0'), stub)
        self.assertEqual(stub.count(b'\x0f\x85'), 6)

    def test_captured_kraken_activation_and_reject_other_slots(self):
        frame = struct.pack('<HHIBB', 10, 0x5d, 0x4003c209, 2, 1)
        self.assertEqual(validate_command(frame), (0x5d, 0x3c209, (0x4003c209,)))
        for inventory, slot in ((1, 1), (2, 0), (2, 2)):
            with self.assertRaises(ValueError):
                validate_command(struct.pack('<HHIBB', 10, 0x5d, 0x4003c209, inventory, slot))

    def frame(self):
        return struct.pack('<HHIHI', 14, 0x9b, 123, 1, 100007)

    def test_navigation_validation(self):
        self.assertEqual(validate_frame(self.frame()), (123, (100007,)))

    def test_rejects_non_navigation(self):
        frame = bytearray(self.frame())
        struct.pack_into('<H', frame, 2, 0xa3)
        with self.assertRaises(ValueError):
            validate_frame(frame)

    def test_rejects_corrupt_frames(self):
        frame = self.frame()
        for value in (frame[:-1], frame + b'\0', frame[:8] + b'\x02\x00' + frame[10:]):
            with self.assertRaises(ValueError):
                validate_frame(value)

    def test_stub_guard_branches_reach_failure(self):
        stub = build_stub(0x1000000, 0x2000000, 0x3000000, 0x4000000, 10, 123)
        failure = stub.index(bytes.fromhex('b80100adde'))
        offset = 0
        count = 0
        while (offset := stub.find(b'\x0f\x85', offset)) >= 0:
            displacement = struct.unpack_from('<i', stub, offset + 2)[0]
            self.assertEqual(offset + 6 + displacement, failure)
            count += 1
            offset += 6
        self.assertEqual(count, 6)
        self.assertIn(b'\xb8' + struct.pack('<I', SUCCESS), stub)
        self.assertTrue(stub.endswith(bytes.fromhex('5d5f5e5bc20400')))

    def test_handler_marker_is_after_guards_and_before_native_call(self):
        stub = build_stub(0x1000000, 0x2000000, 0x3000000, 0x4000000, 10, 123, phase_address=0x400cfb0)
        marker = stub.index(b'\xc7\x05' + struct.pack('<II', 0x400cfb0, 1))
        self.assertLess(stub.rindex(b'\x0f\x85'), marker)
        self.assertLess(marker, stub.index(bytes.fromhex('b820284000ffd0')))
        failure = stub.index(bytes.fromhex('b80100adde'))
        for offset in range(len(stub) - 6):
            if stub[offset:offset+2] == b'\x0f\x85':
                self.assertEqual(offset + 6 + struct.unpack_from('<i', stub, offset+2)[0], failure)


if __name__ == '__main__':
    unittest.main()
