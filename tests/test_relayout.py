from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from relayout import GameWindow, grid_positions, relayout, window_names


class RelayoutTests(unittest.TestCase):
    def test_two_landscape_windows_go_side_by_side(self) -> None:
        positions = grid_positions([(1300, 800)] * 2, (0, 0, 3200, 1800))
        self.assertEqual(positions, [(0, 500), (1900, 500)])

    def test_portrait_monitor_uses_vertical_layout(self) -> None:
        positions = grid_positions([(800, 600)] * 2, (100, 50, 1100, 2050))
        self.assertEqual(positions, [(200, 50), (200, 1450)])

    def test_four_windows_use_two_rows_and_columns(self) -> None:
        self.assertEqual(grid_positions([(1300, 800)] * 4, (0, 0, 3200, 1800)),
                         [(0, 0), (1900, 0), (0, 1000), (1900, 1000)])

    def test_six_windows_use_three_columns_and_center_overlap(self) -> None:
        self.assertEqual(grid_positions([(1300, 800)] * 6, (0, 0, 3200, 1800)),
                         [(0, 0), (950, 0), (1900, 0), (0, 1000), (950, 1000), (1900, 1000)])

    def test_one_window_centers_and_oversized_windows_stay_at_work_origin(self) -> None:
        self.assertEqual(grid_positions([(1000, 600)], (100, 50, 2100, 1250)), [(600, 350)])
        self.assertEqual(grid_positions([(1300, 800)], (100, 50, 900, 650)), [(100, 50)])
        self.assertEqual(grid_positions([], (0, 0, 800, 600)), [])

    def test_unnamed_window_becomes_a_and_existing_names_are_preserved(self) -> None:
        windows = [GameWindow(1, 1, 'born', 'ENB', 1000, 600),
                   GameWindow(2, 2, 'born', 'ENB_macboyje', 1000, 600)]
        self.assertEqual(window_names(windows), ['ENB_a', 'ENB_macboyje'])
        self.assertEqual(window_names([GameWindow(3, 3, 'born', 'ENB_a', 1000, 600), *windows]),
                         ['ENB_a', 'ENB_b', 'ENB_macboyje'])

    def test_preview_does_not_restore_rename_or_move(self) -> None:
        window = GameWindow(1, 123, 'born', 'ENB', 1000, 600)
        focus = Mock()
        with patch('relayout.SharedFocus', return_value=focus), \
                patch('relayout.game_windows', return_value=[window]), \
                patch('win32api.GetMonitorInfo', return_value={'Work': (0, 0, 2000, 1200)}), \
                patch('relayout.win32gui.IsWindow', return_value=True), \
                patch('relayout.process_identity', return_value='born'), \
                patch('relayout.win32process.GetWindowThreadProcessId', return_value=(1, 123)), \
                patch('relayout.win32gui.GetWindowText', return_value='ENB'), \
                patch('relayout.win32gui.ShowWindow') as restore, \
                patch('relayout.win32gui.SetWindowText') as rename, \
                patch('relayout.win32gui.SetWindowPos') as move:
            self.assertIn('Preview ENB_a', relayout(list_only=True)[0])
        restore.assert_not_called()
        rename.assert_not_called()
        move.assert_not_called()
        focus.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
