"""Fresh account authentication and character selection on the global server."""
from dataclasses import dataclass, field
import hashlib
import hmac
import http.client
import ssl
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request

from .connection import GameConnection
from .wire import Frame, UDP_HEADER, encode_udp

PROXY_VERSION = 246


def register_comms(host: str) -> socket.socket:
    connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        connection.settimeout(10)
        connection.connect((socket.gethostbyname(host), 3806))
        connection.send(encode_udp(0x1000, 0, struct.pack('<I', PROXY_VERSION)))
        reply = connection.recv(1024)
        if len(reply) < UDP_HEADER.size:
            raise ValueError('Truncated UDP registration reply')
        size, opcode, owner, _ = UDP_HEADER.unpack_from(reply)
        if size != len(reply) or opcode != 0x1001 or owner != 0:
            raise ValueError('Game UDP registration was rejected')
        return connection
    except BaseException:
        connection.close()
        raise


def global_error(body: bytes) -> str:
    """Decode the server's 0x0075 explanation without dumping account packets."""
    if len(body) < 8:
        return 'Global server rejected login with a truncated error reply'
    length = struct.unpack_from('<I', body)[0]
    code = struct.unpack_from('!I', body, 4)[0]
    if length > len(body) - 8 or length > 2048:
        return f'Global server rejected login (code {code}; invalid error length)'
    message = body[8:8 + length].decode('cp1252', errors='replace').strip('\0\r\n ')
    return f'{message or "Global server rejected login"} (server code {code})'


def ticket_frame(ticket: str) -> Frame:
    encoded = ticket.encode('ascii')
    if not encoded or len(encoded) > 512 or b'\0' in encoded:
        raise ValueError('Invalid authentication ticket format')
    # Observed client: little-endian byte count INCLUDING its terminating NUL.
    # The terminator matters: the legacy server reads this field as a C string.
    terminated = encoded + b'\0'
    return Frame(0x006d, struct.pack('<I', len(terminated)) + terminated)


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class Character:
    slot: int
    name: str
    sector_id: int
    location: str


@dataclass(frozen=True)
class CharacterTicket:
    player_id: int
    sector_id: int
    join_data: bytes = field(repr=False)


def authenticate(host: str, credentials: Credentials, certificate_sha256: str | None = None) -> str:
    query = urllib.parse.urlencode({'username': credentials.username, 'password': credentials.password,
                                  'serviceID': '2184', 'version': '2.5'})
    try:
        if certificate_sha256 is None:
            request = urllib.request.Request(f'https://{host}/AuthLogin?{query}', headers={'User-Agent': 'AuthLogin'})
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read(8193)
        else:
            # Legacy service certificates can be expired. An explicit certificate
            # pin is checked before the credential-bearing request is sent.
            expected = bytes.fromhex(certificate_sha256)
            if len(expected) != 32:
                raise ValueError('Certificate SHA256 must contain 64 hexadecimal characters')
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            connection = http.client.HTTPSConnection(host, timeout=15, context=context)
            try:
                connection.connect()
                if connection.sock is None:
                    raise ConnectionError('HTTPS connection is unavailable')
                certificate = connection.sock.getpeercert(binary_form=True)
                if certificate is None or not hmac.compare_digest(hashlib.sha256(certificate).digest(), expected):
                    raise ValueError('Game authentication certificate does not match its configured pin')
                connection.request('GET', f'/AuthLogin?{query}', headers={'User-Agent': 'AuthLogin'})
                response = connection.getresponse()
                if response.status != 200:
                    raise ConnectionError('Game authentication returned an HTTP error')
                data = response.read(8193)
            finally:
                connection.close()
    except (OSError, urllib.error.URLError):
        # urllib exception messages can include the credential-bearing URL.
        raise ConnectionError('HTTPS account authentication failed') from None
    if len(data) > 8192:
        raise ValueError('Oversized account authentication response')
    fields = dict(line.split('=', 1) for line in data.decode('ascii').splitlines() if '=' in line)
    if fields.get('Valid', '').lower() != 'true' or not fields.get('Ticket'):
        raise ValueError('Account authentication was rejected')
    return fields['Ticket']


def decode_characters(body: bytes) -> list[Character]:
    if len(body) < 1874:
        raise ValueError('Truncated character list')
    result: list[Character] = []
    for index in range(5):
        start = index * 374
        slot, sector = struct.unpack_from('!II', body, start)
        name = body[start + 133:start + 153].split(b'\0', 1)[0].decode('ascii')
        location = body[start + 52:start + 133].split(b'\0', 1)[0].decode('ascii')
        if name:
            if not 0 <= slot <= 4:
                raise ValueError('Invalid character slot in server reply')
            result.append(Character(slot, name, sector, location))
    return result


class GlobalLogin:
    def __init__(self, connection: GameConnection, comms: socket.socket | None = None) -> None:
        self.connection = connection
        self.comms = comms

    @classmethod
    def open(cls, host: str, credentials: Credentials, certificate_sha256: str | None = None) -> tuple['GlobalLogin', list[Character]]:
        comms = register_comms(host)
        connection: GameConnection | None = None
        try:
            ticket = authenticate(host, credentials, certificate_sha256)
            connection = GameConnection.connect(host)
            if connection.check_version() != 0:
                raise ValueError('Game version was rejected')
            connection.send(ticket_frame(ticket))
            reply = connection.receive()
            if reply.opcode == 0x0075:
                raise ValueError(global_error(reply.body))
            if reply.opcode != 0x0070:
                raise ValueError(f'Character list rejected: reply=0x{reply.opcode:04x}')
            return cls(connection, comms), decode_characters(reply.body)
        except BaseException:
            if connection is not None:
                connection.close()
            comms.close()
            raise

    def take_comms(self) -> socket.socket:
        if self.comms is None:
            raise RuntimeError('Global login no longer owns a registered communication socket')
        connection = self.comms
        self.comms = None
        return connection

    def select(self, slot: int) -> CharacterTicket:
        if not 0 <= slot <= 4:
            raise ValueError('Character slot must be between 0 and 4')
        self.connection.send(Frame(0x006e, struct.pack('!I', slot)))
        reply = self.connection.receive()
        if reply.opcode == 0x0075:
            raise ValueError(global_error(reply.body))
        if reply.opcode != 0x006f or len(reply.body) != 68:
            raise ValueError(f'Character selection rejected: reply=0x{reply.opcode:04x}')
        if struct.unpack_from('!I', reply.body)[0] != 0:
            raise ValueError('Character login was rejected by the server')
        join = reply.body[4:]
        player_id, sector_id = struct.unpack_from('!II', join, 16)
        if not player_id or not sector_id:
            raise ValueError('Character ticket is missing its player or sector identity')
        return CharacterTicket(player_id, sector_id, join)

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            if self.comms is not None:
                self.comms.close()
                self.comms = None
