"""Persistent observations from the currently decoded Net-7 UDP message layouts."""
import math
import re
import struct
import time

from protocol.decode_capture import decode_datagram


class WorldTracker:
    def __init__(self):
        self.objects = {}
        self.player = None
        self.token = None
        self.datagrams = 0
        self.errors = 0
        self.started = time.time()
        self.level_evidence = []
        self.process_identity = []

    def object(self, identity, now, historical):
        key = f'0x{identity:08x}'
        item = self.objects.setdefault(key, {'id': key, 'name': None, 'kind': 'unknown', 'first_seen': now})
        item.update(last_seen=now, historical=historical, removal_observed=False)
        return item

    def apply(self, data, outgoing, now=None, historical=False):
        now = time.time() if now is None else now
        try:
            record = decode_datagram(data)
            self.datagrams += 1
            if outgoing:
                if len(data) >= 12:
                    token = struct.unpack_from('<I', data, 4)[0]
                    if self.token is not None and token != self.token:
                        self.objects.clear()
                        self.player = None
                    self.token = token
                if record['opcode'] == '0x1004' and len(data) in (28, 40):
                    xyz = struct.unpack_from('<fff', data, 12)
                    if all(math.isfinite(v) for v in xyz):
                        self.player = {'id': f'0x{self.token:08x}', 'raw_xyz': list(xyz), 'last_seen': now, 'historical': historical}
                return
            for frame in record.get('frames', []):
                body = bytes.fromhex(frame.get('body_hex', ''))
                if frame['opcode'] == '0x2019' and len(body) >= 48:
                    identity = struct.unpack_from('<I', body)[0]
                    length = struct.unpack_from('<H', body, 46)[0]
                    if 48 + length != len(body):
                        continue
                    name = body[48:].decode('ascii', errors='backslashreplace')
                    item = self.object(identity, now, historical)
                    kind = 'gas_cloud' if 'gas cloud' in name.lower() else 'asteroid' if 'asteroid' in name.lower() else 'named_object'
                    item.update(name=name, kind=kind, type_id=struct.unpack_from('<H', body, 4)[0])
                    item.setdefault('level', None)
                    item.setdefault('level_source', 'unknown')
                    xyz = struct.unpack_from('<fff', body, 18)
                    if all(math.isfinite(v) for v in xyz):
                        item.update(raw_xyz=list(xyz), position_seen=now)
                elif frame['opcode'] == '0x003e' and len(body) == 66 and body[:2] == b'\x2f\x01':
                    identity = struct.unpack_from('<I', body, 2)[0]
                    xyz = struct.unpack_from('<fff', body, 10)
                    if not all(math.isfinite(v) for v in xyz):
                        continue
                    if self.token == identity:
                        self.player = {'id': f'0x{identity:08x}', 'raw_xyz': list(xyz), 'last_seen': now, 'historical': historical}
                    else:
                        item = self.object(identity, now, historical)
                        item.update(raw_xyz=list(xyz), position_seen=now)
                elif frame['opcode'] == '0x001b':
                    # Observed target-label layout embeds object ID, uint16 byte length,
                    # then ASCII "Level N". Require the full observed prefix before linking.
                    prefix = bytes.fromhex('010200008001010000')
                    if len(body) >= 23 and body[6:15] == prefix:
                        identity = struct.unpack_from('<I', body, 15)[0]
                        length = struct.unpack_from('<H', body, 19)[0]
                        label = body[21:21+length]
                        match = re.fullmatch(rb'Level ([0-9]{1,3})', label)
                        if match and len(label) == length:
                            item = self.object(identity, now, historical)
                            item.update(level=int(match[1]), level_source='explicit_target_label', level_seen=now)
                elif frame['opcode'] == '0x0007' and len(body) == 4:
                    key = f'0x{struct.unpack("<I", body)[0]:08x}'
                    if key in self.objects:
                        self.objects[key].update(removal_observed=True, removal_seen=now)
        except (ValueError, struct.error):
            self.errors += 1

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        objects = []
        for original in self.objects.values():
            if now - original['last_seen'] > 30:
                continue
            if self.player and original['id'] == self.player['id']:
                continue
            item = dict(original)
            evidence = [e for e in self.level_evidence if e.get('type_id') == item.get('type_id') and e.get('name') == item.get('name')]
            levels = {e['level'] for e in evidence}
            exact = [e for e in evidence if e['object_id'] == item['id'] and e.get('processes') == self.process_identity]
            if exact:
                item.update(level=exact[-1]['level'], level_source='user_confirmed_selection')
            elif item.get('level') is None and evidence:
                item['inferred_level'] = next(iter(levels)) if len(levels) == 1 else None
                item['level_inference'] = {'basis': 'shared_type_and_name', 'confidence': 'low' if len(levels) == 1 else 'conflicting',
                                           'confirmed_sample_count': len({e['object_id'] for e in evidence})}
            item['age_seconds'] = max(0, now - item['last_seen'])
            item['status'] = ('removal_observed' if item.get('removal_observed') else
                              'historical' if item.get('historical') else
                              'recent')
            if 'raw_xyz' in item:
                item['map_xyz'] = [v / 1000 for v in item['raw_xyz']]
                item['position_age_seconds'] = max(0, now - item['position_seen'])
                if self.player:
                    item['distance_map_units'] = math.dist(self.player['raw_xyz'], item['raw_xyz']) / 1000
            objects.append(item)
        objects.sort(key=lambda item: (-item['last_seen'], item['id']))
        player = dict(self.player) if self.player else None
        if player:
            player['map_xyz'] = [v / 1000 for v in player['raw_xyz']]
            player['age_seconds'] = max(0, now - player['last_seen'])
        return {'updated': now, 'started': self.started, 'token': self.token, 'datagrams': self.datagrams, 'decode_errors': self.errors,
                'player': player, 'objects': objects,
                'counts': {'remembered_asteroids': sum(x['kind'] == 'asteroid' for x in objects),
                           'recent_asteroids': sum(x['kind'] == 'asteroid' and x['status'] == 'recent' for x in objects),
                           'objects': len(objects)},
                'object_window_seconds': 30, 'object_order': 'newest_first',
                'coverage': 'Only objects seen within 30 seconds are shown. Not a complete visible-object or enemy census. Expiry from this dump does not prove removal. Opcode 0x0007 is a removal candidate. Distances use latest non-simultaneous positions.'}
