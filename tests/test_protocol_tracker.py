import struct
import unittest
from protocol.world_tracker import WorldTracker, sector_name_201a


def wrap(opcode, body):
    inner = struct.pack('<HH', len(body)+4, opcode) + body
    return struct.pack('<HHII', len(inner)+12, 0x2016, 123, 1) + inner


def asteroid():
    body = bytearray(48)
    struct.pack_into('<I', body, 0, 99)
    struct.pack_into('<fff', body, 18, 3000, 4000, 0)
    struct.pack_into('<H', body, 46, 8)
    return wrap(0x2019, body + b'Asteroid')


class TrackerTests(unittest.TestCase):
    def test_avatar_update_identifies_pluto_and_clears_previous_sector(self):
        # Observed final sector field and trailer from the JE's Pluto entry.
        body = struct.pack('<II', 0x4003c204, 6) + b'\x05' * 20
        body += bytes.fromhex('1000506c75746f20616e6420436861726f6e32040000f607c00300000101010000')
        tracker = WorldTracker()
        tracker.sector = "Akeron's Gate"
        tracker.objects['old'] = {'name': 'Previous sector nav'}
        tracker.player = {'raw_xyz': [1, 2, 3]}
        packet = struct.pack('<HH', len(body) + 4, 0x201a) + body
        tracker.apply(packet, False, 10)
        self.assertEqual(tracker.sector, 'Pluto and Charon')
        self.assertEqual(tracker.objects, {})
        self.assertIsNone(tracker.player)
        for invalid in (body[:-1], body[:30], body.replace(b'Pluto', b'\x00luto')):
            self.assertIsNone(sector_name_201a(invalid))

    def test_short_npc_movement_layout_keeps_coordinates(self):
        tracker=WorldTracker()
        tracker.sector='Saturn'
        body=bytearray(54); body[:2]=b'\x28\x01'
        struct.pack_into('<I',body,2,0x18744)
        struct.pack_into('<fff',body,10,89810,-467480,1400)
        tracker.apply(wrap(0x003e,body),outgoing=False,now=100)
        obj=tracker.objects['0x00018744']
        self.assertEqual(obj['raw_xyz'],[89810,-467480,1400])
        self.assertEqual(obj['sector'],'Saturn')
        self.assertEqual(obj['hostility'],'unknown')
    def test_ship_spawn_identifies_vrix_without_guessing_hostility(self):
        body = bytes.fromhex('498701003c00011604004400000c80c300ae40f8c0070b0056277269782053636f757403ac48af1fa99f43370000803f010400000005050505050000000000000000')
        tracker = WorldTracker()
        tracker.sector = 'Saturn'
        tracker.apply(wrap(0x001b, body), False, 10)
        obj = tracker.objects['0x00018749']
        self.assertEqual(obj['name'], "V'rix Scout")
        self.assertEqual(obj['kind'], 'ship_or_npc')
        self.assertEqual(obj['hostility'], 'unknown')
        self.assertEqual(obj['spawn_attributes_hex'], body[8:22].hex())
        tracker.apply(wrap(0x001b, body[:27]), False, 11)
        self.assertEqual(obj['last_seen'], 10)

    def test_observed_map_point_layout_has_id_name_and_coordinates(self):
        body = bytes.fromhex('a08601000b30000000803f00000000000000000000000002038031a34800eea2c7000000000000000000000000000000000000000000606a47b21500536563746f72204761746520746f2053617475726e')
        tracker = WorldTracker()
        tracker.apply(wrap(0x2018, body), False, 10)
        point = tracker.snapshot(11)['objects'][0]
        self.assertEqual(point['id'], '0x000186a0')
        self.assertEqual(point['name'], 'Sector Gate to Saturn')
        self.assertEqual(point['map_xyz'], [334.22, -83.42, 0.0])
        self.assertEqual(point['kind'], 'map_object')
        tracker.apply(wrap(0x2018, body[:-1]), False, 12)
        self.assertEqual(tracker.objects[point['id']]['last_seen'], 10)

    def test_sector_message_clears_previous_sector_objects(self):
        tracker = WorldTracker()
        tracker.apply(wrap(0x0097, bytes.fromhex('040000002100000009c20300536f6c0041737465726f69642042656c742047616d6d61000077010000')), False, 10)
        self.assertEqual(tracker.sector, 'Asteroid Belt Gamma')
        tracker.apply(asteroid(), False, 11)
        tracker.apply(wrap(0x0097, bytes.fromhex('040000001400000009c20300536f6c0053617475726e000077010000')), False, 12)
        self.assertEqual(tracker.sector, 'Saturn')
        self.assertFalse(tracker.objects)

    def test_dump_expires_after_30_seconds(self):
        tracker = WorldTracker()
        tracker.apply(asteroid(), False, 10)
        self.assertEqual(tracker.snapshot(40)['counts']['remembered_asteroids'], 1)
        self.assertEqual(tracker.snapshot(40.001)['objects'], [])
        self.assertIn('0x00000063', tracker.objects)

    def test_dump_newest_first(self):
        tracker = WorldTracker()
        tracker.object(1, 10, False)
        tracker.object(2, 15, False)
        tracker.object(3, 12, False)
        self.assertEqual([x['id'] for x in tracker.snapshot(20)['objects']], ['0x00000002', '0x00000003', '0x00000001'])

    def test_explicit_level_link(self):
        tracker = WorldTracker()
        body = bytes.fromhex('4ca703404800010200008001010000aa87010007004c6576656c2031')
        tracker.apply(wrap(0x001b, body), False, 10)
        self.assertEqual(tracker.objects['0x000187aa']['level'], 1)
        self.assertEqual(tracker.objects['0x000187aa']['level_source'], 'explicit_target_label')

    def test_type_does_not_invent_level(self):
        tracker = WorldTracker()
        tracker.apply(asteroid(), False, 10)
        item = tracker.snapshot(11)['objects'][0]
        self.assertEqual(item['type_id'], 0)
        self.assertIsNone(item['level'])

    def test_shared_type_level_is_only_an_inference(self):
        tracker = WorldTracker()
        tracker.apply(asteroid(), False, 10)
        tracker.level_evidence = [{'object_id': '0x00000001', 'type_id': 0, 'name': 'Asteroid', 'level': 3}]
        item = tracker.snapshot(11)['objects'][0]
        self.assertIsNone(item['level'])
        self.assertEqual(item['inferred_level'], 3)
        tracker.level_evidence.append({'object_id': '0x00000002', 'type_id': 0, 'name': 'Asteroid', 'level': 4})
        self.assertIsNone(tracker.snapshot(11)['objects'][0]['inferred_level'])

    def test_removal_retained_and_reappearance(self):
        tracker = WorldTracker()
        tracker.apply(asteroid(), False, 10)
        tracker.apply(wrap(7, struct.pack('<I', 99)), False, 20)
        self.assertEqual(tracker.snapshot(21)['objects'][0]['status'], 'removal_observed')
        tracker.apply(asteroid(), False, 30)
        self.assertEqual(tracker.snapshot(31)['objects'][0]['status'], 'recent')

    def test_distance_and_session_reset(self):
        tracker = WorldTracker()
        player = struct.pack('<HHIIfffI', 28, 0x1004, 123, 1, 0, 0, 0, 0)
        tracker.apply(player, True, 10)
        tracker.apply(asteroid(), False, 11)
        self.assertEqual(tracker.snapshot(12)['objects'][0]['distance_map_units'], 5)
        tracker.apply(struct.pack('<HHII', 12, 0x3005, 456, 1), True, 13)
        self.assertFalse(tracker.objects)

    def test_historical_is_not_recent(self):
        tracker = WorldTracker()
        tracker.apply(asteroid(), False, 10, historical=True)
        self.assertEqual(tracker.snapshot(11)['counts']['recent_asteroids'], 0)


if __name__ == '__main__':
    unittest.main()


class NavigationDiscoveryTests(unittest.TestCase):
    def map_packet(self, flags):
        body = bytearray(60)
        struct.pack_into('<I', body, 0, 100001)
        body[4] = 0x25
        struct.pack_into('<fff', body, 25, 1000, 2000, 0)
        body[57] = flags
        name = b'Nav test'
        struct.pack_into('<H', body, 58, len(name))
        return wrap(0x2018, body + name)

    def test_packed_flags_distinguish_unvisited_from_visited(self):
        tracker = WorldTracker()
        tracker.apply(self.map_packet(0x31), False, 10)
        point = tracker.objects['0x000186a1']
        self.assertFalse(point['visited'])
        self.assertEqual(point['nav_type'], 1)
        self.assertFalse(point['is_huge'])
        tracker.apply(self.map_packet(0xb1), False, 11)
        self.assertTrue(point['visited'])

    def test_non_navigation_object_has_no_discovery_classification(self):
        tracker = WorldTracker()
        tracker.apply(self.map_packet(0x22), False, 10)
        point = tracker.objects['0x000186a1']
        self.assertFalse(point['navigation_present'])
        self.assertNotIn('visited', point)

    def test_navigation_update_marks_discovery_without_losing_name(self):
        tracker = WorldTracker()
        tracker.apply(self.map_packet(0x31), False, 10)
        tracker.apply(wrap(0x0099, struct.pack('<IfBIB', 100001, 20000, 1, 1, 0)), False, 11)
        point = tracker.objects['0x000186a1']
        self.assertTrue(point['visited'])
        self.assertEqual(point['name'], 'Nav test')
        self.assertEqual(point['navigation_source'], '0x0099')

    def test_navigation_update_rejects_invalid_boolean(self):
        tracker = WorldTracker()
        tracker.apply(wrap(0x0099, struct.pack('<IfBIB', 100001, 20000, 2, 1, 0)), False, 11)
        self.assertFalse(tracker.objects)
