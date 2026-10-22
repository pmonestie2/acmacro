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
    def test_mission_count_accepts_one_through_nine(self):
        for count in range(1, 10):
            with self.subTest(count=count):
                self.path.write_text(CONFIG.replace('mission_count = 4', f'mission_count = {count}'))
                self.assertEqual(load_player_config(self.path).mission_count, count)
        for count in (0, 10):
            with self.subTest(count=count):
                self.path.write_text(CONFIG.replace('mission_count = 4', f'mission_count = {count}'))
                with self.assertRaisesRegex(ValueError, 'mission_count must be between 1 and 9'):
                    load_player_config(self.path)

    def test_job_pickup_and_destination_configuration(self):
        self.assertEqual(load_player_config(self.path).job_pickup, 'packet')
        self.path.write_text(CONFIG + 'job_pickup = ui\njob_destinations = charon, monster_pit\njob_min_level = 75\njob_max_level = 105\n')
        settings = load_player_config(self.path)
        self.assertEqual(settings.job_pickup, 'ui')
        self.assertEqual(settings.job_destinations, 'charon, monster_pit')
        self.assertEqual((settings.job_min_level, settings.job_max_level), (75, 105))
        for extra in ('job_pickup = invalid', 'job_destinations = jupiter',
                      'job_destinations = auto, charon', 'job_min_level = -1',
                      'job_min_level = 105\njob_max_level = 50'):
            self.path.write_text(CONFIG + extra + '\n')
            with self.assertRaises(ValueError):
                load_player_config(self.path)
    def test_gate_navigation_is_optional_and_validated(self):
        self.path.write_text(CONFIG, encoding="utf-8")
        self.assertEqual(load_player_config(self.path).gate_navigation, "packet")
        self.path.write_text(CONFIG + "gate_navigation = click\n", encoding="utf-8")
        self.assertEqual(load_player_config(self.path).gate_navigation, "click")
        self.path.write_text(CONFIG + "gate_navigation = invalid\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "gate_navigation"):
            load_player_config(self.path)

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

    def test_new_player_type_selects_trade_only_route(self):
        self.path.write_text(CONFIG.replace("player_type = te", "player_type = NEW"), encoding="utf-8")
        settings = load_player_config(self.path)
        player = Player()
        player.configure(settings)
        self.assertEqual(player.type_, "new")

    def test_player_config_argument_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()), patch("pluto.request_elevation") as elevate:
            with self.assertRaises(SystemExit) as caught:
                pluto.main([])
        self.assertEqual(caught.exception.code, 2)
        elevate.assert_not_called()

    def test_instance_directory_and_coordinates_are_optional_and_resolved(self):
        self.path.write_text(CONFIG + "game_dir = game copy\nwindow_x = -1280\nwindow_y = 40\n", encoding="utf-8")
        settings = load_player_config(self.path)
        self.assertEqual(settings.game_dir, str((self.path.parent / "game copy").resolve()))
        self.assertEqual((settings.window_x, settings.window_y), (-1280, 40))
        self.path.write_text(CONFIG + "window_x = not-a-number\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "screen coordinates"):
            load_player_config(self.path)

    def test_explicit_client_path_is_resolved(self):
        self.path.write_text(CONFIG + "client_exe = game copy/release/client.exe\n", encoding="utf-8")
        settings = load_player_config(self.path)
        self.assertEqual(settings.client_exe, str((self.path.parent / "game copy/release/client.exe").resolve()))
        self.path.write_text(CONFIG + "client_exe = game copy\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "client.exe"):
            load_player_config(self.path)

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

    def test_singleton_survives_elevation(self):
        with patch("pluto.ctypes.windll.shell32.IsUserAnAdmin", return_value=False), \
             patch("pluto.request_elevation", return_value=0) as elevate:
            self.assertEqual(pluto.main([str(self.path), "--singleton"]), 0)
        elevate.assert_called_once_with([str(self.path.resolve()), "--singleton"])

    def test_station_lock_is_disabled_by_default_and_survives_elevation(self):
        self.assertFalse(pluto.PlutoRunner().station_lock)
        for flag in ('--station-lock', '--earth-station-lock'):
            with self.subTest(flag=flag), \
                 patch('pluto.ctypes.windll.shell32.IsUserAnAdmin', return_value=False), \
                 patch('pluto.request_elevation', return_value=0) as elevate:
                self.assertEqual(pluto.main([str(self.path), flag]), 0)
            elevate.assert_called_once_with([str(self.path.resolve()), '--station-lock'])

    def test_runner_uses_supplied_player_without_hardcoded_overrides(self):
        settings = load_player_config(self.path)
        runner = pluto.PlutoRunner(settings)
        with patch("pluto.require_admin"), patch("pluto.reset_chat") as reset, \
             patch("pluto.stop_chat"), patch.object(runner, "_run_loop"):
            runner.run()
        reset.assert_not_called()  # The mocked loop selects the client before resetting chat.
        self.assertEqual(pluto.PLAYER.toon_name, "TestToon")
        self.assertEqual(pluto.PLAYER.type_, "te")
        self.assertEqual(pluto.PLAYER.toon_position, 3)
        self.assertEqual(pluto.PLAYER.mission_count, 4)

    def test_singleton_mode_is_logged_on_startup(self):
        settings = load_player_config(self.path)
        runner = pluto.PlutoRunner(settings, singleton=True)
        with patch("pluto.require_admin"), patch("pluto.reset_chat"), \
             patch("pluto.stop_chat"), patch.object(runner, "_run_loop"), \
             patch("pluto.log_event") as event:
            runner.run()
        self.assertTrue(any("SINGLETON (--singleton)" in c.args[0] for c in event.call_args_list))

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
