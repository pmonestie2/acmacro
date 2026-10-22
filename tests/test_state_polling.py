from contextlib import nullcontext, contextmanager
import unittest
from unittest.mock import Mock, patch

import ac
import pluto


class StatePollingTests(unittest.TestCase):
    def test_gate_polling_slows_only_cooperative_non_monster_checks(self):
        controller = self.controller()
        self.assertEqual(controller.gate_poll_interval(current=3), 8)
        self.assertEqual(controller.gate_poll_interval(current=2), 8)
        self.assertEqual(controller.gate_poll_interval(current=2, fast=True), 2)
        controller.singleton = True
        self.assertEqual(controller.gate_poll_interval(current=3), 3)
        self.assertEqual(controller.gate_poll_interval(current=2), 2)

    def test_slow_polling_checks_at_deadline_without_oversleeping(self):
        controller = self.controller()
        clock = [0.0]
        snapshots = []
        controller.moveTo = Mock()
        controller.screenshot = Mock(side_effect=lambda: snapshots.append(clock[0]))
        controller.test_obj = Mock(side_effect=[False, False, True])
        controller._yield = Mock(side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
        with patch("ac.time.monotonic", side_effect=lambda: clock[0]):
            self.assertTrue(controller.wait_gate_objects([Mock(anchor=(0, 0))], timeout=12))
        self.assertEqual(snapshots, [0, 8, 12])
        self.assertEqual([c.args[0] for c in controller._yield.call_args_list], [8, 4])

    def test_satellite_fast_wait_checks_panel_after_last_approach_key(self):
        controller = self.controller()
        clock = [0.0]
        snapshots = []
        controller.moveTo = Mock()
        controller.screenshot = Mock(side_effect=lambda: snapshots.append(clock[0]))
        controller.test_obj = Mock(side_effect=[False, False, True])
        controller._yield = Mock(side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
        def approach():
            clock[0] += 1.2
        with patch("ac.time.monotonic", side_effect=lambda: clock[0]):
            self.assertTrue(controller.wait_objects([Mock(anchor=(0, 0))], yield_time=3,
                timeout=6, yield_fn=approach, final_check=True))
        self.assertEqual(snapshots, [0, 4.2, 6])

    def controller(self):
        with patch.object(ac.AcController, "init_window"):
            return ac.AcController("test")

    def test_default_polling_takes_one_screenshot_every_two_seconds(self):
        controller = self.controller()
        clock = [0.0]
        controller.moveTo = Mock()
        controller.screenshot = Mock(return_value="snapshot")
        controller.test_obj = Mock(return_value=False)
        controller._yield = Mock(side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
        with patch("ac.time.monotonic", side_effect=lambda: clock[0]):
            self.assertFalse(controller.wait_objects([Mock(anchor=(10, 10))], timeout=5))
        self.assertEqual(controller.screenshot.call_count, 3)
        self.assertEqual([c.args[0] for c in controller._yield.call_args_list], [2, 2, 2])

    def test_waiting_thirty_seconds_for_focus_consumes_state_timeout(self):
        controller = self.controller()
        controller.focus = Mock(total_wait=0.0)
        controller.input_action = Mock(side_effect=nullcontext)
        clock = [0.0]
        controller.moveTo = Mock()
        def screenshot():
            if controller.screenshot.call_count == 1:
                controller.focus.total_wait = 30.0
                clock[0] += 30
            return "snapshot"
        controller.screenshot = Mock(side_effect=screenshot)
        controller.test_obj = Mock(side_effect=[False, True])
        controller._yield = Mock(side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
        with patch("ac.time.monotonic", side_effect=lambda: clock[0]):
            self.assertFalse(controller.wait_objects([Mock(anchor=(10, 10))], timeout=3))
        self.assertEqual(controller.screenshot.call_count, 1)
        controller._yield.assert_not_called()

    def test_focus_queue_wait_does_not_extend_progress_deadlines(self):
        controller = self.controller()
        controller.player = ac.Player()
        controller.player.set_time = 100
        controller.player.relogin_time = 200
        controller.focus = Mock(depth=0, owned=False, singleton=False, last_wait=30)
        controller.window = 123
        controller.require_window = Mock()
        with patch('ac.win32gui.GetForegroundWindow', return_value=123), patch('ac.run_interrupts'):
            with controller.input_action():
                pass
        self.assertEqual(controller.player.set_time, 100)
        self.assertEqual(controller.player.relogin_time, 200)

    def test_delay_includes_time_spent_in_focus_waiting_interrupts(self):
        controller = self.controller()
        controller._logging_in = True
        clock = [0.0]
        def sleep(seconds):
            clock[0] += seconds
        def interrupt():
            clock[0] += 10  # A callback waited for another game's focus.
        with patch('ac.time.monotonic', side_effect=lambda: clock[0]), \
             patch('ac.time.sleep', side_effect=sleep) as delay, \
             patch('ac.run_interrupts', side_effect=interrupt):
            controller._yield(5)
        self.assertEqual(delay.call_count, 1)
        self.assertAlmostEqual(clock[0], 10.3)

    def test_gate_and_station_checks_reuse_one_snapshot_and_skip_redundant_wait(self):
        controller = self.controller()
        for name in ("press", "get_map", "lclick", "_yield", "wait_objects"):
            setattr(controller, name, Mock())
        controller.screenshot = Mock(return_value="shared-snapshot")
        controller.test_obj = Mock(side_effect=[True, False])
        with patch("ac.time.sleep"):
            self.assertTrue(controller.gate_internal(10, 20, no_gate=True))
        controller.screenshot.assert_called_once()
        controller.wait_objects.assert_not_called()
        self.assertEqual([c.kwargs["screenshot"] for c in controller.test_obj.call_args_list],
                         ["shared-snapshot", "shared-snapshot"])

    def test_poll_cursor_capture_and_response_share_focus_but_sleep_yields(self):
        controller = self.controller()
        depth = [0]
        @contextmanager
        def action():
            depth[0] += 1
            try:
                yield
            finally:
                depth[0] -= 1
        controller.input_action = Mock(side_effect=action)
        controller.moveTo = Mock(side_effect=lambda *args: self.assertEqual(depth[0], 1))
        controller.screenshot = Mock(side_effect=lambda: self.assertEqual(depth[0], 1))
        controller.test_obj = Mock(side_effect=[False, True])
        controller._yield = Mock(side_effect=lambda *args: self.assertEqual(depth[0], 0))
        response = Mock(side_effect=lambda: self.assertEqual(depth[0], 1))
        self.assertTrue(controller.wait_objects([Mock(anchor=(0, 0))], on_found=response))
        self.assertEqual(controller.input_action.call_count, 2)
        controller.moveTo.assert_called_once()
        controller._yield.assert_called_once_with(2)
        response.assert_called_once()

    def test_job_panel_check_and_click_share_turn_and_retry_wait_yields(self):
        for opening in (True, False):
            with self.subTest(opening=opening):
                runner = pluto.PlutoRunner()
                controller = Mock()
                runner.controller = controller
                depth = [0]
                @contextmanager
                def action(name):
                    depth[0] += 1
                    try:
                        yield
                    finally:
                        depth[0] -= 1
                controller.with_focus.side_effect = action
                controller.polling_time.return_value = 0
                states = iter([not opening, opening])
                def check(*args):
                    self.assertEqual(depth[0], 1)
                    return next(states)
                runner._test = Mock(side_effect=check)
                controller.lclick.side_effect = lambda *args: self.assertEqual(depth[0], 1)
                controller._yield.side_effect = lambda *args: self.assertEqual(depth[0], 0)
                (runner.open_job if opening else runner.close_job)()
                controller.lclick.assert_called_once()
                self.assertEqual(controller.with_focus.call_count, 2)


if __name__ == "__main__":
    unittest.main()
