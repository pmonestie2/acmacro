from __future__ import annotations

import struct
import unittest
from unittest.mock import Mock

from protocol.client_cargo import ClientCargo
from protocol.client_vault import ClientVault
from protocol.vendor_cargo import CargoItem


class ClientVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cargo = Mock(spec=ClientCargo)
        self.memory = self.cargo.memory.return_value
        self.vault = ClientVault(self.cargo)
        self.vault.root = 0x200000
        group = 0x2001c8
        self.values: dict[int, int] = {0x200000: 0xb03360, 0x200074: 0, 0x20009c: 0,
            group: 0xb00f54, group + 0x74: 0x200000, group + 0x78: 2,
            group + 0x88: 0x300000, group + 0x8c: 0x300000 + 384}
        for slot in range(96):
            item = 0x400000 + slot * 0x1000
            self.values[0x300000 + slot * 4] = item
            self.values.update({item: 0xb0111c, item + 0x9c: 0xb00d34, item + 0x124: 0xb00d34,
                                item + 0x120: 775 if slot == 26 else 0xffffffff,
                                item + 0x1a8: 1 if slot == 26 else 0})
        self.memory.u32.side_effect = self.values.__getitem__
        self.memory.read.side_effect = lambda address, size: b''.join(
            struct.pack('<I', self.values[address + offset]) for offset in range(0, size, 4))

    def test_current_vault_reads_all_slots_and_item_identity(self) -> None:
        result = self.vault.snapshot(Mock())
        self.assertEqual(len(result), 96)
        self.assertEqual(result[26], CargoItem(775, 1))
        self.assertEqual(result[95], CargoItem(0xffffffff, 0))

    def test_wrong_parent_or_field_index_is_rejected(self) -> None:
        for address in (0x20023c, 0x200240):
            previous = self.values[address]
            self.values[address] = 999
            with self.assertRaisesRegex(RuntimeError, 'structure changed'):
                self.vault.snapshot(Mock())
            self.values[address] = previous

    def test_unknown_item_structure_is_rejected(self) -> None:
        self.values[0x400000 + 95 * 0x1000] = 0
        with self.assertRaisesRegex(RuntimeError, 'slot 95'):
            self.vault.snapshot(Mock())

    def test_stop_is_checked_before_reads(self) -> None:
        with self.assertRaises(InterruptedError):
            self.vault.snapshot(Mock(side_effect=InterruptedError('stop')))
        self.memory.read.assert_not_called()
