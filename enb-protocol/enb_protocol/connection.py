"""Direct TCP RSA/RC4 connection; no proxy process or UI dependency."""
import secrets
import socket
import struct

from .wire import FRAME_HEADER, Frame, decode_frame


class RC4:
    def __init__(self, key: bytes) -> None:
        if not 1 <= len(key) <= 256:
            raise ValueError('RC4 key must contain 1 to 256 bytes')
        self.state = list(range(256))
        self.x = self.y = 0
        j = 0
        for i in range(256):
            j = (j + self.state[i] + key[i % len(key)]) & 255
            self.state[i], self.state[j] = self.state[j], self.state[i]

    def transform(self, data: bytes) -> bytes:
        result = bytearray()
        for value in data:
            self.x = (self.x + 1) & 255
            self.y = (self.y + self.state[self.x]) & 255
            self.state[self.x], self.state[self.y] = self.state[self.y], self.state[self.x]
            result.append(value ^ self.state[(self.state[self.x] + self.state[self.y]) & 255])
        return bytes(result)


def receive_exact(connection: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        part = connection.recv(size - len(result))
        if not part:
            raise EOFError('Server closed the connection')
        result.extend(part)
    return bytes(result)


def read_public_integer(connection: socket.socket) -> int:
    size = struct.unpack('!I', receive_exact(connection, 4))[0]
    if not 1 <= size <= 256:
        raise ValueError('Unsupported RSA public integer length')
    return int.from_bytes(receive_exact(connection, size), 'big')


class GameConnection:
    def __init__(self, connection: socket.socket, key: bytes) -> None:
        self.connection = connection
        self.incoming = RC4(key)
        self.outgoing = RC4(key)

    @classmethod
    def connect(cls, host: str, port: int = 3805, timeout: float = 15.0) -> 'GameConnection':
        connection = socket.create_connection((host, port), timeout)
        try:
            modulus = read_public_integer(connection)
            exponent = read_public_integer(connection)
            if modulus.bit_length() != 512 or not 1 < exponent < modulus:
                raise ValueError('Unsupported game RSA public key')
            key = secrets.token_bytes(8)
            block = int.from_bytes(bytes(56) + key[::-1], 'big')
            encrypted = pow(block, exponent, modulus).to_bytes(64, 'big')
            connection.sendall(struct.pack('!I', len(encrypted)) + encrypted)
            return cls(connection, key)
        except BaseException:
            connection.close()
            raise

    def send(self, frame: Frame) -> None:
        self.connection.sendall(self.outgoing.transform(frame.encode()))

    def receive(self) -> Frame:
        header = self.incoming.transform(receive_exact(self.connection, FRAME_HEADER.size))
        length, _ = FRAME_HEADER.unpack(header)
        if not FRAME_HEADER.size <= length <= 32767:
            raise ValueError('Invalid TCP game frame length')
        body = self.incoming.transform(receive_exact(self.connection, length - FRAME_HEADER.size))
        return decode_frame(header + body)

    def check_version(self) -> int:
        self.send(Frame(0x0000, struct.pack('!II', 42, 0)))
        reply = self.receive()
        if reply.opcode != 0x0001 or len(reply.body) != 4:
            raise ValueError('Unexpected version response')
        return struct.unpack('<I', reply.body)[0]

    def close(self) -> None:
        self.connection.close()
