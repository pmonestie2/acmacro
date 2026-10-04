import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import failure_capture


class FailureCaptureTests(unittest.TestCase):
    def test_native_client_capture_and_unique_files(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(failure_capture, 'DIAGNOSTICS_DIR', Path(folder)), \
             patch.object(failure_capture, 'find_window_exact', return_value=123), \
             patch.object(failure_capture, 'client_geometry', return_value=((1280, 768), (1920, 1152), (3, 48))), \
             patch.object(failure_capture.pag, 'screenshot', return_value=Image.new('RGB', (1920, 1152))) as capture, \
             patch.object(failure_capture, 'log_event'):
            first = failure_capture.capture_failure(RuntimeError('sector unknown'))
            second = failure_capture.capture_failure(RuntimeError('sector unknown'))
            capture.assert_called_with(region=(3, 48, 1920, 1152))
            self.assertNotEqual(first, second)
            with Image.open(first) as saved:
                self.assertEqual(saved.size, (1920, 1152))
            self.assertTrue(second.is_file())

    def test_missing_window_captures_desktop(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(failure_capture, 'DIAGNOSTICS_DIR', Path(folder)), \
             patch.object(failure_capture, 'find_window_exact', return_value=None), \
             patch.object(failure_capture.pag, 'screenshot', return_value=Image.new('RGB', (20, 20))) as capture, \
             patch.object(failure_capture, 'log_event'):
            result = failure_capture.capture_failure(RuntimeError('window lost'))
            capture.assert_called_once_with(region=None)
            self.assertTrue(result.name.endswith('_desktop.png'))

    def test_capture_error_does_not_escape(self):
        with patch.object(failure_capture, 'find_window_exact', return_value=None), \
             patch.object(failure_capture.pag, 'screenshot', side_effect=OSError('capture unavailable')), \
             patch.object(failure_capture, 'log_event') as log:
            self.assertIsNone(failure_capture.capture_failure(RuntimeError('original failure')))
            self.assertIn('original failure', log.call_args.args[0])
