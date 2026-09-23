import unittest
from protocol.extract_client_trace import extract, decode_route


class ClientTraceTests(unittest.TestCase):
    def trace(self, body='01 00 00 00 01 00 AB 86 01 00'):
        lines = ['Received 4 byte header (encrypted):', 'AA BB CC DD',
                 'Received 4 byte header (decrypted):', '0E 00 9B 00',
                 'Received 10 byte packet', body]
        return '\n'.join('22/09/26 16:32:01 ' + line for line in lines)

    def test_route(self):
        frames = extract(self.trace())
        self.assertEqual(frames[0]['route']['waypoint_ids'], ['0x000186ab'])
        self.assertEqual(frames[0]['encrypted_header_hex'], 'aabbccdd')

    def test_truncated_dump(self):
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            extract(self.trace('01 00'))

    def test_bad_count(self):
        with self.assertRaisesRegex(ValueError, 'count'):
            decode_route(bytes.fromhex('010000000200ab860100'))

    def test_wrong_body_size(self):
        with self.assertRaisesRegex(ValueError, 'size'):
            extract(self.trace().replace('Received 10 byte packet', 'Received 9 byte packet'))


if __name__ == '__main__':
    unittest.main()
