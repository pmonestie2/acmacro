"""Conservative decoder for captured player AUX mission-list updates (0x001b).

Layout: Net7 AuxPlayerIndex/AuxMissions/AuxMission, checked against captures.
Only normal mission updates with a supported prefix are accepted. Missing
baselines, unsupported mission updates and malformed data invalidate the count.
"""
import struct

from protocol.decode_capture import decode_datagram


class Reader:
    def __init__(self, body, offset=0):
        self.body, self.offset = body, offset

    def take(self, size):
        end = self.offset + size
        if end > len(self.body):
            raise ValueError('Truncated mission AUX')
        value = self.body[self.offset:end]
        self.offset = end
        return value

    def number(self, size):
        return int.from_bytes(self.take(size), 'little')

    def string(self):
        size = self.number(2)
        if size > 4096:
            raise ValueError('Oversized mission string')
        return self.take(size).decode('cp1252')

    def flags(self, size, fields):
        bits = self.number(size)
        if bits & 15 != 2 or bits >> (fields + 4):
            raise ValueError('Unsupported mission AUX flags')
        return bits >> 4


FIELDS = ('id', 'name', 'summary', 'reward', 'failure', 'faction', 'timed',
          'expiration', 'start', 'forfeitable', 'completed', 'failed', 'expired',
          'visible', 'stage_count', 'stage', 'stages', 'stage_expiration', 'new_message')
BOOLS = {6, 9, 10, 11, 12, 13, 18}


def mission(reader):
    fields = reader.flags(3, 19)
    result = {}
    for index, name in enumerate(FIELDS):
        if not fields & (1 << index):
            continue
        if 1 <= index <= 5:
            value = reader.string()
        elif index == 16:
            stages = reader.flags(3, 20)
            value = {}
            for slot in range(20):
                if stages & (1 << slot):
                    stage_fields = reader.flags(1, 2)
                    entry = {}
                    if stage_fields & 1:
                        entry['text'] = reader.string()
                    if stage_fields & 2:
                        entry['timed'] = reader.number(1)
                    value[slot] = entry
        else:
            value = reader.number(1 if index in BOOLS else 4)
            if index in BOOLS and value not in (0, 1):
                raise ValueError('Invalid mission boolean')
        result[name] = value
    return result


def decode_missions(body):
    """Return slot deltas, or None when this AUX is not a player mission update."""
    if len(body) < 10 or body[:4] != bytes(4) or body[6] != 1:
        return None
    # The captured proxy fills the AUX payload-size short at offset four.
    if not body[8] & 0x20:
        return None
    reader = Reader(body, 7)
    fields = reader.flags(3, 18)
    if reader.body[4:6] not in (b'\0\0', struct.pack('<H', len(body) - 6)):
        raise ValueError('Invalid player AUX size')
    for index in range(9):
        if not fields & (1 << index):
            continue
        if index == 0:
            reader.take(8)  # Credits.
        elif index in (1, 8):
            reader.take(4)  # XP debt / music ID.
        elif index == 7:
            reader.string()
        else:
            raise ValueError('Unsupported inventory/RPG prefix in mission AUX')
    slots = reader.flags(2, 12)
    result = {}
    for slot in range(12):
        if slots & (1 << slot):
            update = mission(reader)
            if 'id' in update and update['id'] != slot:
                raise ValueError('Mission slot/ID mismatch')
            result[slot] = update
    # Do not guess the size of unrelated fields following Missions.
    if fields >> 10 or reader.offset != len(body):
        raise ValueError('Unsupported trailing player AUX fields')
    return result


class MissionState:
    def __init__(self):
        self.slots = {}
        self.ready = False
        self.seen = 0
        self.reason = 'waiting for a complete mission snapshot'
        self.last_sequence = None
        self.session = None
        self.updates_seen = 0
        self.snapshots_seen = 0

    def invalidate(self, reason):
        self.ready = False
        self.slots.clear()
        self.reason = reason

    def observe(self, payload, outgoing, now):
        if outgoing:
            return
        packet = decode_datagram(payload)
        sequence = packet.get('sequence')
        session = packet.get('session_or_token')
        if session is not None and session != self.session:
            self.invalidate('new packet session; waiting for mission snapshot')
            self.session, self.last_sequence = session, None
        if sequence is not None and self.last_sequence is not None:
            delta = (sequence - self.last_sequence) & 0xffffffff
            if delta == 0 or delta >= 0x80000000:
                return  # Duplicate/reordered mission snapshots must not rewind state.
            if delta != 1:
                self.invalidate('packet sequence gap; waiting for mission snapshot')
        if sequence is not None:
            self.last_sequence = sequence
        frames = packet.get('frames', [packet])
        for frame in frames:
            if frame['opcode'] != '0x001b':
                continue
            body = bytes.fromhex(frame['body_hex'])
            try:
                updates = decode_missions(body)
            except (ValueError, struct.error):
                self.invalidate('unsupported or malformed mission AUX; screen fallback required')
                continue
            if updates is None:
                continue
            self.updates_seen += 1
            # Captured full lists enumerate slots 0..10, including empty IDs.
            # Accept the older source's 12-slot variant as well.
            baseline = set(updates) in (set(range(11)), set(range(12))) and all(
                value.get('id') == slot for slot, value in updates.items())
            if baseline:
                self.snapshots_seen += 1
                self.slots = {slot: {} for slot in updates}
                self.ready = True
                self.reason = ''
            for slot, update in updates.items():
                if self.ready and slot not in self.slots:
                    self.invalidate('mission slot outside captured snapshot')
                if self.ready:
                    self.slots[slot].update(update)
            self.seen = now

    def snapshot(self):
        if not self.ready:
            return None
        entries = [dict(slot=slot, **value) for slot, value in sorted(self.slots.items())
                   if value.get('name')]
        return {'count': len(entries), 'missions': entries, 'seen': self.seen,
                'source': 'packet 0x001b', 'capacity': len(self.slots)}
