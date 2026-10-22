import unittest
from unittest.mock import Mock, patch

import ac


class MapStateTests(unittest.TestCase):
    def controller(self, states):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        for name in ("press", "_yield", "moveTo", "lclick", "key_press",
                     "mouseDown", "mouseUp"):
            setattr(controller, name, Mock())
        controller.translate = Mock(side_effect=lambda x, y: (x, y))
        controller.screenshot = Mock(return_value="frame")
        controller.test_obj = Mock(side_effect=states)
        return controller

    def test_missed_toggle_retries_and_verifies_before_zooming(self):
        controller = self.controller([True, True, False])
        def zoom(*args):
            self.assertEqual(controller.screenshot.call_count, 3)
        controller.key_press.side_effect = zoom
        with patch("ac.pag.moveTo"):
            controller.get_map()
        self.assertEqual(controller.lclick.call_count, 2)
        self.assertTrue(all(call.args == (711, 187) for call in controller.lclick.call_args_list))
        controller.key_press.assert_called_once_with("-", 3.0)
        self.assertEqual(controller.moveTo.call_count, 3)

    def test_already_small_map_is_not_toggled_back_to_large(self):
        controller = self.controller([False])
        with patch("ac.pag.moveTo"):
            controller.get_map()
        controller.lclick.assert_not_called()
        controller.key_press.assert_called_once_with("-", 3.0)

    def test_stuck_large_map_stops_before_using_small_map_coordinates(self):
        controller = self.controller([True] * 4)
        with patch("ac.pag.moveTo") as drag:
            with self.assertRaisesRegex(ac.GameUnresponsive, "three toggle attempts"):
                controller.get_map()
        self.assertEqual(controller.lclick.call_count, 3)
        self.assertEqual(controller.screenshot.call_count, 4)
        controller.key_press.assert_not_called()
        controller.mouseDown.assert_not_called()
        drag.assert_not_called()

    def test_verified_small_map_reuses_normalization_without_zoom_or_drag(self):
        controller = self.controller([False, True])
        controller.player.current_position = 'saturn'
        controller._map_ready_key = controller.map_key()
        with patch('ac.pag.moveTo') as drag:
            controller.get_map()
        controller.press.assert_not_called()
        controller.key_press.assert_not_called()
        controller.mouseDown.assert_not_called()
        drag.assert_not_called()
