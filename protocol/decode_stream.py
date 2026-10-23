"""Decode one reassembled legacy TCP direction, starting after key exchange."""
import argparse
import json
from pathlib import Path
import struct
from enb_protocol.connection import RC4


class FrameDecoder:
    """One instance per connection direction; cipher state spans frames."""
    def __init__(self, key=None):
        self.cipher = RC4(key) if key is not None else None
        self.pending = bytearray()
        self.offset = 0

    def feed(self, data):
        self.pending.extend(self.cipher.transform(data) if self.cipher else data)
        frames = []
        while len(self.pending) >= 4:
            length, opcode = struct.unpack_from("<HH", self.pending)
            if length < 4:
                raise ValueError(f"Invalid frame length {length} at stream offset {self.offset}; check key and stream alignment")
            if len(self.pending) < length:
                break
            payload = bytes(self.pending[4:length])
            frames.append({
                "offset": self.offset,
                "length": length,
                "opcode": f"0x{opcode:04x}",
                "payload_hex": payload.hex(),
                "ascii_preview": "".join(chr(b) if 32 <= b < 127 else "." for b in payload),
            })
            del self.pending[:length]
            self.offset += length
        return frames

    def finish(self):
        if self.pending:
            raise ValueError(f"Truncated or misaligned stream at offset {self.offset}: {len(self.pending)} bytes remain")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stream", type=Path, help="Raw bytes of one ordered TCP direction, NOT a pcap file")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plaintext", action="store_true")
    mode.add_argument("--key-file", type=Path, help="File containing exactly eight raw key bytes")
    mode.add_argument("--client-zero-key", action="store_true", help="Use the zero key observed in DoClientKeyExchange")
    parser.add_argument("--skip", type=int, default=0, help="Skip handshake bytes before initializing RC4")
    args = parser.parse_args()
    try:
        if args.skip < 0:
            raise ValueError("--skip must be nonnegative")
        key = args.key_file.read_bytes() if args.key_file else bytes(8) if args.client_zero_key else None
        if key is not None and len(key) != 8:
            raise ValueError("This proxy path requires exactly eight raw key bytes")
        decoder = FrameDecoder(key)
        with args.stream.open("rb") as stream:
            if args.skip > args.stream.stat().st_size:
                raise ValueError("--skip exceeds the stream size")
            stream.seek(args.skip)
            while chunk := stream.read(65536):
                for frame in decoder.feed(chunk):
                    print(json.dumps(frame))
        decoder.finish()
    except (OSError, ValueError) as error:
        parser.exit(1, f"Decode failed: {error}\n")


if __name__ == "__main__":
    main()
