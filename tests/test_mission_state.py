import json
from pathlib import Path
import struct
import threading
import unittest
from unittest.mock import Mock, patch

import ac
from protocol.game_packet_monitor import GamePacketMonitor
from protocol.mission_state import MissionState, decode_missions


FIXTURES = {row['label']: row for row in json.loads(
    (Path(__file__).parent / 'fixtures/mission_aux.json').read_text())}


def packet(body, sequence=1):
    frame = struct.pack('<HH', len(body) + 4, 0x001b) + body
    return struct.pack('<HHII', len(frame) + 12, 0x2016, 123, sequence) + frame


def rename(slot, name):
    text = name.encode()
    slots = ((1 << (slot + 4)) | 2).to_bytes(2, 'little')
    return bytes(6) + b'\x01\x02\x20\x00' + slots + b'\x22\x00\x00' + struct.pack('<H', len(text)) + text


class MissionStateTests(unittest.TestCase):
    def test_real_snapshots_count_occupied_names_including_story_missions(self):
        for row in FIXTURES.values():
            with self.subTest(row=row['label']):
                state = MissionState()
                state.observe(packet(bytes.fromhex(row['body_hex'])), False, 10)
                self.assertEqual(state.snapshot()['count'], row['expected_count'])
                self.assertEqual(state.snapshot()['capacity'], 11)

    def test_slot_ids_alone_do_not_count_as_missions(self):
        state = MissionState()
        state.observe(packet(bytes.fromhex(FIXTURES['empty']['body_hex'])), False, 10)
        self.assertEqual(state.snapshot()['count'], 0)

    def test_partial_capture_never_invents_a_baseline(self):
        state = MissionState()
        state.observe(packet(rename(1, 'Launch a Satellite')), False, 10)
        self.assertIsNone(state.snapshot())

    def test_accept_remove_duplicate_and_reordered_updates(self):
        state = MissionState()
        state.observe(packet(bytes.fromhex(FIXTURES['promotion']['body_hex']), 10), False, 10)
        state.observe(packet(rename(1, 'Launch a Satellite'), 11), False, 11)
        self.assertEqual(state.snapshot()['count'], 2)
        state.observe(packet(rename(1, ''), 12), False, 12)
        self.assertEqual(state.snapshot()['count'], 1)
        state.observe(packet(rename(1, 'Launch a Satellite'), 11), False, 13)
        self.assertEqual(state.snapshot()['count'], 1)

    def test_malformed_mission_update_invalidates_previously_good_count(self):
        state = MissionState()
        state.observe(packet(bytes.fromhex(FIXTURES['delivery']['body_hex'])), False, 10)
        state.observe(packet(rename(1, 'abc')[:-1], 2), False, 11)
        self.assertIsNone(state.snapshot())

    def test_packet_loss_invalidates_count_until_another_complete_snapshot(self):
        state = MissionState()
        body = bytes.fromhex(FIXTURES['delivery']['body_hex'])
        state.observe(packet(body, 10), False, 10)
        state.observe(packet(rename(1, ''), 12), False, 12)
        self.assertIsNone(state.snapshot())
        state.observe(packet(body, 13), False, 13)
        self.assertEqual(state.snapshot()['count'], 2)

    def test_new_packet_session_cannot_reuse_previous_characters_missions(self):
        state = MissionState()
        state.observe(packet(bytes.fromhex(FIXTURES['delivery']['body_hex']), 10), False, 10)
        other = bytearray(packet(rename(1, ''), 1))
        struct.pack_into('<I', other, 4, 456)
        state.observe(bytes(other), False, 11)
        self.assertIsNone(state.snapshot())

    def test_unsupported_inventory_prefix_requires_fallback(self):
        body = bytearray(bytes.fromhex(FIXTURES['promotion']['body_hex']))
        body[7] |= 0x40
        state = MissionState()
        state.observe(packet(bytes(body)), False, 10)
        self.assertIsNone(state.snapshot())

    def test_foreign_object_and_outbound_packet_do_not_change_count(self):
        body = bytearray(bytes.fromhex(FIXTURES['promotion']['body_hex']))
        body[0] = 1
        self.assertIsNone(decode_missions(body))
        state = MissionState()
        state.observe(packet(bytes.fromhex(FIXTURES['promotion']['body_hex'])), True, 10)
        self.assertIsNone(state.snapshot())

    def test_snapshot_rejects_previous_proxy_stale_capture_and_unsettled_update(self):
        monitor = GamePacketMonitor.__new__(GamePacketMonitor)
        monitor.lock = threading.Lock()
        monitor.instance = Mock(proxy_pid=123, proxy_born='born')
        monitor.owner = (123, 'born')
        monitor.thread = Mock()
        monitor.thread.is_alive.return_value = True
        monitor.traffic_seen = 20
        monitor.missions = MissionState()
        monitor.missions.observe(packet(bytes.fromhex(FIXTURES['promotion']['body_hex'])), False, 19)
        self.assertEqual(monitor.mission_snapshot(now=20)['count'], 1)
        self.assertIsNone(monitor.mission_snapshot(now=19.1))
        self.assertIsNone(monitor.mission_snapshot(now=26))
        monitor.instance.proxy_born = 'other'
        self.assertIsNone(monitor.mission_snapshot(now=20))

    def test_diagnostic_distinguishes_mid_session_start_and_packet_loss(self):
        monitor = GamePacketMonitor.__new__(GamePacketMonitor)
        monitor.lock = threading.Lock()
        monitor.instance = Mock(proxy_pid=123, proxy_born='born')
        monitor.owner = (123, 'born')
        monitor.thread = Mock()
        monitor.thread.is_alive.return_value = True
        monitor.traffic_seen = 20
        monitor.mission_remote = 'server:1234'
        monitor.missions = MissionState()
        reason = monitor.mission_unavailable_reason(now=20)
        self.assertIn('existing login may predate capture', reason)
        self.assertIn('full_snapshots=0', reason)
        body = bytes.fromhex(FIXTURES['promotion']['body_hex'])
        monitor.missions.observe(packet(body, 10), False, 19)
        monitor.missions.observe(packet(rename(1, 'Job'), 12), False, 20)
        reason = monitor.mission_unavailable_reason(now=20)
        self.assertIn('packet sequence gap', reason)
        self.assertIn('full_snapshots=1', reason)
        self.assertNotIn('existing login may predate capture', reason)


class CountSourceTests(unittest.TestCase):
    def controller(self):
        with patch.object(ac.AcController, 'init_window'):
            controller = ac.AcController('test')
        controller.start_packet_capture = Mock()
        controller._packet_monitor = Mock()
        controller._count_mission_ui = Mock(return_value=3)
        return controller

    def test_packet_count_avoids_focus_and_ui_and_logs_names(self):
        controller = self.controller()
        controller._packet_monitor.mission_snapshot.return_value = {
            'count': 2, 'missions': [{'slot': 0, 'name': 'Promotion'}, {'slot': 1, 'name': 'Launch a Satellite'}]}
        with patch('ac.log_event') as log:
            self.assertEqual(controller.count_mission(), 2)
        controller._count_mission_ui.assert_not_called()
        self.assertIn('source=packet 0x001b', log.call_args.args[0])
        self.assertIn('Launch a Satellite', log.call_args.args[0])

    def test_missing_baseline_uses_and_logs_screen_fallback(self):
        controller = self.controller()
        controller._packet_monitor.mission_snapshot.return_value = None
        controller._packet_monitor.mission_unavailable_reason.return_value = 'waiting for a complete mission snapshot'
        with patch('ac.log_event') as log:
            self.assertEqual(controller.count_mission(), 3)
        controller._count_mission_ui.assert_called_once()
        self.assertIn('screen estimate', log.call_args.args[0])
        self.assertIn('waiting for a complete mission snapshot', log.call_args.args[0])
