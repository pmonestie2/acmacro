import unittest
from unittest.mock import patch

import ac


class ResolutionTests(unittest.TestCase):
    def controller(self):
        with patch.object(ac.AcController, 'init_window'):
            controller = ac.AcController('test')
        controller.window = 123
        return controller

    def test_other_resolution_allows_mouse_input(self):
        controller = self.controller()
        with patch('ac.win32gui.IsWindow', return_value=True), \
             patch('ac.client_geometry', return_value=((1280, 1024), (1280, 1024), (6, 52))), \
             patch('ac.pag.moveTo') as move, patch('ac.pag.click') as click, \
             patch.object(controller, '_yield'):
            controller.lclick(300, 190)
        move.assert_called_once_with(306, 242, duration=0)
        click.assert_called_once()

    def test_dpi_scaled_correct_resolution_is_allowed(self):
        controller = self.controller()
        with patch('ac.win32gui.IsWindow', return_value=True), \
             patch('ac.client_geometry', return_value=((1280, 768), (1920, 1152), (6, 52))):
            self.assertEqual(controller.translate(300, 190), (456, 337))

    def test_login_window_can_have_different_resolution(self):
        controller = self.controller()
        with patch('ac.bring_to_front_exact', return_value=True), \
             patch.object(controller, 'init_window'), \
             patch('ac.win32gui.IsWindow', return_value=True), \
             patch('ac.client_geometry', return_value=((1024, 768), (1024, 768), (0, 0))), \
             patch('ac.pag.typewrite') as typing, patch('ac.pag.click') as click, \
             patch('ac.pag.moveTo'), patch('ac.key_press'), patch.object(controller, '_yield'):
            controller.enter_login_credentials()
        self.assertEqual(typing.call_count, 2)
        self.assertEqual(click.call_count, 2)


if __name__ == '__main__':
    unittest.main()
