from __future__ import annotations

import struct
import unittest
from unittest.mock import Mock, patch

from protocol.client_sector import (ACTOR_NETWORK_GAME_ID, ACTOR_PLAYER, ACTOR_VTABLE,
    ClientSectorReader, INTEGER_PROPERTY_VTABLE, PLAYER_VTABLE, PROPERTY_VALUE,
    SECTOR_NAME_PROPERTY, SECTOR_NUMBER_PROPERTY, STRING_PROPERTY_VTABLE, read_current_sector)


class ClientSectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor, self.player, self.name_pointer = 0x20000, 0x30000, 0x40000
        self.game_id = 0x4003c204
        self.values = {self.actor: ACTOR_VTABLE, self.actor + ACTOR_PLAYER: self.player,
            self.player: PLAYER_VTABLE, self.player + 0x9c: 0,
            self.player + SECTOR_NAME_PROPERTY: STRING_PROPERTY_VTABLE,
            self.player + SECTOR_NUMBER_PROPERTY: INTEGER_PROPERTY_VTABLE,
            self.player + SECTOR_NAME_PROPERTY + PROPERTY_VALUE: self.name_pointer,
            self.player + SECTOR_NUMBER_PROPERTY + PROPERTY_VALUE: 1060}
        self.name = b'Earth\0'
        self.cargo = Mock(inventory=0x50000, game_id=self.game_id)
        self.process = self.cargo.memory.return_value
        self.process.u32.side_effect = self.values.__getitem__
        self.process.read.side_effect = self.read_memory
        self.reader = ClientSectorReader(self.cargo)

    def read_memory(self, address: int, size: int) -> bytes:
        if address == self.actor + ACTOR_NETWORK_GAME_ID:
            return struct.pack('>I', self.game_id)
        if address == self.name_pointer:
            return self.name.ljust(size, b'\0')
        raise OSError('Unmapped memory')

    def test_owned_actor_sector_and_network_order_identity(self) -> None:
        with patch.object(self.reader, 'candidate_actors', return_value={self.actor}):
            self.assertEqual(self.reader.read(), 'Earth')
        self.assertEqual(self.cargo.validate_inventory.call_count, 2)
        self.cargo.game_id = 0x4003c209
        with self.assertRaisesRegex(RuntimeError, 'does not belong'):
            self.reader.snapshot(self.actor)

    def test_no_actor_or_multiple_actors_fail_closed(self) -> None:
        for candidates in (set(), {self.actor, self.actor + 1}):
            with self.subTest(candidates=candidates), \
                    patch.object(self.reader, 'candidate_actors', return_value=candidates):
                with self.assertRaisesRegex(RuntimeError, 'Expected one'):
                    self.reader.read()

    def test_invalid_properties_names_and_sector_number_are_rejected(self) -> None:
        for address in (self.actor, self.player, self.player + 0x9c,
                        self.player + SECTOR_NAME_PROPERTY, self.player + SECTOR_NUMBER_PROPERTY,
                        self.player + SECTOR_NUMBER_PROPERTY + PROPERTY_VALUE):
            with self.subTest(address=address):
                saved = self.values[address]
                self.values[address] = 0 if saved else 1
                with self.assertRaises(RuntimeError):
                    self.reader.snapshot(self.actor)
                self.values[address] = saved
        for invalid in (b'\0', b'X' * 128, b'Earth\n\0', b'\xff\0'):
            with self.subTest(name=invalid), self.assertRaises(RuntimeError):
                self.name = invalid
                self.reader.snapshot(self.actor)

    def test_changed_snapshot_or_owned_ship_aborts(self) -> None:
        first = self.reader.snapshot(self.actor)
        self.name = b'Mars\0'
        second = self.reader.snapshot(self.actor)
        with patch.object(self.reader, 'candidate_actors', return_value={self.actor}), \
                patch.object(self.reader, 'snapshot', side_effect=[first, second]):
            with self.assertRaisesRegex(RuntimeError, 'changed while reading'):
                self.reader.read()
        self.cargo.validate_inventory.side_effect = RuntimeError('Ship changed')
        with patch.object(self.reader, 'candidate_actors') as scan:
            with self.assertRaisesRegex(RuntimeError, 'Ship changed'):
                self.reader.read()
            scan.assert_not_called()

    def test_candidate_scan_ignores_unrelated_vtable_occurrences(self) -> None:
        block = bytearray(64)
        struct.pack_into('<I', block, 4, ACTOR_VTABLE)
        struct.pack_into('<I', block, 24, ACTOR_VTABLE)
        self.process.read.side_effect = None
        self.process.read.return_value = bytes(block)
        with patch.object(self.reader, 'regions', return_value=iter([(0x60000, 64)])), \
                patch.object(self.reader, 'snapshot', side_effect=[RuntimeError('other actor'), Mock()]):
            self.assertEqual(self.reader.candidate_actors(), {0x60018})

    def test_public_reader_always_closes_after_open_or_read_failure(self) -> None:
        for open_failure in (False, True):
            with self.subTest(open_failure=open_failure), \
                    patch('protocol.client_sector.ClientCargo') as cargo_class, \
                    patch('protocol.client_sector.ClientSectorReader') as reader_class:
                cargo = cargo_class.return_value
                if open_failure:
                    cargo.open.side_effect = RuntimeError('open failed')
                else:
                    reader_class.return_value.read.side_effect = RuntimeError('read failed')
                with self.assertRaises(RuntimeError):
                    read_current_sector(1, 'born', self.game_id)
                cargo.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
