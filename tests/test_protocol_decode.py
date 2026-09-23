import struct
import unittest

from protocol.decode_stream import FrameDecoder, RC4


class ProtocolDecodeTests(unittest.TestCase):
    def test_known_rc4_vector(self):
        self.assertEqual(RC4(b"Key").transform(b"Plaintext").hex(), "bbf316e8d940af0ad3")

    def test_rc4_state_spans_calls(self):
        cipher = RC4(b"Key")
        self.assertEqual((cipher.transform(b"Plain") + cipher.transform(b"text")).hex(), "bbf316e8d940af0ad3")

    def test_encrypted_frames_survive_every_split(self):
        plain = struct.pack("<HH", 9, 0x1234) + b"hello" + struct.pack("<HH", 4, 7)
        encrypted = RC4(bytes(8)).transform(plain)
        for split in range(len(encrypted) + 1):
            decoder = FrameDecoder(bytes(8))
            frames = decoder.feed(encrypted[:split]) + decoder.feed(encrypted[split:])
            decoder.finish()
            self.assertEqual([f["opcode"] for f in frames], ["0x1234", "0x0007"])
            self.assertEqual([f["offset"] for f in frames], [0, 9])
            self.assertEqual(frames[0]["ascii_preview"], "hello")

    def test_plaintext_bytewise(self):
        decoder = FrameDecoder()
        frames = []
        for value in b"\x06\x00\x02\x01\x00\xff":
            frames.extend(decoder.feed(bytes([value])))
        decoder.finish()
        self.assertEqual(frames[0]["payload_hex"], "00ff")

    def test_invalid_length(self):
        with self.assertRaisesRegex(ValueError, "Invalid frame length"):
            FrameDecoder().feed(b"\x03\x00\x00\x00")

    def test_truncation(self):
        for partial in (b"\x08", b"\x08\x00\x01\x00hello"[:7]):
            decoder = FrameDecoder()
            decoder.feed(partial)
            with self.assertRaisesRegex(ValueError, "Truncated"):
                decoder.finish()


if __name__ == "__main__":
    unittest.main()
