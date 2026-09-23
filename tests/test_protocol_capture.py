import struct
import unittest
from protocol.decode_capture import chat_fields, decode_datagram, udp


def sample():
    body = struct.pack('<II', 3, 0)
    for text in (b'Test', b'Test', b'', b'Channel', b'hello'):
        body += struct.pack('<H', len(text)) + text
    body += bytes(6)
    inner = struct.pack('<HH', len(body)+4, 0xa5) + body
    return struct.pack('<HHII', len(inner)+12, 0x2016, 123, 9) + inner


class CaptureDecodeTests(unittest.TestCase):
    def test_chat_and_envelope(self):
        record = decode_datagram(sample())
        self.assertEqual(record['sequence'], 9)
        chat = record['frames'][0]['chat']
        self.assertEqual(chat['message'], 'hello')
        self.assertEqual(chat['channel'], 'Channel')
        self.assertEqual(chat['trailer_hex'], '000000000000')

    def test_each_truncation_rejected(self):
        data = sample()
        for end in range(len(data)):
            with self.assertRaises(ValueError):
                decode_datagram(data[:end])

    def test_bad_inner_length(self):
        data = bytearray(sample())
        struct.pack_into('<H', data, 12, 65535)
        with self.assertRaisesRegex(ValueError, 'inner length'):
            decode_datagram(data)

    def test_bad_string_length(self):
        with self.assertRaisesRegex(ValueError, 'chat string'):
            chat_fields(bytes(8) + b'\xff\xff')

    def test_short_ethernet(self):
        self.assertIsNone(udp(bytes(10)))


if __name__ == '__main__':
    unittest.main()
