import sys
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ac
import chatproc
from chat_reply import ChatDecision
import watchdog


class ReloginTests(unittest.TestCase):
    def controller(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.player = ac.Player()
        controller.player.password = ""
        controller.player.toon_position = 1
        controller._last_progress_version = controller.player.progress_version
        for name in ("say", "_yield", "killenb", "launchEnb", "lclick", "enter_login_credentials"):
            setattr(controller, name, Mock())
        controller.bring_to_front = Mock(return_value=True)
        controller.wait_objects = Mock(return_value=True)
        controller.test_obj = Mock(return_value=False)
        return controller

    def test_successful_relogins_do_not_reset_limit(self):
        controller = self.controller()
        with patch("ac.key_press"):
            for _ in range(3):
                controller.relogin()
            with self.assertRaisesRegex(RuntimeError, "Relogin limit reached"):
                controller.relogin()
        self.assertEqual(controller.launchEnb.call_count, 3)
        self.assertEqual(controller.relogin_attempts, 3)
        self.assertTrue(controller._terminated)
        self.assertFalse(controller._logging_in)

    def test_failed_attempts_are_bounded_and_initial_start_is_separate(self):
        for initial, launches in ((False, 3), (True, 4)):
            with self.subTest(initial=initial), patch("ac.key_press"):
                controller = self.controller()
                controller.wait_objects.return_value = False
                with self.assertRaisesRegex(RuntimeError, "Relogin limit reached"):
                    controller.relogin(initial=initial)
                self.assertEqual(controller.launchEnb.call_count, launches)
                self.assertEqual(controller.relogin_attempts, 3)

    def test_progress_does_not_bypass_ten_minute_limit(self):
        controller = self.controller()
        with patch("ac.time.monotonic", side_effect=[0, 100, 200, 300]), patch("ac.key_press"):
            for pos in ("checkpoint1", "checkpoint2", "checkpoint3"):
                controller.relogin()
                controller.player.set_pos(pos)
            with self.assertRaisesRegex(RuntimeError, "within 10 minutes"):
                controller.relogin()
        self.assertEqual(controller.launchEnb.call_count, 3)

    def test_ten_minutes_expiring_does_not_bypass_no_progress_limit(self):
        controller = self.controller()
        with patch("ac.time.monotonic", side_effect=[0, 601, 1202, 1803]), patch("ac.key_press"):
            for _ in range(3):
                controller.relogin()
            with self.assertRaisesRegex(RuntimeError, "since last progress"):
                controller.relogin()
        self.assertEqual(controller.launchEnb.call_count, 3)

    def test_progress_and_expired_history_allow_more_relogins(self):
        controller = self.controller()
        with patch("ac.time.monotonic", side_effect=[0, 100, 200, 600]), patch("ac.key_press"):
            for _ in range(3):
                controller.relogin()
            controller.player.set_pos("checkpoint1")
            controller.relogin()
        self.assertEqual(controller.launchEnb.call_count, 4)
        self.assertEqual(controller.relogin_attempts, 1)
        self.assertEqual(list(controller._relogin_times), [100, 200, 600])

    def test_same_position_and_login_location_do_not_count_as_progress(self):
        player = ac.Player()
        player.set_pos(player.current_position)
        player.set_pos("recognized-sector", progress=False)
        self.assertEqual(player.progress_version, 0)
        player.set_pos("new-checkpoint")
        self.assertEqual(player.progress_version, 1)

    def test_only_one_restart_and_stale_request_is_discarded(self):
        controller = self.controller()
        controller.player.set_time = controller.player.relogin_time = 0
        with patch("ac.find_window_exact", return_value=None), patch("ac.request_interrupt") as enqueue:
            controller.check_game_progress()
            controller.check_game_progress()
        enqueue.assert_called_once()
        controller.terminate()
        with patch.object(controller, "relogin") as login:
            enqueue.call_args.args[0]()
        login.assert_not_called()

    def test_progress_clock_is_reset_after_login(self):
        controller = self.controller()
        controller.player.set_time = 0
        with patch.object(controller, "_login"), patch("ac.time.time", return_value=1234):
            controller.relogin()
        self.assertEqual(controller.player.set_time, 1234)
        self.assertEqual(controller.player.relogin_time, 1234)

    def test_replies_wait_for_login_then_target_each_sender(self):
        controller = self.controller()
        with patch("chatproc.require_admin"), patch.object(Path, "exists", return_value=False):
            chatproc.reset_chat("Lightje")
        marker = chatproc.begin_chat()
        text = f"(3):19:00:00 Lightje (private): {marker}\n(3):19:00:01 Alice (private): hi\n(3):19:00:02 Bob (private): hi\n"
        controller._logging_in = True
        with patch("chatproc.read_chat_log", return_value=text), patch("ac.request_interrupt") as enqueue, \
             patch("ac.reply_decision", return_value=ChatDecision("OK", "Hi")), patch("ac.log_interaction"):
            controller.check_chat_response()
            enqueue.assert_not_called()
            self.assertEqual(chatproc.next_reply()[0], "Alice")
            controller._logging_in = False
            controller.check_chat_response()
            controller.check_chat_response()
            enqueue.assert_called_once()
            controller.bring_to_front.return_value = False
            enqueue.call_args.args[0]()
            self.assertEqual(chatproc.next_reply()[0], "Alice")
            controller.bring_to_front.return_value = True
            controller.check_chat_response()
            enqueue.call_args.args[0]()
        self.assertEqual(controller.say.call_args_list, [call("/t Alice Hi"), call("/t Bob Hi")])
        self.assertIsNone(chatproc.next_reply())
        controller.killenb.assert_not_called()

    def test_chat_timer_runs_while_main_thread_handles_long_interrupt(self):
        entered = threading.Event()
        polled = threading.Event()
        stop = watchdog.run_every(0.01, lambda: polled.set() if entered.is_set() else None)
        def login():
            entered.set()
            self.assertTrue(polled.wait(1), "Chat polling blocked by relogin")
        try:
            watchdog.request_interrupt(login)
            watchdog.run_interrupts()
        finally:
            stop.set()


if __name__ == "__main__":
    unittest.main()
