import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol.capture_jobs import write_status


class CaptureStatusTests(unittest.TestCase):
    def test_status_survives_transient_windows_reader_lock(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'capture.status.json'
            path.write_text('{"state":"old"}')
            replace = Path.replace
            attempts = 0

            def locked_once(source: Path, destination: Path) -> Path:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError('Windows reader still holds destination')
                return replace(source, destination)

            with patch.object(Path, 'replace', locked_once), patch('protocol.capture_jobs.time.sleep'):
                self.assertTrue(write_status(path, {'state': 'running', 'packets': 12}))
            self.assertEqual(json.loads(path.read_text()), {'state': 'running', 'packets': 12})

    def test_persistent_reader_lock_preserves_previous_status_without_crashing_capture(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'capture.status.json'
            path.write_text('{"state":"old"}')
            with patch.object(Path, 'replace', side_effect=PermissionError('locked')), patch('protocol.capture_jobs.time.sleep'):
                self.assertFalse(write_status(path, {'state': 'running'}))
            self.assertEqual(json.loads(path.read_text()), {'state': 'old'})
            self.assertTrue(write_status(path, {'state': 'running'}))
            self.assertEqual(json.loads(path.read_text()), {'state': 'running'})
