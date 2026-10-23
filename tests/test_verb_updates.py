from __future__ import annotations

import struct
import unittest

from protocol.verb_updates import decode_verb_update


def availability(target: int, attribute: int, verb: int = 2) -> bytes:
    return (struct.pack('>II', target, 1) + struct.pack('<HH', 2, verb)
            + struct.pack('>I', 1) + struct.pack('<HH', attribute, verb))


class VerbUpdateTests(unittest.TestCase):
    def test_live_enabled_current_overrides_default_too_far(self) -> None:
        body = bytes.fromhex('000186a000000001020002000000000100000200')
        update = decode_verb_update(body)
        self.assertEqual(update.target_id, 100000)
        self.assertEqual(update.defaults[0].attribute, 2)
        self.assertEqual(update.attribute(2), 0)
        self.assertEqual(decode_verb_update(availability(100000, 2)).attribute(2), 2)
        self.assertIsNone(update.attribute(10))

    def test_multiple_verbs_preserve_mixed_endianness(self) -> None:
        body = (struct.pack('>II', 0x186a0, 2) + struct.pack('<4H', 2, 2, 2, 10)
                + struct.pack('>I', 2) + struct.pack('<4H', 0, 2, 3, 10))
        update = decode_verb_update(body)
        self.assertEqual(update.attribute(2), 0)
        self.assertEqual(update.attribute(10), 3)

    def test_malformed_or_ambiguous_updates_are_rejected(self) -> None:
        valid = availability(100000, 0)
        invalid = [valid[:-1], valid + b'\0', availability(0, 0),
                   struct.pack('>II', 100000, 65), availability(100000, 0, 0),
                   struct.pack('>II', 100000, 2) + struct.pack('<4H', 2, 2, 2, 2)
                   + struct.pack('>I', 0)]
        for body in invalid:
            with self.subTest(body=body.hex()), self.assertRaises(ValueError):
                decode_verb_update(body)


if __name__ == '__main__':
    unittest.main()
