import struct
import unittest
from protocol.inject_navigation import validate_frame, validate_command, build_stub, SUCCESS


class NavigationInjectionTests(unittest.TestCase):
    def test_information_request_endianness(self):
        frame = struct.pack('<HH', 16, 0x5a) + struct.pack('>II', 0x4003a74c, 0x18706) + struct.pack('<I', 1)
        self.assertEqual(validate_command(frame), (0x5a, 0x3a74c, (0x18706,)))
        with self.assertRaises(ValueError):
            validate_command(frame[:-4] + struct.pack('<I', 2))

    def test_selection_does_not_clear_navigation_flag(self):
        stub = build_stub(0x1000000, 0x2000000, 0x3000000, 0x4000000, 8, 123, 0x17)
        self.assertNotIn(b'\xc6\x05' + struct.pack('<I', 0x2000232), stub)

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


if __name__ == '__main__':
    unittest.main()
