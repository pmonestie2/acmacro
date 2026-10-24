"""Mocked process-exit races; no server/client starts or stops."""
from __future__ import annotations

import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from restart_server import process_identity, restart, wait_ready


class RestartDeploymentTests(unittest.TestCase):
    """Real temporary files, mocked OS process ownership; no live game input."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / 'state'
        self.state.mkdir()
        self.binary = self.root / 'target/debug/enb-server.exe'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'old executable')
        self.staged = self.root / 'target/restart-build/debug/enb-server.exe'
        self.staged.parent.mkdir(parents=True)
        self.staged.write_bytes(b'new executable')
        self.identity = (self.binary, 100.)
        (self.state / 'server.local.json').write_text(json.dumps(dict(
            pid=1234, started_at=100., bind='127.0.0.18',
            command=[str(self.binary), '--state-dir', str(self.state)])))
        self.addCleanup(patch.stopall)
        patch('restart_server.ROOT', self.root).start()
        self.process_identity = patch('restart_server.process_identity',
            side_effect=[self.identity, self.identity, None]).start()
        self.build = patch('restart_server.build', side_effect=self.build_before_stop).start()
        self.replacement = Mock(pid=2345)
        self.replacement.poll.return_value = None
        self.launch = patch('restart_server.launch', return_value=self.replacement).start()
        self.ready = patch('restart_server.wait_ready').start()
        self.registered = patch('restart_server.registered', return_value=False).start()

    def build_before_stop(self, *, target_dir: Path) -> Path:
        self.assertFalse((self.state / 'stop.request').exists())
        self.assertEqual(self.binary.read_bytes(), b'old executable')
        self.assertEqual(target_dir, self.root / 'target/restart-build')
        return self.staged

    def test_finished_build_is_installed_after_owned_shutdown(self) -> None:
        self.assertEqual(restart(self.state, no_build=False), 2345)
        self.assertTrue((self.state / 'stop.request').exists())
        self.assertEqual(self.binary.read_bytes(), b'new executable')
        self.assertEqual((self.root / 'target/restart-fallback/enb-server.exe').read_bytes(), b'old executable')
        self.launch.assert_called_once()
        self.assertEqual(self.launch.call_args.args[0][0], str(self.binary))
        self.assertFalse(list(self.binary.parent.glob('*.next-*')))

    def test_build_failure_leaves_playable_server_untouched(self) -> None:
        self.build.side_effect = RuntimeError('compiler failed')
        with self.assertRaisesRegex(RuntimeError, 'compiler failed'):
            restart(self.state, no_build=False)
        self.assertFalse((self.state / 'stop.request').exists())
        self.assertEqual(self.binary.read_bytes(), b'old executable')
        self.launch.assert_not_called()

    def test_changed_process_after_build_is_not_stopped_or_replaced(self) -> None:
        self.process_identity.side_effect = [self.identity, None]
        with self.assertRaisesRegex(RuntimeError, 'changed while building'):
            restart(self.state, no_build=False)
        self.assertFalse((self.state / 'stop.request').exists())
        self.launch.assert_not_called()

    def test_terminal_new_process_failure_restores_old_executable(self) -> None:
        failed = Mock(pid=2344)
        failed.poll.return_value = 2
        self.launch.side_effect = [failed, self.replacement]
        self.ready.side_effect = [RuntimeError('startup exited'), None]
        with self.assertRaisesRegex(RuntimeError, 'previous server is running'):
            restart(self.state, no_build=False)
        self.assertEqual(self.launch.call_count, 2)
        backup = Path(self.launch.call_args.args[0][0])
        self.assertEqual(backup.read_bytes(), b'old executable')

    def test_live_readiness_timeout_never_launches_a_duplicate(self) -> None:
        self.ready.side_effect = TimeoutError('still loading')
        with self.assertRaisesRegex(TimeoutError, 'still loading'):
            restart(self.state, no_build=False)
        self.launch.assert_called_once()
        self.replacement.terminate.assert_not_called()
        self.registered.assert_not_called()

    def test_no_build_restarts_same_image_without_linking(self) -> None:
        self.assertEqual(restart(self.state, no_build=True), 2345)
        self.build.assert_not_called()
        self.assertEqual(self.binary.read_bytes(), b'old executable')


class ReadinessTests(unittest.TestCase):
    def test_live_timeout_is_an_observation_failure_without_process_termination(self) -> None:
        process = Mock(pid=42)
        process.poll.return_value = None
        with patch('restart_server.registered', return_value=False), \
             patch('restart_server.time.monotonic', side_effect=[0., 31.]):
            with self.assertRaisesRegex(TimeoutError, 'still running'):
                wait_ready(process, '127.0.0.18')
        process.terminate.assert_not_called()

    def test_a_terminal_process_is_not_declared_ready_by_another_listener(self) -> None:
        process = Mock(pid=42)
        process.poll.return_value = 2
        with patch('restart_server.registered', return_value=True) as registered:
            with self.assertRaisesRegex(RuntimeError, 'exited with code 2'):
                wait_ready(process, '127.0.0.18')
        registered.assert_not_called()


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
