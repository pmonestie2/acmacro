import struct
import unittest

from protocol.vendor_catalog import AuxFragments, StockItem, VendorStock


class VendorCatalogTests(unittest.TestCase):
    def body(self, records: bytes, changed: tuple[int, ...] = (0,)) -> bytes:
        header = bytearray(27)
        header[7] = 0x80
        flags = 2 | sum(1 << (slot + 4) for slot in changed)
        header[10:27] = flags.to_bytes(17, 'little')
        return bytes(header) + records

    def test_delta_keeps_cached_fields_and_clears_empty_slots(self) -> None:
        stock = VendorStock([StockItem(0, 10, 100, 1), StockItem(1, 20, 200, 1)])
        result = stock.apply(self.body(struct.pack('<HIQHI', 0x52, 30, 300, 0x12, 0xffffffff), (0, 1)))
        self.assertIsNotNone(result)
        self.assertEqual(result, [StockItem(0, 30, 300, 1)])

    def test_bad_update_does_not_change_cache(self) -> None:
        stock = VendorStock([StockItem(0, 10, 100)])
        for records in (b'\xff\xff', struct.pack('<HI', 0x52, 99), b'\x52'):
            with self.assertRaises(ValueError):
                stock.apply(self.body(records))
            self.assertEqual(stock.slots, [StockItem(0, 10, 100)])

    def test_observed_compact_and_full_secure_inventory_deltas(self) -> None:
        for changed in ((3,), (1, 3, 4, 5), tuple(range(22))):
            flags = (2 | sum(1 << (slot + 4) for slot in changed)).to_bytes(13, 'little')
            secure = flags + b''.join(struct.pack('<HQ', 0x42, 100) for _ in changed)
            plain = self.body(struct.pack('<HIQ', 0x52, 1952, 8499))
            header = bytearray(plain[:10])
            header[7] = 0xc0
            body = bytes(header) + secure + plain[10:]
            self.assertEqual(VendorStock([]).apply(body), [StockItem(0, 1952, 8499)])

    def test_sparse_delta_keeps_unmodified_vendor_slots(self) -> None:
        stock = VendorStock([StockItem(0, 10, 100), StockItem(1, 20, 200), StockItem(2, 30, 300)])
        result = stock.apply(self.body(struct.pack('<HQ', 0x42, 999), (2,)))
        self.assertEqual(result, [StockItem(0, 10, 100), StockItem(1, 20, 200), StockItem(2, 30, 999)])

    def test_average_cost_structure_quality_and_string_fields(self) -> None:
        records = struct.pack('<HIQfffH', 0x7d2, 10, 100, 5.5, .5, 1.0, 3) + b'abc'
        item = VendorStock([]).apply(self.body(records))
        self.assertEqual(item, [StockItem(0, 10, 100, quality=1.0, structure=.5, average_cost=5.5)])

    def test_missing_baseline_is_not_a_complete_inventory(self) -> None:
        with self.assertRaises(ValueError):
            VendorStock([]).apply(self.body(struct.pack('<HQ', 0x42, 100)))

    def test_fragments_require_matching_owner_and_next_sequence(self) -> None:
        fragments = AuxFragments()
        frame = struct.pack('<HH', 12, 0x1b) + b'12345678'
        start = struct.pack('<HHII', 20, 0x2016, 42, 9) + frame[:8]
        self.assertIsNone(fragments.feed(start, True))
        wrong = struct.pack('<HHII', 16, 0x201a, 43, 10) + frame[8:]
        self.assertIsNone(fragments.feed(wrong, False))
        final = struct.pack('<HHII', 16, 0x201a, 42, 10) + frame[8:]
        self.assertEqual(fragments.feed(final, False), b'12345678')


if __name__ == '__main__':
    unittest.main()
