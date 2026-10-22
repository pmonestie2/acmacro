"""Bounded IPv4 reassembly for read-only raw-socket capture."""
import struct
import time


class IPv4Reassembler:
    def __init__(self, ttl=5, max_pending=256):
        self.ttl, self.max_pending = ttl, max_pending
        self.pending = {}
        self.completed = self.rejected = self.expired = 0

    def feed(self, packet, now=None):
        now = time.monotonic() if now is None else now
        for key in list(self.pending):
            if now-self.pending[key]['started'] > self.ttl:
                self.pending.pop(key)
                self.expired += 1
        if len(packet) < 20 or packet[0] >> 4 != 4:
            return None
        size = (packet[0]&15)*4
        total, identity, flags = struct.unpack_from('!HHH',packet,2)
        if size < 20 or total < size or total > len(packet):
            return None
        packet = packet[:total]
        if not flags & 0x3fff:
            return packet
        offset = (flags&0x1fff)*8
        more = bool(flags&0x2000)
        payload = packet[size:]
        key = (packet[12:16],packet[16:20],packet[9],identity)
        if not payload or offset+len(payload)>65515 or (more and len(payload)%8):
            self.pending.pop(key,None); self.rejected += 1
            return None
        if key not in self.pending:
            if len(self.pending) >= self.max_pending:
                self.pending.pop(min(self.pending,key=lambda k:self.pending[k]['started']))
                self.expired += 1
            self.pending[key] = {'started':now,'chunks':{},'header':None,'length':None}
        item = self.pending[key]
        for start, data in item['chunks'].items():
            if start == offset and data == payload:
                break
            if max(start,offset)<min(start+len(data),offset+len(payload)):
                self.pending.pop(key); self.rejected += 1
                return None
        item['chunks'][offset] = payload
        if offset == 0:
            item['header'] = packet[:size]
        if not more:
            length = offset+len(payload)
            if item['length'] is not None and item['length'] != length:
                self.pending.pop(key); self.rejected += 1
                return None
            item['length'] = length
        if item['header'] is None or item['length'] is None:
            return None
        cursor, parts = 0, []
        for start,data in sorted(item['chunks'].items()):
            if start != cursor:
                return None
            cursor += len(data); parts.append(data)
        if cursor != item['length']:
            return None
        header = bytearray(item['header'])
        struct.pack_into('!HH',header,2,len(header)+cursor,identity)
        struct.pack_into('!H',header,6,0)
        struct.pack_into('!H',header,10,0)
        self.pending.pop(key)
        self.completed += 1
        return bytes(header)+b''.join(parts)
