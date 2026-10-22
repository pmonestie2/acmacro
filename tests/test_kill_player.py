from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from kill_player import ProcessInfo, macro_player, named_clients, resolve_player, stop_player


class KillPlayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processes = {
            10: ProcessInfo(10, 1, 'python.exe', '', '2026-10-20 00:00:00'),
            11: ProcessInfo(11, 1, 'python.exe', '', '2026-10-20 00:00:01'),
            20: ProcessInfo(20, 30, 'client.exe', '', '2026-10-20 00:02:00'),
            21: ProcessInfo(21, 31, 'client.exe', '', '2026-10-20 00:02:01'),
            22: ProcessInfo(22, 32, 'client.exe', '', '2026-10-20 00:02:02'),
            30: ProcessInfo(30, 1, 'net7proxy.exe', '', '2026-10-20 00:01:00'),
            31: ProcessInfo(31, 1, 'net7proxy.exe', '', '2026-10-20 00:01:01'),
            32: ProcessInfo(32, 1, 'net7proxy.exe', '', '2026-10-20 00:01:02'),
        }
        patches = {
            'process_snapshot': {'return_value': self.processes},
            'named_clients': {'return_value': {20, 22}},
            'collect_claims': {'return_value': [
                {'pid': 10, 'born': self.processes[10].born, 'toon': 'MacBoyJE'},
                {'pid': 11, 'born': self.processes[11].born, 'toon': 'macboypw'}]},
            'active_statuses': {'return_value': []},
            'macro_player': {'return_value': None},
            'client_path': {'side_effect': lambda pid: Path(self.processes[pid].name)},
            'process_identity': {'side_effect': lambda pid: self.processes[pid].born},
            'terminate': {'return_value': True},
        }
        self.mocks: dict[str, Mock] = {}
        for name, options in patches.items():
            patched = patch(f'kill_player.{name}', **options)
            self.mocks[name] = patched.start()
            self.addCleanup(patched.stop)

    def test_all_matching_clients_and_proxies_stop_after_only_their_macro(self) -> None:
        output = stop_player('macboyje')
        self.assertEqual([c.args[0] for c in self.mocks['terminate'].call_args_list], [10, 20, 22, 30, 32])
        self.assertEqual(len(output), 5)

    def test_list_does_not_terminate(self) -> None:
        self.assertEqual(len(stop_player('macboyje', list_only=True)), 5)
        self.mocks['terminate'].assert_not_called()

    def test_shared_proxy_is_preserved(self) -> None:
        self.processes[21] = ProcessInfo(21, 30, 'client.exe', '', '2026-10-20 00:02:01')
        stop_player('macboyje')
        self.assertNotIn(30, [c.args[0] for c in self.mocks['terminate'].call_args_list])

    def test_reused_parent_pid_is_preserved(self) -> None:
        self.processes[30] = ProcessInfo(30, 1, 'net7proxy.exe', '', '2026-10-20 00:03:00')
        stop_player('macboyje')
        self.assertNotIn(30, [c.args[0] for c in self.mocks['terminate'].call_args_list])

    def test_stale_macro_claim_is_not_stopped(self) -> None:
        self.mocks['collect_claims'].return_value[0]['born'] = 'old birth'
        stop_player('macboyje')
        self.assertNotIn(10, [c.args[0] for c in self.mocks['terminate'].call_args_list])

    def test_unregistered_macro_config_is_matched(self) -> None:
        self.mocks['collect_claims'].return_value = []
        self.mocks['macro_player'].side_effect = ['macboyje', 'macboypw']
        stop_player('macboyje')
        self.assertEqual(self.mocks['terminate'].call_args_list[0].args[0], 10)

    def test_unknown_player_does_not_fall_back_to_all_games(self) -> None:
        self.mocks['named_clients'].return_value = set()
        self.assertIn('No matching', stop_player('unknown')[0])
        self.mocks['terminate'].assert_not_called()

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            stop_player('  ')
        self.mocks['process_snapshot'].assert_not_called()

    def test_config_path_selects_its_character(self) -> None:
        with patch('kill_player.load_player_config', return_value=Mock(name='settings')) as load:
            load.return_value.name = 'macboyje'
            stop_player(r'.\config_player_macboyje.txt')
        self.mocks['named_clients'].assert_called_once_with('macboyje', self.processes)
        self.assertEqual([c.args[0] for c in self.mocks['terminate'].call_args_list], [10, 20, 22, 30, 32])

    def test_missing_config_stops_before_process_discovery(self) -> None:
        with patch('kill_player.load_player_config', side_effect=FileNotFoundError('missing')):
            with self.assertRaisesRegex(ValueError, 'Cannot read player config'):
                stop_player(r'.\missing.txt')
        self.mocks['process_snapshot'].assert_not_called()
        self.mocks['terminate'].assert_not_called()


class PlayerDiscoveryTests(unittest.TestCase):
    def test_config_name_is_read_instead_of_guessed_from_filename(self) -> None:
        with patch('kill_player.load_player_config', return_value=Mock(name='settings')) as load:
            load.return_value.name = 'ActualToon'
            self.assertEqual(resolve_player('config_player_macboytt.txt'), 'ActualToon')
            self.assertTrue(load.call_args.args[0].is_absolute())

    def test_plain_character_name_needs_no_config(self) -> None:
        with patch('kill_player.load_player_config') as load:
            self.assertEqual(resolve_player(' macboytt '), 'macboytt')
        load.assert_not_called()

    def test_window_title_resolves_to_character(self) -> None:
        with patch('kill_player.load_player_config') as load:
            self.assertEqual(resolve_player(' ENB_macboytt '), 'macboytt')
            self.assertEqual(resolve_player('enb_MacBoyJE'), 'MacBoyJE')
        load.assert_not_called()

    def test_empty_window_character_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_player('ENB_')

    def test_exact_titles_match_multiple_windows_without_prefix_matching(self) -> None:
        processes = {pid: ProcessInfo(pid, 1, 'client.exe', '', 'born') for pid in (20, 21, 22)}
        titles = {1: 'ENB_MacBoyJE', 2: 'ENB_macboyje', 3: 'ENB_macboyje2'}
        def enumerate_windows(callback: Callable[[int, object], None], unused: object) -> None:
            for hwnd in titles:
                callback(hwnd, None)
        with patch('kill_player.win32gui.EnumWindows', side_effect=enumerate_windows), \
                patch('kill_player.win32gui.GetWindowText', side_effect=titles.__getitem__), \
                patch('kill_player.win32process.GetWindowThreadProcessId', side_effect=lambda hwnd: (1, hwnd + 19)), \
                patch('kill_player.process_identity', return_value='born'), \
                patch('kill_player.client_path', return_value=Path('client.exe')):
            self.assertEqual(named_clients('macboyje', processes), {20, 21})

    def test_macro_config_determines_character_and_unrelated_scripts_are_ignored(self) -> None:
        with patch('kill_player.load_player_config', return_value=Mock(name='settings')) as load:
            load.return_value.name = 'macboyje'
            self.assertEqual(macro_player(r'python C:\MACROS\acmacro\vendor.py C:\configs\je.txt'), 'macboyje')
            self.assertIsNone(macro_player(r'python other.py C:\configs\je.txt'))
            self.assertEqual(load.call_count, 1)


if __name__ == '__main__':
    unittest.main()
