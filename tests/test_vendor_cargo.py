from __future__ import annotations

import json
from pathlib import Path
import struct
import unittest

from protocol.vendor_cargo import CargoItem, VendorCargo, decode_cargo


GAME_ID = 0x4003c209


def cargo_update(records: dict[int, tuple[int | None, int | None]]) -> bytes:
    flags = 2 | sum(1 << (slot + 4) for slot in records)
    body = struct.pack('<IHB', GAME_ID, 0, 1) + (2 | (1 << 40)).to_bytes(8, 'little')
    body += struct.pack('<H', 0x402) + flags.to_bytes(6, 'little')
    for item_id, quantity in records.values():
        mask = 2 | (0x10 if item_id is not None else 0) | (0x20 if quantity is not None else 0)
        body += struct.pack('<H', mask)
        if item_id is not None:
            body += struct.pack('<I', item_id)
        if quantity is not None:
            body += struct.pack('<I', quantity)
    return body


class VendorCargoTests(unittest.TestCase):
    def test_recorded_bulk_purchase_has_actual_slots_and_ids(self) -> None:
        fixture = json.loads((Path(__file__).parent / 'fixtures/vendor_cargo_purchase.json').read_text())
        decoded = decode_cargo(bytes.fromhex(fixture['body_hex']), GAME_ID)
        self.assertEqual(decoded, {slot: CargoItem(5258, 1) for slot in range(22)})

    def test_mixed_cargo_only_matches_configured_item_in_nonconsecutive_slots(self) -> None:
        state = VendorCargo(GAME_ID)
        state.apply(cargo_update({0: (3115, 1), 6: (6549, 1), 32: (6549, 1), 39: (6548, 1)}))
        self.assertEqual(state.matching(6549), {6: 1, 32: 1})
        self.assertEqual(state.matching(6548), {39: 1})
        with self.assertRaises(RuntimeError):
            state.require(0, 6549, 1)

    def test_quantity_without_identity_never_authorizes_sale(self) -> None:
        state = VendorCargo(GAME_ID)
        state.apply(cargo_update({0: (None, 22)}))
        self.assertEqual(state.matching(6549), {})
        state.apply(cargo_update({0: (6549, None)}))
        self.assertEqual(state.matching(6549), {})

    def test_preexisting_items_are_eligible_without_a_purchase(self) -> None:
        state = VendorCargo(GAME_ID)
        state.apply(cargo_update({0: (6549, 1), 5: (6549, 3), 7: (3115, 1)}))
        state.apply(cargo_update({9: (6549, 1)}))
        self.assertEqual(state.matching(6549), {0: 1, 5: 3, 9: 1})

    def test_other_character_and_player_vendor_aux_are_ignored(self) -> None:
        body = cargo_update({2: (6549, 1)})
        self.assertIsNone(decode_cargo(body, GAME_ID + 1))
        self.assertIsNone(decode_cargo(bytes(20), GAME_ID))

    def test_truncation_is_transactional_and_blocks_further_sales(self) -> None:
        state = VendorCargo(GAME_ID)
        state.apply(cargo_update({0: (6549, 1)}))
        state.apply(cargo_update({1: (6549, 1), 2: (6549, 1)})[:-1])
        self.assertEqual(state.slots, {})
        with self.assertRaisesRegex(RuntimeError, 'Truncated'):
            state.require(0, 6549, 1)

    def test_stack_decrement_and_final_clear_confirm_only_one_sale(self) -> None:
        state = VendorCargo(GAME_ID)
        state.apply(cargo_update({5: (6549, 2)}))
        self.assertFalse(state.sale_confirmed(5, 6549, 2))
        state.apply(cargo_update({5: (None, 1)}))
        self.assertTrue(state.sale_confirmed(5, 6549, 2))
        state.apply(cargo_update({5: (0xffffffff, None)}))
        self.assertTrue(state.sale_confirmed(5, 6549, 1))

    def test_replaced_item_cannot_confirm_sale_or_reuse_previous_quantity(self) -> None:
        state = VendorCargo(GAME_ID)
        state.apply(cargo_update({5: (6549, 2)}))
        state.apply(cargo_update({5: (3115, None)}))
        self.assertEqual(state.slots[5], CargoItem(3115, None))
        with self.assertRaises(RuntimeError):
            state.sale_confirmed(5, 6549, 2)

    def test_unsupported_inventory_prefix_stops_instead_of_guessing_offsets(self) -> None:
        state = VendorCargo(GAME_ID)
        body = bytearray(cargo_update({0: (6549, 1)}))
        body[15:17] = struct.pack('<H', 0x442)  # Mounts before CargoInv.
        state.apply(bytes(body))
        with self.assertRaisesRegex(RuntimeError, 'mount'):
            state.matching(6549)


if __name__ == '__main__':
    unittest.main()
