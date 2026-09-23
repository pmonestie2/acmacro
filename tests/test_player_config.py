import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player import Player, load_player_config
import pluto


CONFIG = """[player]
name = TestToon
player_type = te
character_slot = 3
username = TestAccount
password = test%#;value
mission_count = 4
low_job = true
avoid_pit = true
"""


class PlayerConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "player with spaces.txt"
        self.path.write_text(CONFIG, encoding="utf-8")

    def test_loads_all_settings_and_keeps_password_literal_and_out_of_repr(self):
        settings = load_player_config(self.path)
        player = Player()
        player.configure(settings)
        self.assertEqual(player.toon_name, "TestToon")
        self.assertEqual(player.type_, "te")
        self.assertEqual(player.toon_position, 3)
        self.assertEqual(player.mission_count, 4)
        self.assertEqual(player.password, "test%#;value")
        self.assertEqual(player.username, "TestAccount")
        self.assertNotIn(player.password, repr(settings))
        runner = pluto.PlutoRunner(settings)
        self.assertTrue(runner.low_job)
        self.assertTrue(runner.avoid_pit)
        override = pluto.PlutoRunner(settings, low_job=False, avoid_pit=False)
        self.assertFalse(override.low_job)
        self.assertFalse(override.avoid_pit)

    def test_rejects_bad_config_before_game_input_without_echoing_password(self):
        invalid = [
            CONFIG.replace("character_slot = 3", "character_slot = 5"),
            CONFIG.replace("mission_count = 4", "mission_count = 0"),
            CONFIG.replace("low_job = true", "low_job = maybe"),
            CONFIG.replace("name = TestToon", "name = /quit"),
            CONFIG.replace("player_type = te", "player_type = invalid"),
            CONFIG.replace("username = TestAccount", "username = "),
            CONFIG.replace("username = TestAccount", "username = bad name"),
            CONFIG.replace("mission_count = 4\n", ""),
            CONFIG + "unknown = value\n",
            CONFIG + "password = another\n",
            "password = test%#;value\n" + CONFIG,
        ]
        for contents in invalid:
            with self.subTest(contents=contents.replace("test%#;value", "REDACTED")):
                self.path.write_text(contents, encoding="utf-8")
                with self.assertRaises(ValueError) as caught:
                    load_player_config(self.path)
                self.assertNotIn("test%#;value", str(caught.exception))

    def test_player_config_argument_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()), patch("pluto.request_elevation") as elevate:
            with self.assertRaises(SystemExit) as caught:
                pluto.main([])
        self.assertEqual(caught.exception.code, 2)
        elevate.assert_not_called()

    def test_bad_file_is_rejected_before_elevation(self):
        self.path.write_text("[player]\nname = TestToon\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), patch("pluto.request_elevation") as elevate:
            with self.assertRaises(SystemExit):
                pluto.main([str(self.path)])
        elevate.assert_not_called()

    def test_selected_file_and_overrides_survive_elevation(self):
        with patch("pluto.ctypes.windll.shell32.IsUserAnAdmin", return_value=False), \
             patch("pluto.request_elevation", return_value=0) as elevate:
            self.assertEqual(pluto.main([str(self.path), "--low-job", "--avoid-pit"]), 0)
        elevate.assert_called_once_with([str(self.path.resolve()), "--low-job", "--avoid-pit"])

    def test_runner_uses_supplied_player_without_hardcoded_overrides(self):
        settings = load_player_config(self.path)
        runner = pluto.PlutoRunner(settings)
        with patch("pluto.require_admin"), patch("pluto.reset_chat") as reset, \
             patch("pluto.stop_chat"), patch.object(runner, "_run_loop"):
            runner.run()
        reset.assert_called_once_with("TestToon")
        self.assertEqual(pluto.PLAYER.toon_name, "TestToon")
        self.assertEqual(pluto.PLAYER.type_, "te")
        self.assertEqual(pluto.PLAYER.toon_position, 3)
        self.assertEqual(pluto.PLAYER.mission_count, 4)

    def test_stop_is_reported_as_normal_completion_by_entry_point(self):
        from watchdog import StopMacro
        runner = Mock()
        runner.run.side_effect = StopMacro("Chat action STOP")
        with patch("pluto.ctypes.windll.shell32.IsUserAnAdmin", return_value=True), \
             patch("pluto.PlutoRunner", return_value=runner), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(pluto.main([str(self.path)]), 0)
        self.assertIn("Chat action STOP", output.getvalue())


if __name__ == "__main__":
    unittest.main()
