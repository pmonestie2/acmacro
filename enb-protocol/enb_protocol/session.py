"""Synchronous headless space session: login, ordered world delivery, and logout."""
from datetime import datetime
import math
import socket
import struct
import threading
import time

from .login import Character, CharacterTicket, Credentials, GlobalLogin
from .wire import FRAME_HEADER, UDP_HEADER, Frame, decode_frame, encode_udp
from .world import World
from .verbs import decode_verb_update

class SectorStream:
    """AUX frames can span a 0x2016 datagram and several 0x201a continuations."""
    def __init__(self, player_id: int) -> None:
        self.player_id = player_id
        self.expected = 0
        self.waiting: dict[int, bytes] = {}
        self.pending = bytearray()

    def feed(self, packet: bytes) -> list[Frame]:
        if len(packet) < UDP_HEADER.size:
            raise ValueError('Truncated sector datagram')
        length, opcode, player_id, sequence = UDP_HEADER.unpack_from(packet)
        if length != len(packet) or player_id != self.player_id or opcode not in (0x2016, 0x201a):
            raise ValueError('Unexpected sector datagram envelope')
        if sequence < self.expected:
            return []
        if sequence - self.expected > 255:
            raise ValueError('Sector sequence gap exceeds the receive window')
        payload = packet[UDP_HEADER.size:]
        if sequence in self.waiting and self.waiting[sequence] != payload:
            raise ValueError('Conflicting duplicate sector sequence')
        self.waiting[sequence] = payload
        frames: list[Frame] = []
        while self.expected in self.waiting:
            self.pending.extend(self.waiting.pop(self.expected))
            self.expected += 1
            while len(self.pending) >= FRAME_HEADER.size:
                size, _ = FRAME_HEADER.unpack_from(self.pending)
                if not FRAME_HEADER.size <= size <= 32767:
                    raise ValueError('Invalid reassembled sector frame length')
                if len(self.pending) < size:
                    break
                frames.append(decode_frame(bytes(self.pending[:size])))
                del self.pending[:size]
        return frames


def login_body(ticket: CharacterTicket, tick: int) -> bytes:
    timestamp = datetime.now().strftime('%m/%d/%y %H:%M:%S').encode('ascii') + b'\0'
    # The server consumes join_data and TimeSent. No client memory or old ticket
    # is reused; the legacy LoginData reserved bytes are zero-initialized.
    return ticket.join_data + struct.pack('<I', tick) + bytes(40) + timestamp + bytes(7) + bytes(4)


class SpaceSession:
    def __init__(self, host: str, ticket: CharacterTicket, character: Character,
                 sector: socket.socket, comms: socket.socket, sector_port: int) -> None:
        self.host = host
        self.ticket = ticket
        self.character = character
        self.sector = sector
        self.comms = comms
        self.sector_port = sector_port
        self.stream = SectorStream(ticket.player_id)
        self.world = World()
        self.position: tuple[float, float, float] | None = None
        self.sequence = 1
        self.started = False
        self.station_layout: bytes | None = None
        self.closed = False
        self.received_at = time.monotonic()
        self.last_keepalive = time.monotonic()
        self.last_resend = 0.0
        self.packets = 0
        self.frames = 0
        self.navigation_status = ''
        self.gate_target: int | None = None
        self.gate_selected = False
        self.gate_submitted_at = 0.0
        self.gate_camera = False
        self.gate_finished = False
        self.pending_handoff: CharacterTicket | None = None
        self.cancel = threading.Event()

    def navigate(self, target: int, mode: str) -> None:
        item = self.world.objects.get(target)
        if item is None or not item.navigation_present or not self.started:
            raise ValueError('Target is not a navigable object in the current live sector')
        if self.gate_target is not None:
            raise RuntimeError('A gate transition is already pending')
        if mode == 'warp':
            self.send(0x009b, struct.pack('<IHI', self.ticket.player_id & 0xffffff, 1, target))
            self.navigation_status = f'Warp requested: {item.name}'
        elif mode == 'gate' and item.kind == 'gate':
            self.gate_target = target
            self.gate_selected = False
            self.gate_camera = self.gate_finished = False
            self.gate_submitted_at = time.monotonic()
            self.send(0x17, struct.pack('<II', self.ticket.player_id, target))
            self.send(0x5a, struct.pack('>II', self.ticket.player_id, target) + struct.pack('<I', 1))
            self.navigation_status = f'Selecting gate: {item.name}'
        else:
            raise ValueError('Choose warp, or a gate object for sector travel')

    def activate_gate(self) -> None:
        if self.gate_target is None or self.gate_selected:
            return
        self.gate_selected = True
        self.gate_submitted_at = time.monotonic()
        self.send(0x2c, struct.pack('<4I', self.ticket.player_id, 18, self.gate_target, 0))
        self.navigation_status = 'Gate activated; waiting for the gate sequence'

    def enter_handoff(self, ticket: CharacterTicket) -> None:
        self.navigation_status = 'Changing sectors; receiving destination map'
        address = socket.gethostbyname(self.host)
        self.sector.connect((address, 3808))
        self.sector.settimeout(.25)
        self.sector.send(encode_udp(0x2008, ticket.player_id, struct.pack('<IB', ticket.sector_id, 1)))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not self.cancel.is_set():
            try:
                reply = self.sector.recv(65535)
            except socket.timeout:
                continue
            if len(reply) < 22:
                continue
            length, opcode, owner, _ = UDP_HEADER.unpack_from(reply)
            if opcode != 0x2009:
                continue
            if length != len(reply) or owner != ticket.player_id or struct.unpack_from('<I', reply, 18)[0] != owner:
                raise ValueError('Destination handoff identity mismatch')
            port = struct.unpack_from('<H', reply, 16)[0]
            if not port:
                raise ValueError('Destination handoff has no sector port')
            self.sector.connect((address, port))
            self.ticket, self.sector_port = ticket, port
            self.sequence = 1
            self.stream = SectorStream(ticket.player_id)
            self.world = World()
            self.station_layout = None
            self.position = None
            self.started = False
            self.gate_target = None
            self.pending_handoff = None
            self.send(2, login_body(ticket, int(time.monotonic() * 1000) & 0xffffffff))
            return
        if self.cancel.is_set():
            return
        raise TimeoutError('Destination sector handoff timed out; no retry sent')

    @classmethod
    def login(cls, host: str, credentials: Credentials, slot: int,
              certificate_sha256: str | None = None) -> 'SpaceSession':
        global_login, characters = GlobalLogin.open(host, credentials, certificate_sha256)
        return cls.enter(host, global_login, characters, slot)

    @classmethod
    def enter(cls, host: str, global_login: GlobalLogin, characters: list[Character], slot: int,
              *, allow_station: bool = False) -> 'SpaceSession':
        """Consume a freshly authenticated global connection after UI selection."""
        sector: socket.socket | None = None
        comms: socket.socket | None = None
        ticket: CharacterTicket | None = None
        try:
            sector = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            comms = global_login.take_comms()
            character = next((item for item in characters if item.slot == slot), None)
            if character is None:
                raise ValueError('Requested character slot is empty')
            if character.sector_id >= 10000 and not allow_station:
                raise ValueError('Character is inside a station; space login is required for this milestone')
            address = socket.gethostbyname(host)
            ticket = global_login.select(slot)
            comms.send(encode_udp(0x200f, ticket.player_id))
            sector.settimeout(15)
            sector.connect((address, 3808))
            sector.send(encode_udp(0x2008, ticket.player_id, struct.pack('<IB', ticket.sector_id, 1)))
            handoff = sector.recv(65535)
            length, opcode, owner, _ = UDP_HEADER.unpack_from(handoff)
            if length != len(handoff) or opcode != 0x2009 or owner != ticket.player_id or len(handoff) < 22:
                raise ValueError('Invalid master handoff reply')
            port = struct.unpack_from('<H', handoff, 16)[0]
            if not port or struct.unpack_from('<I', handoff, 18)[0] != ticket.player_id:
                raise ValueError('Sector handoff identity did not match the selected character')
            # Live handoffs advertise a private backend address. The observed
            # public endpoint stays on the same game host and the returned port.
            sector.connect((address, port))
            session = cls(host, ticket, character, sector, comms, port)
            session.send(2, login_body(ticket, int(time.monotonic() * 1000) & 0xffffffff))
            sector.settimeout(.25)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                session.poll()
                if session.started and ((allow_station and character.sector_id >= 10000 and session.station_layout is not None)
                        or (session.world.sector and session.world.navigable())):
                    return session
            raise TimeoutError('Sector login did not complete with a navigable map within 30 seconds')
        except BaseException:
            if comms is not None and ticket is not None:
                try:
                    comms.send(encode_udp(0x1008, ticket.player_id, struct.pack('<I', ticket.player_id)))
                except OSError:
                    pass
            if sector is not None:
                sector.close()
            if comms is not None:
                comms.close()
            raise
        finally:
            global_login.close()

    def send(self, opcode: int, body: bytes = b'') -> None:
        if self.closed:
            raise RuntimeError('Space session is closed')
        self.sector.send(encode_udp(opcode, self.ticket.player_id, body, self.sequence))
        self.sequence += 1

    def apply_frame(self, frame: Frame) -> None:
        self.frames += 1
        self.world.apply_frame(frame)
        if frame.opcode == 0x52:
            self.station_layout = frame.body
        elif frame.opcode == 0x2020 and len(frame.body) == 4:
            self.send(0x2021, frame.body)
        elif frame.opcode == 5 and len(frame.body) == 4:
            self.send(6, frame.body)
            self.send(0x3004, struct.pack('<I', self.ticket.player_id))
            self.started = True
            if self.navigation_status:
                self.navigation_status = f'Arrived in {self.world.sector or "destination sector"}'
        elif frame.opcode == 0x003e and len(frame.body) >= 38:
            identity = struct.unpack_from('<I', frame.body, 2)[0]
            if identity == self.ticket.player_id:
                position = struct.unpack_from('<fff', frame.body, 10)
                if all(math.isfinite(value) for value in position):
                    self.position = (position[0], position[1], position[2])
        elif frame.opcode == 0x0019 and self.gate_target is not None and len(frame.body) == 8:
            target, other = struct.unpack('<II', frame.body)
            if target == self.gate_target and other == 0xffffffff:
                self.activate_gate()
        elif frame.opcode == 0x005c and self.gate_target is not None:
            update = decode_verb_update(frame.body)
            if update.target_id == self.gate_target:
                self.activate_gate()
        elif frame.opcode == 0x0092 and len(frame.body) == 8 and self.gate_target is not None:
            camera, target = struct.unpack('>II', frame.body)
            if camera == 4 and target == self.gate_target:
                self.gate_camera = True
        elif frame.opcode == 0x001d and len(frame.body) > 3:
            message = frame.body[3:].split(b'\0', 1)[0].decode('cp1252', errors='replace')
            if message:
                self.navigation_status = message
                if any(term in message.lower() for term in ('cannot gate', 'unable to gate', 'gate inoperative', 'gate malfunction', 'sector is offline')):
                    self.gate_target = None
        elif frame.opcode == 0x003a:
            if len(frame.body) < 64:
                raise ValueError('Truncated destination handoff')
            player_id, sector_id = struct.unpack_from('!II', frame.body, 16)
            if player_id != self.ticket.player_id or not sector_id or sector_id >= 10000:
                raise ValueError('Handoff is not a valid space sector for this character')
            self.pending_handoff = CharacterTicket(player_id, sector_id, frame.body[:64])
        elif frame.opcode == 0x00ba:
            raise ConnectionError('Server ended the character session')

    def poll(self) -> list[Frame]:
        """Advance the session and return decoded frames for event consumers."""
        received: list[Frame] = []
        try:
            packet = self.sector.recv(65535)
        except socket.timeout:
            packet = b''
        now = time.monotonic()
        if packet:
            self.packets += 1
            self.received_at = now
            opcode = struct.unpack_from('<H', packet, 2)[0] if len(packet) >= 4 else -1
            if opcode in (0x2016, 0x201a):
                for frame in self.stream.feed(packet):
                    received.append(frame)
                    self.apply_frame(frame)
                    if self.pending_handoff is not None:
                        break
        if self.pending_handoff is not None:
            self.enter_handoff(self.pending_handoff)
            return received
        if self.gate_target is not None:
            elapsed = now - self.gate_submitted_at
            if self.gate_selected and self.gate_camera and elapsed >= 15 and not self.gate_finished:
                self.send(0x2c, struct.pack('<4I', self.ticket.player_id, 19, self.gate_target, 0))
                self.gate_finished = True
                self.navigation_status = 'Gate sequence complete; waiting for sector handoff'
            elif elapsed > (45 if self.gate_selected else 5):
                self.gate_target = None
                self.navigation_status = 'Gate did not complete; no activation retry sent'
        if self.stream.waiting and now - self.last_resend >= .25:
            count = min(self.stream.waiting) - self.stream.expected
            if count > 0:
                self.send(0x2017, struct.pack('<II', self.stream.expected, count))
                self.last_resend = now
        if now - self.last_keepalive >= 15:
            self.send(0x3005)
            self.comms.send(encode_udp(0x3005, self.ticket.player_id))
            self.last_keepalive = now
        if now - self.received_at > 45:
            raise TimeoutError('No sector packets received for 45 seconds')
        return received

    def close(self, *, logout: bool = True) -> None:
        if self.closed:
            return
        try:
            if logout:
                self.comms.send(encode_udp(0x1008, self.ticket.player_id, struct.pack('<I', self.ticket.player_id)))
        finally:
            self.closed = True
            self.sector.close()
            self.comms.close()
