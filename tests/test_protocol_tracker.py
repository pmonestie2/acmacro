import struct
import unittest
from protocol.world_tracker import WorldTracker


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
