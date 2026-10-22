import unittest
from unittest.mock import Mock, patch
import kill_games


class KillGamesTests(unittest.TestCase):
    def test_macro_stops_before_games_so_recovery_cannot_relaunch(self):
        claim = {"pid": 111, "born": "macro", "toon": "Test", "instance": "unused"}
        with patch("kill_games.collect_claims", return_value=[claim]), \
             patch("kill_games.find_macros", return_value=[]), \
             patch("kill_games.configured_paths", return_value=set()), \
             patch("kill_games.find_games", return_value=[(222, "client", "client.exe"), (333, "proxy", "net7proxy.exe")]), \
             patch("kill_games.terminate", return_value=True) as stop:
            kill_games.stop_all()
        self.assertEqual([c.args[0] for c in stop.call_args_list], [111, 222, 333])

    def test_list_never_terminates(self):
        with patch("kill_games.collect_claims", return_value=[]), \
             patch("kill_games.find_macros", return_value=[]), \
             patch("kill_games.configured_paths", return_value=set()), \
             patch("kill_games.find_games", return_value=[(222, "born", "client.exe")]), \
             patch("kill_games.terminate") as stop:
            self.assertIn("Running client.exe", kill_games.stop_all(list_only=True)[0])
        stop.assert_not_called()

    def test_reused_pid_is_not_terminated(self):
        handle = Mock()
        with patch("kill_games.win32api.OpenProcess", return_value=handle), \
             patch("kill_games.win32process.GetProcessTimes", return_value={"CreationTime": "replacement"}), \
             patch("kill_games.win32api.TerminateProcess") as stop:
            self.assertFalse(kill_games.terminate(111, "original"))
        stop.assert_not_called()
        handle.Close.assert_called_once()

    def test_macro_missing_from_registry_is_still_stopped(self):
        missing = {"pid": 444, "born": "macro", "toon": "pluto_console.py"}
        with patch("kill_games.collect_claims", return_value=[]), \
             patch("kill_games.find_macros", return_value=[missing]), \
             patch("kill_games.configured_paths", return_value=set()), \
             patch("kill_games.find_games", return_value=[]), \
             patch("kill_games.terminate", return_value=True) as stop:
            kill_games.stop_all()
        stop.assert_called_once_with(444, "macro")

    def test_process_matching_accepts_macro_and_excludes_unrelated_python(self):
        self.assertEqual(kill_games.macro_script(r'"C:\PYTHON\python.exe" "C:\MACROS\acmacro\vendor.py" config_player_pw.txt'), "vendor.py")
        self.assertEqual(kill_games.macro_script(r'"C:\PYTHON\python.exe" "C:\MACROS\acmacro\pluto.py" config_player_pw.txt'), "pluto.py")
        self.assertEqual(kill_games.macro_script(r'python -u "C:\MACROS\acmacro\pluto_console.py" config_player_je.txt'), "pluto_console.py")
        self.assertIsNone(kill_games.macro_script(r'python "C:\other\pluto.py"'))
        self.assertIsNone(kill_games.macro_script(r'python -c "print(1)" pluto.py'))
        self.assertIsNone(kill_games.macro_script(r'python other.py pluto.py'))
