"""Launcher ownership checks; never start or control a game client."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Tools'))
import play_local


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        """Create an isolated project and state directory."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.state = self.project / '.local-state'
        self.state.mkdir()
        self.client = play_local.installed_executable()

    def test_existing_client_activates_without_build_or_log_changes(self) -> None:
        """A repeat launch targets only this project's existing application."""
        log = self.project / 'server.log'
        log.write_text('original session diagnostics\n')
        with patch.object(play_local.subprocess, 'check_output', return_value=str(self.client) + '\n'), \
                patch.object(play_local.subprocess, 'run') as activate, \
                patch.object(play_local, 'run_game') as start:
            play_local.launch(self.project, None, False, False)
        start.assert_not_called()
        activate.assert_called_once_with(['/usr/bin/open', '-a', str(self.client.parents[2])], check=True)
        self.assertEqual(log.read_text(), 'original session diagnostics\n')

    def test_other_project_does_not_count_as_existing_client(self) -> None:
        """Matching uses the entire executable path, not the application name."""
        with patch.object(play_local.subprocess, 'check_output', return_value='/other/42 and Beyond\n'):
            self.assertFalse(play_local.running_client(self.client))

    def test_busy_launcher_does_not_start_second_server(self) -> None:
        """Concurrent launches cannot race while the first is still starting."""
        owner = play_local.LaunchLock(self.state / 'launcher.lock')
        self.assertTrue(owner.acquire())
        try:
            with patch.object(play_local, 'show_existing', return_value=False), \
                    patch.object(play_local, 'run_game') as start:
                play_local.launch(self.project, None, False, False)
            start.assert_not_called()
        finally:
            owner.release()

    def test_failure_releases_lock(self) -> None:
        """A failed launch must permit a later retry."""
        with patch.object(play_local, 'show_existing', return_value=False), \
                patch.object(play_local, 'run_game', side_effect=RuntimeError('failed')):
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                play_local.launch(self.project, None, False, False)
        retry = play_local.LaunchLock(self.state / 'launcher.lock')
        try:
            self.assertTrue(retry.acquire())
        finally:
            retry.release()

    def test_process_exit_releases_lock(self) -> None:
        """An abruptly terminated launcher cannot leave a stale held lock."""
        code = (
            'import sys; from pathlib import Path; '
            'sys.path.insert(0, sys.argv[1]); from play_local import LaunchLock; '
            'lock=LaunchLock(Path(sys.argv[2])); assert lock.acquire(); '
            'print("locked", flush=True); sys.stdin.read()'
        )
        process = subprocess.Popen([sys.executable, '-c', code, str(Path(play_local.__file__).parent),
                                    str(self.state / 'launcher.lock')],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            probe = play_local.LaunchLock(self.state / 'launcher.lock')
            self.assertFalse(probe.acquire())
            process.terminate()
            process.wait(timeout=5)
            try:
                self.assertTrue(probe.acquire())
            finally:
                probe.release()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()

    def test_expected_error_has_no_traceback(self) -> None:
        """A port conflict becomes a concise actionable failure."""
        with patch.object(sys, 'argv', ['play_local.py']), \
                patch.object(play_local, 'launch', side_effect=RuntimeError('ports already in use')), \
                patch.object(sys, 'stderr') as errors:
            with self.assertRaises(SystemExit) as caught:
                play_local.main()
        self.assertEqual(caught.exception.code, 1)
        message = ''.join(str(call.args[0]) for call in errors.write.call_args_list)
        self.assertIn('ports already in use', message)
        self.assertNotIn('Traceback', message)


if __name__ == '__main__':
    unittest.main()
