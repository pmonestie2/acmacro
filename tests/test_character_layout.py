from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from arrange_windows import character_position, find_client
from game_instance import GameInstance
from player import PlayerSettings


class CharacterLayoutTests(unittest.TestCase):
    def test_six_named_positions_ignore_launch_order_and_case(self) -> None:
        positions = {'MACBOYTT': (100, 1000), 'macboyte': (1800, 1000),
                     'macboypw': (1800, 50), 'Macboyje': (100, 50),
                     'MACBOYTS': (950, 50), 'Macboyapw': (950, 1000)}
        with patch('arrange_windows.win32api.MonitorFromPoint', return_value=1), \
                patch('arrange_windows.win32api.GetMonitorInfo', return_value={'Work': (100, 50, 3100, 1800)}), \
                patch('arrange_windows.win32gui.GetWindowRect', return_value=(0, 0, 1300, 800)):
            for name, expected in positions.items():
                with self.subTest(name=name):
                    self.assertEqual(character_position(10, name), expected)
            self.assertIsNone(character_position(10, 'OtherToon'))

    def test_small_work_area_keeps_corners_visible(self) -> None:
        with patch('arrange_windows.win32api.MonitorFromPoint', return_value=1), \
                patch('arrange_windows.win32api.GetMonitorInfo', return_value={'Work': (0, 0, 800, 600)}), \
                patch('arrange_windows.win32gui.GetWindowRect', return_value=(0, 0, 1300, 800)):
            self.assertEqual(character_position(10, 'macboyte'), (0, 0))
            self.assertEqual(character_position(10, 'macboyts'), (0, 0))
            self.assertEqual(character_position(10, 'macboyapw'), (0, 0))

    def test_game_instance_fixed_corner_overrides_config_and_slot(self) -> None:
        settings = PlayerSettings('macboypw', 'pw', 0, 'unused', 8, False, False, 'unused',
                                  window_x=123, window_y=456)
        for shared in (False, True):
            for slot in (0, 1, 2, 3):
                instance = GameInstance(settings, shared_install=shared)
                instance.window, instance.slot = 10, slot
                with patch('game_instance.character_position', return_value=(1800, 0)), \
                        patch('game_instance.win32gui.SetWindowText'), \
                        patch('game_instance.visible_position', side_effect=lambda hwnd, x, y: (x, y)), \
                        patch('game_instance.win32gui.SetWindowPos') as move:
                    instance.layout()
                self.assertEqual(move.call_args.args[2:4], (1800, 0))

    def test_discovery_repairs_moved_named_window_but_does_not_move_it_repeatedly(self) -> None:
        settings = PlayerSettings('macboyje', 'je', 0, 'unused', 8, False, False, 'unused')
        instance = GameInstance(settings, shared_install=True)
        instance.window = 10
        with patch.object(instance, 'valid', return_value=True), \
                patch('game_instance.character_position', return_value=(0, 0)), \
                patch('game_instance.win32gui.GetWindowText', return_value='ENB_macboyje'), \
                patch('game_instance.win32gui.GetWindowRect', side_effect=[(500, 300, 1800, 1100), (0, 0, 1300, 800)]), \
                patch.object(instance, 'layout') as layout:
            instance.discover()
            instance.discover()
        layout.assert_called_once_with()

    def test_shared_install_arrangement_selects_toon_by_title(self) -> None:
        client = Path(r'C:\games\shared\client.exe')
        titles = {10: 'ENB_macboytt', 20: 'ENB_macboyje', 30: 'ENB_macboypw'}
        with patch('arrange_windows.win32gui.EnumWindows', side_effect=lambda callback, arg: [callback(h, arg) for h in titles]), \
                patch('arrange_windows.win32gui.IsWindowVisible', return_value=True), \
                patch('arrange_windows.win32gui.GetWindowText', side_effect=titles.__getitem__), \
                patch('arrange_windows.win32process.GetWindowThreadProcessId', return_value=(1, 100)), \
                patch('arrange_windows.client_path', return_value=client.resolve()):
            self.assertEqual(find_client(client, 'macboyje'), 20)
            with self.assertRaises(RuntimeError):
                find_client(client, 'macboyte')


if __name__ == '__main__':
    unittest.main()
