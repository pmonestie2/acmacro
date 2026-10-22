from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ac
import pluto
from watchdog import GameWindowLost, GameUnresponsive


class CrashRecoveryTests(unittest.TestCase):
    def controller(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        return controller

    def test_missing_window_raises_recoverable_error_before_mouse_input(self):
        controller = self.controller()
        controller.window = 123
        def lost():
            controller.window = None
        with patch("ac.win32gui.IsWindow", return_value=False), \
             patch.object(controller, "init_window", side_effect=lost), patch("ac.pag.moveTo") as move:
            with self.assertRaises(GameWindowLost):
                controller.moveTo(50, 50)
        move.assert_not_called()

    def test_window_disappearing_during_geometry_is_recoverable(self):
        controller = self.controller()
        controller.window = 123
        with patch("ac.win32gui.IsWindow", side_effect=[True, False]), \
             patch("ac.client_geometry", side_effect=ac.win32gui.error(1400, "GetClientRect", "Invalid window handle")):
            with self.assertRaises(GameWindowLost):
                controller.refresh_offset()
        self.assertIsNone(controller.window)

    def test_unrelated_geometry_error_is_not_retried_as_a_crash(self):
        controller = self.controller()
        controller.window = 123
        with patch("ac.win32gui.IsWindow", return_value=True), \
             patch("ac.client_geometry", side_effect=RuntimeError("bad geometry")):
            with self.assertRaisesRegex(RuntimeError, "bad geometry") as caught:
                controller.refresh_offset()
        self.assertNotIsInstance(caught.exception, GameWindowLost)

    def test_wait_detects_window_loss_before_returning_to_route(self):
        controller = self.controller()
        with patch.object(controller, "init_window"), patch("ac.time.sleep"):
            with self.assertRaises(GameWindowLost):
                controller._yield(0.1)

    def test_route_crash_reuses_controller_and_timer_then_resumes(self):
        controller = Mock(window=123)
        runner = pluto.PlutoRunner()
        with patch("pluto.AcController", return_value=controller) as factory, \
             patch("pluto.run_every") as timer, patch("pluto.suspend_chat") as suspend, \
             patch("pluto.reset_chat") as reset, patch.object(runner, "find_dir"), \
             patch.object(pluto.PLAYER, "set_pos"), \
             patch.object(runner, "route", side_effect=[GameWindowLost("crashed"), KeyboardInterrupt]) as route:
            with self.assertRaises(KeyboardInterrupt):
                runner._run_loop()
        factory.assert_called_once()
        timer.assert_called_once()
        controller.terminate.assert_called_once()
        controller.relogin.assert_called_once_with(initial=False)
        self.assertEqual(route.call_count, 2)
        suspend.assert_called_once()
        reset.assert_called_once()  # Initial cursor only; recovery preserves pending PMs.

    def test_crashes_during_cold_login_exhaust_relogin_budget(self):
        controller = self.controller()
        controller.player = ac.Player()
        controller._last_progress_version = controller.player.progress_version
        controller.init_window = Mock()
        controller.bring_to_front = Mock(return_value=True)
        for name in ("say", "_yield", "killenb", "launchEnb"):
            setattr(controller, name, Mock())
        # Simulate the client vanishing when selecting the character on every login.
        controller.lclick = Mock(side_effect=GameWindowLost("crashed during login"))
        runner = pluto.PlutoRunner()
        with patch("ac.key_press"), patch("pluto.AcController", return_value=controller), \
             patch("pluto.run_every"), patch("pluto.suspend_chat"):
            with self.assertRaisesRegex(RuntimeError, "Relogin limit reached"):
                runner._run_loop()
        self.assertEqual(controller.launchEnb.call_count, 4)  # One cold start + three relogins.
        self.assertEqual(controller.relogin_attempts, 3)
        self.assertEqual(len(controller._relogin_times), 3)
        self.assertTrue(controller._terminated)

    def test_missing_chat_marker_requests_recovery(self):
        controller = self.controller()
        controller.bring_to_front = Mock(return_value=True)
        controller.say = Mock()
        controller.check_chat_response = Mock()
        controller._yield = Mock()
        with patch("ac.begin_chat", return_value="test-marker"), patch("ac.chat_ready", return_value=False), \
             patch("ac.time.monotonic", side_effect=[0, 60]):
            with self.assertRaises(GameUnresponsive):
                controller.init_read_chat()

    def test_marker_arriving_at_deadline_does_not_restart_game(self):
        controller = self.controller()
        controller.bring_to_front = Mock(return_value=True)
        controller.say = Mock()
        controller.check_chat_response = Mock()
        controller._yield = Mock()
        with patch("ac.begin_chat", return_value="test-marker"), \
             patch("ac.chat_ready", side_effect=[False, True, True]), \
             patch("ac.time.monotonic", side_effect=[0, 60]):
            controller.init_read_chat()

    def test_frozen_game_is_killed_and_relogged_with_retry_limit(self):
        controller = self.controller()
        controller.window = 123  # Frozen window still exists.
        controller.player = ac.Player()
        controller._last_progress_version = controller.player.progress_version
        controller.bring_to_front = Mock(return_value=True)
        controller.wait_objects = Mock(return_value=True)
        controller.test_obj = Mock(return_value=False)
        for name in ("init_window", "refresh_offset", "say", "_yield", "killenb", "launchEnb", "lclick", "enter_login_credentials"):
            setattr(controller, name, Mock())
        controller.client_size = controller.screen_size = (1280, 768)
        controller.init_read_chat = Mock(side_effect=GameUnresponsive("marker timeout"))
        runner = pluto.PlutoRunner()
        with patch("ac.key_press"), patch("pluto.AcController", return_value=controller), \
             patch("pluto.run_every") as timer, patch("pluto.suspend_chat"), \
             patch("pluto.reset_chat") as reset, patch.object(runner, "route") as route:
            with self.assertRaisesRegex(RuntimeError, "Relogin limit reached"):
                runner._run_loop()
        self.assertEqual(controller.launchEnb.call_count, 3)
        self.assertEqual(controller.killenb.call_count, 4)  # Three retries, then final close.
        self.assertEqual(controller.init_read_chat.call_count, 4)
        self.assertEqual(controller.relogin_attempts, 3)
        self.assertTrue(controller._terminated)
        timer.assert_called_once()
        reset.assert_called_once()  # Initial cursor only; recovery preserves pending PMs.
        route.assert_not_called()


if __name__ == "__main__":
    unittest.main()
