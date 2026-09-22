import ctypes
import sys
from pathlib import Path
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui
import keyboard_input


class KeyboardTests(unittest.TestCase):
    def capture_event(self, count, pointer, size):
        event = ctypes.cast(pointer, ctypes.POINTER(keyboard_input.Input)).contents
        key = event.payload.keyboard
        self.events.append((event.type, key.vk, key.scan, key.flags))
        self.assertEqual(count, 1)
        self.assertEqual(size, 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        return 1

    def setUp(self):
        self.events = []

    def test_arrow_scan_codes_and_release_flags(self):
        for key, scan in [('left', 0x4B), ('{RIGHT}', 0x4D), ('up', 0x48), ('down', 0x50)]:
            with self.subTest(key=key), patch.object(keyboard_input.user32, 'SendInput', side_effect=self.capture_event), patch('gui.SLEEP_FN') as sleep:
                self.events.clear()
                gui.key_press(key, 0.2)
                self.assertEqual(self.events, [(1, 0, scan, 9), (1, 0, scan, 11)])
                sleep.assert_called_once_with(0.2)

    def test_interruption_still_releases_arrow(self):
        with patch.object(keyboard_input.user32, 'SendInput', side_effect=self.capture_event), patch('gui.SLEEP_FN', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                gui.key_press('left', 1)
            self.assertEqual(self.events, [(1, 0, 0x4B, 9), (1, 0, 0x4B, 11)])

    def test_failed_injection_is_reported(self):
        with patch.object(keyboard_input.user32, 'SendInput', return_value=0), patch('gui.SLEEP_FN') as sleep:
            with self.assertRaises(OSError):
                gui.key_press('left', 1)
            sleep.assert_not_called()

    def test_forward_keeps_pyautogui_method(self):
        with patch('gui.pag.keyDown') as down, patch('gui.pag.keyUp') as up, patch('gui.SLEEP_FN'):
            gui.key_press('a', 2)
            down.assert_called_once_with('a', _pause=False)
            up.assert_called_once_with('a', _pause=False)


if __name__ == '__main__':
    unittest.main()
