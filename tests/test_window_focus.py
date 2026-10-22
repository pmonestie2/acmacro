import unittest
from unittest.mock import patch
import gui


class WindowFocusTests(unittest.TestCase):
    def test_silent_refusal_uses_fallback_and_verifies_settled_focus(self):
        current = [9]
        attempts = [0]
        def activate(hwnd):
            attempts[0] += 1
            if attempts[0] == 2:
                current[0] = hwnd
        with patch("gui.win32gui.IsWindow", return_value=True), \
             patch("gui.win32gui.IsIconic", return_value=False), \
             patch("gui.win32gui.GetForegroundWindow", side_effect=lambda: current[0]), \
             patch("gui.win32gui.SetForegroundWindow", side_effect=activate), \
             patch("gui.win32api.GetCurrentThreadId", return_value=1), \
             patch("gui.win32process.GetWindowThreadProcessId", return_value=(2, 10)), \
             patch("gui.win32gui.PeekMessage"), patch("gui.win32gui.BringWindowToTop"), \
             patch.object(gui._user32, "AttachThreadInput", return_value=1) as attach, \
             patch("gui.SLEEP_FN") as sleep:
            self.assertTrue(gui.focus_window(123))
        self.assertEqual(attach.call_args_list[0].args, (1, 2, True))
        self.assertEqual(attach.call_args_list[-1].args, (1, 2, False))
        sleep.assert_any_call(gui.FOCUS_SETTLE_TIME)

    def test_persistent_refusal_is_bounded(self):
        with patch("gui.win32gui.IsWindow", return_value=True), \
             patch("gui.win32gui.IsIconic", return_value=False), \
             patch("gui.win32gui.GetForegroundWindow", return_value=0), \
             patch("gui.win32gui.SetForegroundWindow") as activate, \
             patch("gui.win32api.GetCurrentThreadId", return_value=1), \
             patch("gui.win32gui.PeekMessage"), patch("gui.win32gui.BringWindowToTop"), \
             patch("gui.SLEEP_FN"):
            self.assertFalse(gui.focus_window(123))
        self.assertEqual(activate.call_count, 8)

    def test_already_focused_window_skips_retries_and_delay(self):
        with patch("gui.win32gui.IsWindow", return_value=True), \
             patch("gui.win32gui.IsIconic", return_value=False), \
             patch("gui.win32gui.GetForegroundWindow", return_value=123), \
             patch("gui.SLEEP_FN") as sleep:
            self.assertTrue(gui.focus_window(123))
        sleep.assert_not_called()
