from __future__ import annotations

import struct
import unittest
from unittest.mock import Mock, patch

from protocol.client_cargo import CargoChanging, ClientCargo
from protocol.proxy_commands import Process
from protocol.vendor_cargo import CargoItem


class ClientCargoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reader = ClientCargo(123, 'born', 0x4003c204)
        self.reader.inventory = 0x200000
        self.process = Mock(spec=Process)
        self.process.k = Mock()
        self.process.handle = 321
        self.reader.process = self.process
        self.group = 0x200000 + 0x3f4
        self.values: dict[int, int] = {
            0x200000 - 0x1990: 0xb0094c,
            0x200000 - 0x1990 + 0x9c: 0x4003c204,
            0x200000: 0xb00c7c, self.group: 0xb00f54,
            0x200000 + 0x9c: 0xb00d34, 0x200000 + 0x120: 18,
            self.group + 0x88: 0x300000, self.group + 0x8c: 0x300000 + 160,
        }
        for slot in range(40):
            item = 0x400000 + slot * 0x1000
            self.values[0x300000 + slot * 4] = item
            self.values[item] = 0xb0111c
            self.values[item + 0x9c] = 0xb00d34
            self.values[item + 0x124] = 0xb00d34
            self.values[item + 0x120] = 6549 if slot in (0, 7, 39) else 3115
            self.values[item + 0x1a8] = 1
        self.process.u32.side_effect = lambda address: self.values[address]
        self.process.read.side_effect = self.read
        identity = patch('protocol.client_cargo.process_identity', return_value='born')
        identity.start()
        self.addCleanup(identity.stop)

    def read(self, address: int, size: int) -> bytes:
        return b''.join(struct.pack('<I', self.values[address + offset]) for offset in range(0, size, 4))

    def test_capacity_is_distinct_from_the_forty_entry_backing_array(self) -> None:
        self.assertEqual(self.reader.capacity(), 18)
        self.assertEqual(len(self.reader.snapshot()), 40)

    def test_invalid_capacity_and_property_type_are_rejected(self) -> None:
        for capacity in (0, 41, 0xffffffff):
            self.values[0x200000 + 0x120] = capacity
            with self.subTest(capacity=capacity), self.assertRaises(RuntimeError):
                self.reader.capacity()
        self.values[0x200000 + 0x120] = 18
        self.values[0x200000 + 0x9c] = 0
        with self.assertRaisesRegex(RuntimeError, 'capacity property'):
            self.reader.capacity()

    def test_existing_ids_are_read_from_the_owned_ship_including_high_slots(self) -> None:
        snapshot = self.reader.snapshot()
        self.assertEqual(len(snapshot), 40)
        self.assertEqual(snapshot[0], CargoItem(6549, 1))
        self.assertEqual(snapshot[7], CargoItem(6549, 1))
        self.assertEqual(snapshot[39], CargoItem(6549, 1))
        self.assertEqual(snapshot[3], CargoItem(3115, 1))
        self.assertFalse(hasattr(self.process, 'write') and self.process.write.called)

    def test_other_players_inventory_is_rejected(self) -> None:
        self.values[0x200000 - 0x1990 + 0x9c] = 0x4003c209
        with self.assertRaisesRegex(RuntimeError, 'Owned cargo'):
            self.reader.snapshot()

    def test_reused_pid_is_rejected_before_read(self) -> None:
        with patch('protocol.client_cargo.process_identity', return_value='new birth'):
            with self.assertRaisesRegex(RuntimeError, 'exited or changed'):
                self.reader.snapshot()
        self.process.read.assert_not_called()

    def test_unknown_item_structure_is_rejected(self) -> None:
        self.values[0x400000 + 7 * 0x1000 + 0x9c] = 0
        with self.assertRaisesRegex(RuntimeError, 'slot 7'):
            self.reader.snapshot()

    def test_wrong_capacity_is_rejected(self) -> None:
        self.values[self.group + 0x8c] -= 4
        with self.assertRaisesRegex(RuntimeError, 'array changed'):
            self.reader.snapshot()

    def test_transient_difference_is_reread_without_using_the_old_identity(self) -> None:
        old = {0: CargoItem(6549, 1)}
        current = {0: CargoItem(3115, 1)}
        self.reader.read_slots = Mock(side_effect=[old, current, current, current])
        with patch('protocol.client_cargo.time.sleep'):
            self.assertEqual(self.reader.snapshot(), current)
        self.assertEqual(self.reader.read_slots.call_count, 4)

    def test_continually_differing_reads_stop_at_the_deadline(self) -> None:
        self.reader.read_slots = Mock(side_effect=[{0: CargoItem(6549, 1)}, {0: CargoItem(3115, 1)}])
        with self.assertRaisesRegex(RuntimeError, 'did not settle'):
            self.reader.snapshot(clock=Mock(side_effect=[0, 1]))

    def test_transient_pointer_change_is_reread(self) -> None:
        current = {0: CargoItem(6549, 1)}
        self.reader.read_slots = Mock(side_effect=[CargoChanging('pointer update'), current, current])
        with patch('protocol.client_cargo.time.sleep'):
            self.assertEqual(self.reader.snapshot(), current)

    def test_stop_during_read_retries_propagates_without_more_reads(self) -> None:
        self.reader.read_slots = Mock(side_effect=[{0: CargoItem(6549, 1)}, {}])
        checkpoint = Mock(side_effect=[None, InterruptedError('stop')])
        with patch('protocol.client_cargo.time.sleep'), self.assertRaises(InterruptedError):
            self.reader.snapshot(checkpoint=checkpoint)
        self.assertEqual(self.reader.read_slots.call_count, 2)

    def test_close_releases_handle_and_cached_address(self) -> None:
        self.reader.close()
        self.process.k.CloseHandle.assert_called_once()
        self.assertIsNone(self.reader.process)
        self.assertIsNone(self.reader.inventory)

    def test_unsupported_client_build_closes_handle_without_reading_inventory(self) -> None:
        self.reader.process = None
        self.reader.inventory = None
        def image_name(handle: int, flags: int, name: object, size: object) -> bool:
            name.value = 'unused-client.exe'
            return True
        self.process.k.QueryFullProcessImageNameW.side_effect = image_name
        with patch('protocol.client_cargo.Process', return_value=self.process), \
                patch('protocol.client_cargo.Path.read_bytes', return_value=b'unsupported build'):
            with self.assertRaisesRegex(RuntimeError, 'Unsupported client build'):
                self.reader.open()
        self.process.read.assert_not_called()
        self.process.k.CloseHandle.assert_called_once_with(321)


if __name__ == '__main__':
    unittest.main()
