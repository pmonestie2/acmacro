from contextlib import contextmanager
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import ac
from protocol.game_packet_monitor import GamePacketMonitor, Positions
from tests.test_protocol_tracker import wrap


def position(token, xyz=(0, 0, 0)):
    return struct.pack('<HHIIfffI', 28, 0x1004, token, 1, *xyz, 0)


class DistanceTests(unittest.TestCase):
    def test_incoming_other_ship_does_not_replace_player_position(self):
        state = Positions()
        state.observe(position(123, (1000, 2000, 0)), True, 10)
        body = bytearray(54)
        body[:2] = b'\x28\x01'
        struct.pack_into('<I', body, 2, 999)
        struct.pack_into('<fff', body, 10, 90000, 0, 0)
        state.observe(wrap(0x003e, body), False, 11)
        self.assertEqual(state.xyz, (1000, 2000, 0))
        struct.pack_into('<I', body, 2, 123)
        state.observe(wrap(0x003e, body), False, 12)
        self.assertEqual(state.xyz, (90000, 0, 0))

    def test_sector_change_discards_old_player_position(self):
        state = Positions()
        state.observe(position(123), True, 10)
        state.observe(wrap(0x0097, bytes.fromhex('040000001400000009c20300536f6c0053617475726e000077010000')), False, 11)
        self.assertEqual(state.sector, 'Saturn')
        self.assertIsNone(state.xyz)

    def test_distance_is_scoped_to_sector_fresh_position_and_proxy_lifetime(self):
        reader = GamePacketMonitor.__new__(GamePacketMonitor)
        reader.lock = threading.Lock()
        reader.instance = Mock(proxy_pid=12, proxy_born='born')
        reader.owner = (12, 'born')
        reader.positions = Positions()
        reader.positions.observe(position(123), True, 100)
        reader.positions.sector = 'Saturn'
        reader.points, reader.catalog_stamp = {}, None
        with tempfile.TemporaryDirectory() as directory:
            reader.catalog = Path(directory) / 'navpoints.json'
            reader.catalog.write_text(json.dumps({'sectors': {'Saturn': {'gate': {
                'id': '0x00000123', 'raw_xyz': [6000, 8000, 0]}}}}))
            self.assertEqual(reader.distance('saturn', 0x123, 101), 10000)
            self.assertIsNone(reader.distance('saturn', 0x123, 106))
            self.assertIsNone(reader.distance('aba', 0x123, 101))
            self.assertIsNone(reader.distance('saturn', 0x124, 101))
            reader.instance.proxy_born = 'restarted'
            self.assertIsNone(reader.distance('saturn', 0x123, 101))

    def test_warp_speed_requires_fresh_motion_and_resets_on_sector_change(self):
        state = Positions()
        state.observe(position(123), True, 10)
        state.observe(position(123, (1250, 0, 0)), True, 11)
        self.assertEqual(state.speed, 1250)
        self.assertEqual(state.moved, 11)
        state.observe(position(123, (1250, 0, 0)), True, 12)
        self.assertEqual(state.moved, 11)
        state.observe(wrap(0x0097, bytes.fromhex('040000001400000009c20300536f6c0053617475726e000077010000')), False, 13)
        self.assertEqual(state.speed, 0)
        self.assertEqual(state.moved, 0)

    def test_unknown_station_cannot_be_confirmed_from_unnamed_sector(self):
        reader = GamePacketMonitor.__new__(GamePacketMonitor)
        reader.lock = threading.Lock()
        reader.instance = Mock(proxy_pid=12, proxy_born='born')
        reader.owner = (12, 'born')
        reader.positions = Positions()
        reader.positions.observe(position(123), True, 100)
        with patch('protocol.game_packet_monitor.time.time', return_value=101):
            self.assertFalse(reader.arrived('earthS'))
            reader.positions.sector = 'Saturn'
            self.assertTrue(reader.arrived('saturn'))


class ApproachTests(unittest.TestCase):
    def controller(self, distance):
        with patch.object(ac.AcController, 'init_window'):
            controller = ac.AcController('test')
        controller.player = ac.Player()
        controller.player.current_position = 'saturn'
        controller.focus = Mock()
        controller._packet_monitor = Mock()
        controller._packet_monitor.distance.return_value = distance
        self.clock = [100.0]
        controller.polling_time = lambda: self.clock[0]
        controller._yield = lambda seconds: self.clock.__setitem__(0, self.clock[0] + seconds)
        self.turns = []
        @contextmanager
        def exclusive(name, timeout, timeout_error):
            self.turns.append(('acquired', timeout, timeout_error))
            try:
                yield
            finally:
                self.turns.append(('released',))
        controller.exclusive_focus = exclusive
        return controller

    def test_at_10km_locks_and_releases_immediately_after_click(self):
        controller = self.controller(10000)
        click = Mock()
        def found(objects, **kwargs):
            self.assertTrue(controller._gate_close_active)
            self.assertEqual(kwargs['yield_time'], 1)
            self.assertLessEqual(kwargs['timeout'], 18)
            kwargs['on_found']()
            return True
        controller.wait_objects = Mock(side_effect=found)
        with patch('ac.time.monotonic', side_effect=lambda: self.clock[0]):
            self.assertTrue(controller.wait_gate_entry([], 0x123, 8, timeout=30, on_found=click))
        click.assert_called_once()
        self.assertEqual(self.clock[0], 100)
        self.assertEqual(self.turns, [('acquired', 20, ac.GateFocusExpired), ('released',)])
        self.assertFalse(controller._gate_close_active)

    def test_far_or_unknown_distance_keeps_normal_polling_without_exclusive_lock(self):
        for distance in (10001, None):
            controller = self.controller(distance)
            controller.wait_objects = Mock(side_effect=[False, True])
            with patch('ac.time.monotonic', side_effect=lambda: self.clock[0]):
                self.assertTrue(controller.wait_gate_entry([], 0x123, 8, timeout=30))
            self.assertEqual(self.turns, [])
            self.assertEqual(self.clock[0], 108)

    def test_focus_expiry_lends_to_peer_and_does_not_relogin(self):
        controller = self.controller(5000)
        def expired(objects, **kwargs):
            if controller.wait_objects.call_count == 1:
                self.clock[0] += 20
                raise ac.GateFocusExpired('20 seconds')
            return True
        controller.wait_objects = Mock(side_effect=expired)
        with patch('ac.time.monotonic', side_effect=lambda: self.clock[0]):
            self.assertTrue(controller.wait_gate_entry([], 0x123, 8, timeout=30))
        self.assertGreaterEqual(self.clock[0], 121.5)
        self.assertEqual(self.turns[1], ('released',))
        self.assertFalse(controller._gate_close_active)

    def test_singleton_uses_original_wait_without_position_thread(self):
        controller = self.controller(5000)
        controller.singleton = True
        controller.wait_objects = Mock(return_value=True)
        with patch('ac.GamePacketMonitor') as monitor:
            self.assertTrue(controller.wait_gate_objects([], current=3, gate_target=0x123))
        monitor.assert_not_called()
        self.assertEqual(controller.wait_objects.call_args.kwargs['yield_time'], 3)
