import unittest
import uuid
from unittest.mock import Mock, patch
import ac
from focus import SharedFocus
from player import Player


class StationWaitTests(unittest.TestCase):
    def test_first_arrival_wins_and_waiter_proceeds_when_owner_leaves(self):
        focus = SharedFocus("station-game", "Arriving", namespace="Local\\station-test-" + uuid.uuid4().hex)
        try:
            with focus.state() as state:
                state["members"]["peer"] = {"pid": 100, "born": "peer", "toon": "First",
                    "game": True, "station": "earthS", "station_ticket": 1}
                state["station_ticket"] = 1
            with patch.object(focus, "clean"):
                self.assertEqual(focus.station_blocker("earthS"), "First")
                self.assertEqual(focus.station_blocker("earthS"), "First")
                with focus.state() as state:
                    state["members"]["peer"]["station"] = None
                self.assertIsNone(focus.station_blocker("earthS"))
        finally:
            focus.close()

    def controller(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.player = Player()
        return controller

    def test_station_wait_releases_focus_waits_one_minute_and_rechecks(self):
        controller = self.controller()
        controller.focus = Mock(depth=0)
        controller.focus.station_blocker.side_effect = ["Peer", None]
        def pause(seconds):
            self.assertEqual(seconds, 60)
            self.assertTrue(controller._station_waiting)
            controller.focus.release.assert_called_once()
        controller._yield = Mock(side_effect=pause)
        controller._position_changed("earthJOB_jc_3")
        self.assertEqual([c.args for c in controller.focus.station_blocker.call_args_list], [("earthS",), ("earthS",)])
        self.assertFalse(controller._station_waiting)

    def test_singleton_bypasses_station_check_and_wait(self):
        controller = self.controller()
        controller.singleton = True
        controller.focus = Mock()
        controller._yield = Mock()
        controller._position_changed("earthS")
        controller.focus.station_blocker.assert_not_called()
        controller._yield.assert_not_called()

    def test_disabled_station_lock_clears_claim_and_never_waits_at_any_station(self):
        for position in ('earthS', 'earthJOB', 'earthJOB_jc_3', 'charonS', 'somerS', 'prasadS'):
            with self.subTest(position=position):
                controller = self.controller()
                controller.station_lock = False
                controller.focus = Mock(depth=0)
                controller.focus.station_blocker.return_value = None
                controller._yield = Mock()
                controller._position_changed(position)
                controller.focus.station_blocker.assert_called_once_with(None)
                controller._yield.assert_not_called()

    def test_enabled_station_lock_waits_at_all_stations(self):
        for position in ('earthS', 'charonS', 'somerS', 'prasadS'):
            with self.subTest(position=position):
                controller = self.controller()
                controller.station_lock = True
                controller.focus = Mock(depth=0)
                controller.focus.station_blocker.side_effect = ['Peer', None]
                controller._yield = Mock()
                controller._position_changed(position)
                controller._yield.assert_called_once_with(60)

    def test_trade_only_earth_clears_reservation_without_waiting(self):
        controller = self.controller()
        controller.player.type_ = "new"
        controller.focus = Mock(depth=0)
        controller.focus.station_blocker.return_value = None
        controller._yield = Mock()
        controller._position_changed("earthS")
        controller.focus.station_blocker.assert_called_once_with(None)
        controller._yield.assert_not_called()
        controller.focus.release.assert_not_called()

    def test_trade_only_charon_does_not_wait_or_reserve_station(self):
        controller = self.controller()
        controller.player.type_ = 'new'
        controller.focus = Mock(depth=0)
        controller.focus.station_blocker.return_value = None
        controller._yield = Mock()
        controller._position_changed('charonS')
        controller.focus.station_blocker.assert_called_once_with(None)
        controller._yield.assert_not_called()
        controller.focus.release.assert_not_called()

    def test_position_changes_publish_departures_and_job_substates(self):
        player = Player()
        player.on_position = Mock()
        player.set_pos("earthS")
        player.set_pos("earthJOB")
        player.set_pos("earth")
        self.assertEqual([c.args[0] for c in player.on_position.call_args_list], ["earthS", "earthJOB", "earth"])

    def test_arrival_inside_input_action_waits_only_after_unlocking(self):
        controller = self.controller()
        controller.player.type_ = 'je'
        controller.window = 123
        controller.focus = Mock(depth=0, owned=False, singleton=False, last_wait=0)
        controller.focus.station_blocker.side_effect = ['Peer', None]
        controller.require_window = Mock()
        controller._check_remote_logoff = Mock()
        def wait(seconds):
            self.assertEqual(seconds, 60)
            self.assertEqual(controller.focus.depth, 0)
            self.assertIsNone(controller._exclusive_deadline)
        controller._yield = Mock(side_effect=wait)
        with patch('ac.win32gui.GetForegroundWindow', return_value=123), patch('ac.run_interrupts'):
            with controller.exclusive_focus('departure', timeout=45):
                controller._position_changed('charonS')
                controller.focus.station_blocker.assert_not_called()
                controller._yield.assert_not_called()
        controller._yield.assert_called_once_with(60)
        self.assertIsNone(controller._pending_station_position)

    def test_deferred_position_uses_latest_location(self):
        controller = self.controller()
        controller.focus = Mock(depth=1)
        controller.focus.station_blocker.return_value = None
        controller._position_changed('charonS')
        controller._position_changed('pluto')
        controller.focus.station_blocker.assert_not_called()
        controller.focus.depth = 0
        controller._position_changed(controller._pending_station_position)
        controller.focus.station_blocker.assert_called_once_with(None)
        self.assertIsNone(controller._pending_station_position)
