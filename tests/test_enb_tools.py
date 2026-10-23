from __future__ import annotations

from contextlib import nullcontext
from collections.abc import Callable
from pathlib import Path
import tempfile
import io
import unittest
from unittest.mock import Mock, patch

import enb
import start_game


class DispatcherTests(unittest.TestCase):
    def command(self, *arguments: str) -> list[str]:
        return enb.command_line(enb.parser().parse_args(arguments))

    def test_start_name_and_paths_are_preserved_without_shell(self) -> None:
        command = self.command('start', 'ENB_a', '--client', 'folder with spaces/client.exe')
        self.assertEqual(Path(command[1]).name, 'start_game.py')
        self.assertEqual(command[2], 'ENB_a')
        self.assertEqual(command[-2:], ['--client', str(Path('folder with spaces/client.exe').resolve())])

    def test_start_config_is_absolute_before_dispatch_changes_working_directory(self) -> None:
        command = self.command('start', './elsewhere/mbboytt.conf', '--check')
        self.assertEqual(command[2], str(Path('./elsewhere/mbboytt.conf').resolve()))
        self.assertEqual(command[-1], '--check')

    def test_find_combines_words_as_one_search(self) -> None:
        command = self.command('grep', 'orb', 'wever')
        self.assertEqual(command[-2:], ['--grep', 'orb wever'])

    def test_trade_exact_title_id_and_dialogue(self) -> None:
        command = self.command('trade', 'ENB_macboytt', '28', '--dialogue')
        self.assertEqual(command[2:], ['ENB_macboytt', '--id', '28', '--dialogue'])

    def test_kill_is_player_scoped_and_list_is_forwarded(self) -> None:
        command = self.command('kill', 'ENB_a', '--list')
        self.assertEqual(Path(command[1]).name, 'kill_player.py')
        self.assertEqual(command[2:], ['ENB_a', '--list'])

    def test_relative_config_resolved_before_child_working_directory_changes(self) -> None:
        command = self.command('kill', './elsewhere/config_player_x.txt')
        self.assertEqual(command[2], str(Path('./elsewhere/config_player_x.txt').resolve()))

    def test_layout_and_fixed_and_windows(self) -> None:
        self.assertEqual(Path(self.command('layout')[1]).name, 'relayout.py')
        self.assertEqual(Path(self.command('layout', '--fixed')[1]).name, 'arrange_windows.py')
        self.assertEqual(self.command('windows')[-1], '--list')
        with self.assertRaises(ValueError):
            self.command('layout', '--fixed', '--list')

    def test_vault_alias_and_controls(self) -> None:
        self.assertEqual(self.command('vault', 'ENB_macboyje', '--list')[2:],
                         [str(enb.ROOT / 'config_player_macboyje.txt'), '--list'])
        self.assertEqual(self.command('pause', 'ENB_macboyje')[2:], ['pause', 'macboyje'])
        self.assertEqual(self.command('resume')[2:], ['resume'])

    def test_vault_withdrawal_and_deposit_preview_are_forwarded(self) -> None:
        self.assertEqual(self.command('vault', 'macboyje', '--withdraw', '3302', '--quantity', '2')[3:],
                         ['--withdraw', '3302', '--quantity', '2'])
        self.assertEqual(self.command('vault', 'macboyje', '--all', '--dry-run')[-1], '--dry-run')
        with self.assertRaises(ValueError):
            self.command('vault', 'macboyje', '--quantity', '2')

    def test_vault_grep_filters_deposits_and_listings(self) -> None:
        self.assertEqual(self.command('vault', 'macboyje', '--grep', 'white dragon')[3:],
                         ['--grep', 'white dragon'])
        self.assertEqual(self.command('vault', 'macboyje', '--list', '--grep', '775')[3:],
                         ['--list', '--grep', '775'])
        for options in ([], ['--grep', ''], ['--all', '--grep', 'laser'],
                        ['--withdraw', '775', '--grep', 'laser']):
            with self.assertRaises(ValueError):
                self.command('vault', 'macboyje', *options)

    def test_vault_stack_option_is_forwarded_and_requires_withdrawal(self) -> None:
        self.assertEqual(self.command('vault', 'macboyje', '--withdraw', '775', '--stack')[3:],
                         ['--withdraw', '775', '--stack'])
        with self.assertRaisesRegex(ValueError, '--stack requires --withdraw'):
            self.command('vault', 'macboyje', '--list', '--stack')

    def test_dispatch_propagates_exit_code(self) -> None:
        with patch('enb.subprocess.run', return_value=Mock(returncode=7)) as run:
            self.assertEqual(enb.main(['find', 'Julie']), 7)
        self.assertEqual(run.call_args.kwargs, {'cwd': enb.ROOT, 'check': False})

    def test_manufacture_forwards_batch_count(self) -> None:
        command = self.command('manufacture', 'ENB_macboyje', '--batches', '0')
        self.assertEqual(Path(command[1]).name, 'manufacture_batch.py')
        self.assertEqual(command[2:], ['ENB_macboyje', '--batches', '0', '--parallel', '1'])
        with self.assertRaises(ValueError):
            self.command('manufacture', 'ENB_macboyje', '--batches', '-1')

    def test_manufacture_parallel_accepts_user_selected_positive_numbers(self) -> None:
        for count in (1, 2, 4, 8, 40, 100):
            command = self.command('manufacture', 'ENB_macboyje', '--parallel', str(count))
            self.assertEqual(command[-2:], ['--parallel', str(count)])
        for count in (0, -1):
            with self.assertRaises(ValueError):
                self.command('manufacture', 'ENB_macboyje', '--parallel', str(count))


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.client = Path(directory.name) / 'client.exe'
        self.proxy = Path(directory.name) / 'net7proxy.exe'
        self.client.touch()
        self.proxy.touch()
        targets = {
            'start_lock': {'return_value': nullcontext()},
            'title_exists': {'return_value': False},
            'process_identity': {'side_effect': lambda pid: '2026-10-21 00:00:01' if pid == 200 else '2026-10-21 00:00:02'},
            'launched_windows': {'return_value': [(10, 300, '2026-10-21 00:00:02')]},
            'parent_process_id': {'return_value': 200},
            'win32process.GetWindowThreadProcessId': {'return_value': (1, 300)},
            'win32gui.SetWindowText': {},
            'win32gui.GetWindowText': {'return_value': 'ENB_a'},
            'subprocess.Popen': {},
            'time.sleep': {},
        }
        self.mocks: dict[str, Mock] = {}
        for target, options in targets.items():
            patcher = patch(f'start_game.{target}', **options)
            self.mocks[target] = patcher.start()
            self.addCleanup(patcher.stop)
        self.process = self.mocks['subprocess.Popen'].return_value
        self.process.pid = 200
        self.process.poll.return_value = None

    def test_title_normalization_and_invalid_names(self) -> None:
        self.assertEqual(start_game.window_title('a'), 'ENB_a')
        self.assertEqual(start_game.window_title(' enb_a '), 'ENB_a')
        for name in ('', 'ENB_', '../a', 'a b'):
            with self.assertRaises(ValueError):
                start_game.window_title(name)

    def test_start_launches_one_proxy_in_its_directory_and_renames_its_client(self) -> None:
        result = start_game.start_game('ENB_a', self.client, self.proxy)
        self.assertEqual((result.title, result.client_pid, result.proxy_pid), ('ENB_a', 300, 200))
        self.mocks['win32gui.SetWindowText'].assert_called_once_with(10, 'ENB_a')
        self.assertEqual(self.mocks['subprocess.Popen'].call_count, 1)
        self.assertEqual(self.mocks['subprocess.Popen'].call_args.kwargs['cwd'], self.proxy.parent)
        command = self.mocks['subprocess.Popen'].call_args.args[0]
        self.assertIn(f'/CLIENT:{self.client}', command)

    def test_existing_title_prevents_launch(self) -> None:
        self.mocks['title_exists'].return_value = True
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            start_game.start_game('a', self.client, self.proxy)
        self.mocks['subprocess.Popen'].assert_not_called()

    def test_proxy_exit_prevents_rename_and_retry(self) -> None:
        self.process.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'proxy exited'):
            start_game.start_game('a', self.client, self.proxy)
        self.mocks['win32gui.SetWindowText'].assert_not_called()
        self.assertEqual(self.mocks['subprocess.Popen'].call_count, 1)

    def test_timeout_leaves_owned_process_running_without_retry(self) -> None:
        self.mocks['launched_windows'].return_value = []
        with patch('start_game.time.monotonic', side_effect=[0, 11]):
            with self.assertRaisesRegex(TimeoutError, 'left running'):
                start_game.start_game('a', self.client, self.proxy, 10)
        self.process.terminate.assert_not_called()
        self.assertEqual(self.mocks['subprocess.Popen'].call_count, 1)

    def test_changed_client_identity_prevents_rename(self) -> None:
        self.mocks['win32process.GetWindowThreadProcessId'].return_value = (1, 999)
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            start_game.start_game('a', self.client, self.proxy)
        self.mocks['win32gui.SetWindowText'].assert_not_called()

    def test_title_collision_during_launch_does_not_rename_other_window(self) -> None:
        self.mocks['title_exists'].side_effect = [False, True]
        with self.assertRaisesRegex(RuntimeError, 'appeared during launch'):
            start_game.start_game('a', self.client, self.proxy)
        self.mocks['win32gui.SetWindowText'].assert_not_called()

    def test_missing_executable_does_not_launch(self) -> None:
        self.client.unlink()
        with self.assertRaises(FileNotFoundError):
            start_game.start_game('a', self.client, self.proxy)
        self.mocks['subprocess.Popen'].assert_not_called()

    def test_invalid_timeouts_never_launch(self) -> None:
        for timeout in (-1, 0, 301, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                start_game.start_game('a', self.client, self.proxy, timeout)
        self.mocks['subprocess.Popen'].assert_not_called()


class LaunchTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def config(self, *, client_exe: str = '', game_dir: str = '') -> Path:
        import configparser
        path = self.root / 'misleading.conf'
        data = configparser.ConfigParser(interpolation=None)
        data['player'] = {'name': 'actualtoon', 'player_type': 'tt', 'character_slot': '0',
                          'username': 'private-user-do-not-print', 'password': 'private-password-do-not-print',
                          'mission_count': '8', 'low_job': 'false', 'avoid_pit': 'false'}
        if client_exe:
            data['player']['client_exe'] = client_exe
        if game_dir:
            data['player']['game_dir'] = game_dir
        with path.open('w', encoding='utf-8') as stream:
            data.write(stream)
        return path

    def test_config_uses_character_name_not_filename(self) -> None:
        target = start_game.resolve_target(str(self.config()))
        self.assertEqual(target.title, 'ENB_actualtoon')

    def test_config_client_paths_resolve_relative_to_config_directory(self) -> None:
        path = self.config(game_dir='another install')
        target = start_game.resolve_target(str(path))
        self.assertEqual(target.client, (self.root / 'another install/release/client.exe').resolve())

    def test_explicit_client_override_wins_over_config(self) -> None:
        path = self.config(client_exe='configured/client.exe', game_dir='ignored install')
        target = start_game.resolve_target(str(path))
        self.assertEqual(target.client, (self.root / 'configured/client.exe').resolve())
        override = self.root / 'override/client.exe'
        self.assertEqual(start_game.resolve_target(str(path), override).client, override.resolve())

    def test_plain_name_still_needs_no_config(self) -> None:
        with patch('start_game.load_player_config') as load:
            self.assertEqual(start_game.resolve_target('ENB_a').title, 'ENB_a')
        load.assert_not_called()

    def test_missing_config_fails_without_launch(self) -> None:
        with patch('start_game.subprocess.Popen') as launch:
            with self.assertRaises(FileNotFoundError):
                start_game.resolve_target(str(self.root / 'missing.conf'))
        launch.assert_not_called()

    def test_invalid_config_error_does_not_expose_source_secrets(self) -> None:
        path = self.root / 'invalid.conf'
        path.write_text('password = secret-must-not-appear\n', encoding='utf-8')
        with self.assertRaises(ValueError) as raised:
            start_game.resolve_target(str(path))
        self.assertNotIn('secret-must-not-appear', str(raised.exception))

    def test_check_does_not_launch_or_print_credentials(self) -> None:
        output = io.StringIO()
        with patch('start_game.subprocess.Popen') as launch, patch('sys.stdout', output):
            code = start_game.main([str(self.config()), '--proxy', str(self.root / 'net7proxy.exe'), '--check'])
        self.assertEqual(code, 0)
        self.assertIn('ENB_actualtoon', output.getvalue())
        self.assertNotIn('private-user-do-not-print', output.getvalue())
        self.assertNotIn('private-password-do-not-print', output.getvalue())
        launch.assert_not_called()


class WindowDiscoveryTests(unittest.TestCase):
    def test_exact_title_does_not_match_another_characters_prefix(self) -> None:
        from gui import find_window_title
        titles = {1: 'ENB_apw', 2: 'ENB_A'}
        def enumerate_windows(callback: Callable[[int, object], None], unused: object) -> None:
            for hwnd in titles:
                callback(hwnd, None)
        with patch('gui.win32gui.EnumWindows', side_effect=enumerate_windows), \
                patch('gui.win32gui.IsWindowVisible', return_value=True), \
                patch('gui.win32gui.GetWindowText', side_effect=titles.__getitem__):
            self.assertEqual(find_window_title('ENB_a'), 2)
            self.assertIsNone(find_window_title('ENB_je'))
            titles[1] = 'ENB_a'
            with self.assertRaisesRegex(RuntimeError, 'Multiple'):
                find_window_title('ENB_a')

    def test_only_new_clients_of_the_new_proxy_are_selected(self) -> None:
        client = Path('client.exe').resolve()
        def enumerate_windows(callback: Callable[[int, object], None], unused: object) -> None:
            for hwnd in (1, 2, 3, 4):
                callback(hwnd, None)
        with patch('start_game.win32gui.EnumWindows', side_effect=enumerate_windows), \
                patch('start_game.win32gui.IsWindowVisible', return_value=True), \
                patch('start_game.win32gui.GetClassName', return_value='game'), \
                patch('start_game.win32process.GetWindowThreadProcessId', side_effect=lambda hwnd: (1, hwnd)), \
                patch('start_game.parent_process_id', side_effect=lambda pid: 20 if pid != 2 else 99), \
                patch('start_game.process_identity', side_effect=lambda pid: '2026-02' if pid != 3 else '2025-01'), \
                patch('start_game.client_path', side_effect=lambda pid: client if pid != 4 else Path('other.exe')):
            self.assertEqual(start_game.launched_windows(20, '2026-01', client), [(1, 1, '2026-02')])


if __name__ == '__main__':
    unittest.main()
