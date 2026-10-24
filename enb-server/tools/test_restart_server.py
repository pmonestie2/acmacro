"""Mocked process-exit races; no server/client starts or stops."""
from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from restart_server import process_identity


@unittest.skipUnless(os.name == 'nt','Windows process handle lifecycle')
class RestartIdentityTests(unittest.TestCase):
    def test_query_failure_after_exit_is_terminal_only_when_handle_is_signaled(self) -> None:
        import win32event
        handle = Mock()
        handle.__int__ = Mock(return_value=123)
        with patch('win32api.OpenProcess',return_value=handle), \
             patch('local_windows.process_path',side_effect=OSError(5,'exiting process')), \
             patch('win32event.WaitForSingleObject',side_effect=[win32event.WAIT_TIMEOUT,win32event.WAIT_OBJECT_0]):
            self.assertIsNone(process_identity(1234))
        handle.Close.assert_called_once()

    def test_live_process_query_failure_is_not_reported_as_missing(self) -> None:
        import win32event
        handle = Mock()
        handle.__int__ = Mock(return_value=123)
        with patch('win32api.OpenProcess',return_value=handle), \
             patch('local_windows.process_path',side_effect=OSError(5,'query denied')), \
             patch('win32event.WaitForSingleObject',return_value=win32event.WAIT_TIMEOUT):
            with self.assertRaises(OSError):
                process_identity(1234)
        handle.Close.assert_called_once()

    def test_open_access_denied_is_not_reported_as_missing(self) -> None:
        with patch('win32api.OpenProcess',side_effect=OSError(5,'access denied')):
            with self.assertRaises(OSError):
                process_identity(1234)
        with patch('win32api.OpenProcess',side_effect=OSError(87,'invalid PID')):
            self.assertIsNone(process_identity(1234))
