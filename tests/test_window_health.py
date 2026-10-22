from __future__ import annotations

import unittest
from unittest.mock import patch

import win32con
import win32gui

from window_health import WindowProbeTimeout, check_window_responsive


class WindowHealthTests(unittest.TestCase):
    def test_healthy_window_uses_bounded_null_message_without_focus(self) -> None:
        with patch('window_health.win32gui.IsWindow', return_value=True), \
                patch('window_health.win32gui.IsWindowVisible', return_value=True), \
                patch('window_health.win32process.GetWindowThreadProcessId', return_value=(1, 123)), \
                patch('window_health.process_identity', return_value='born'), \
                patch('window_health.win32gui.SendMessageTimeout') as probe:
            check_window_responsive(10, 123, 'born')
        probe.assert_called_once_with(10, win32con.WM_NULL, 0, 0,
                                      win32con.SMTO_ABORTIFHUNG | win32con.SMTO_BLOCK, 250)

    def test_missing_window_is_rejected_before_probe(self) -> None:
        for window in (None, 10):
            with self.subTest(window=window), \
                    patch('window_health.win32gui.IsWindow', return_value=False), \
                    patch('window_health.win32gui.SendMessageTimeout') as probe:
                with self.assertRaisesRegex(RuntimeError, 'disappeared'):
                    check_window_responsive(window, 123, 'born')
                probe.assert_not_called()

    def test_reused_handle_or_pid_does_not_probe_another_client(self) -> None:
        for pid, born in ((999, 'born'), (123, 'different-birth')):
            with self.subTest(pid=pid, born=born), \
                    patch('window_health.win32gui.IsWindow', return_value=True), \
                    patch('window_health.win32gui.IsWindowVisible', return_value=True), \
                    patch('window_health.win32process.GetWindowThreadProcessId', return_value=(1, pid)), \
                    patch('window_health.process_identity', return_value=born), \
                    patch('window_health.win32gui.SendMessageTimeout') as probe:
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    check_window_responsive(10, 123, 'born')
                probe.assert_not_called()

    def test_hung_ui_is_rejected_even_with_live_process(self) -> None:
        with patch('window_health.win32gui.IsWindow', return_value=True), \
                patch('window_health.win32gui.IsWindowVisible', return_value=True), \
                patch('window_health.win32process.GetWindowThreadProcessId', return_value=(1, 123)), \
                patch('window_health.process_identity', return_value='born'), \
                patch('window_health.win32gui.SendMessageTimeout',
                      side_effect=win32gui.error(1460, 'SendMessageTimeout', 'timeout')):
            with self.assertRaisesRegex(WindowProbeTimeout, 'did not answer.*1460'):
                check_window_responsive(10, 123, 'born')

    def test_window_disappearing_during_probe_is_reported(self) -> None:
        with patch('window_health.win32gui.IsWindow', side_effect=win32gui.error(1400, 'IsWindow', 'gone')):
            with self.assertRaisesRegex(RuntimeError, 'unavailable.*1400'):
                check_window_responsive(10, 123, 'born')
