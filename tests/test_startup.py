import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ac
import pluto
from player import PlayerSettings


class StartupTests(unittest.TestCase):
    def test_launch_passes_installed_client_path(self):
        client = Path(r"C:\Program Files (x86)\EA GAMES\Earth & Beyond\release\client.exe")
        proxy = Path(r"C:\Program Files (x86)\Net-7\bin\net7proxy.exe")
        with patch("ac.GAME_CLIENT", client), patch.object(Path, "is_file", return_value=True), patch("ac.net7_proxy", return_value=proxy), patch("ac.subprocess.Popen") as launch:
            ac.AcController.__new__(ac.AcController).launchEnb()
        launch.assert_called_once_with(
            [str(proxy), f"/ADDRESS:{ac.SERVER_IP}", f"/CLIENT:{client}", "/SSL:443", "/POPT"],
            cwd=proxy.parent,
        )

    def test_missing_game_logs_in_before_chat_and_route(self):
        for window in (None, 123):
            with self.subTest(window=window):
                controller = Mock(window=window)
                controller.bring_to_front.return_value = True
                events = Mock()
                events.attach_mock(controller.relogin, "login")
                events.attach_mock(controller.init_read_chat, "chat")
                runner = pluto.PlutoRunner()
                runner.chat_event = None
                with patch("pluto.AcController", return_value=controller), patch("pluto.run_every"), patch.object(runner, "find_dir"), patch.object(pluto.PLAYER, "set_pos"), patch.object(runner, "route", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        runner._run_loop()
                names = [item[0] for item in events.mock_calls]
                self.assertEqual(names, ["login", "chat"] if window is None else ["chat"])

    def test_cold_login_does_not_type_into_another_window(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.bring_to_front = Mock(return_value=False)
        controller.say = Mock()
        controller._yield = Mock()
        controller.killenb = Mock()
        controller.launchEnb = Mock()
        controller.require_window = Mock()  # Window exists, but cannot be focused.
        with patch("ac.key_press") as key:
            with self.assertRaisesRegex(RuntimeError, "after launch"):
                controller.relogin()
        controller.launchEnb.assert_called_once()
        controller.say.assert_not_called()
        key.assert_not_called()

    def test_restart_keeps_controller_and_chat_timer(self):
        controller = Mock(window=123)
        runner = pluto.PlutoRunner()
        with patch("pluto.AcController", return_value=controller) as factory, patch("pluto.run_every") as timer, patch.object(runner, "find_dir"), patch.object(pluto.PLAYER, "set_pos"), patch.object(runner, "route", side_effect=[pluto.RestartException("relogin"), KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                runner._run_loop()
        factory.assert_called_once()
        timer.assert_called_once()
        self.assertEqual(controller.init_read_chat.call_count, 2)

    def test_abort_stops_chat_and_progress_timers(self):
        runner = pluto.PlutoRunner(PlayerSettings("Test", "je", 0, "test", 6, False, False, "TestAccount"))
        controller, event = Mock(), Mock()
        def abort():
            runner.controller, runner.chat_event = controller, event
            raise RuntimeError("Relogin limit reached")
        with patch("pluto.require_admin"), patch("pluto.reset_chat"), patch("pluto.stop_chat") as stop_chat, \
             patch.object(runner, "_run_loop", side_effect=abort):
            with self.assertRaisesRegex(RuntimeError, "Relogin limit"):
                runner.run()
        event.set.assert_called_once()
        controller.terminate.assert_called_once()
        stop_chat.assert_called_once()
        event.join.assert_called_once()

    def test_login_types_username_then_clicks_password_without_hotkeys(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.player = ac.Player()
        controller.player.username = "regjunk2"
        controller.player.password = "pw"
        controller.bring_to_front = Mock(return_value=True)
        controller.lclick = Mock()
        controller.typewrite = Mock()
        events = Mock()
        events.attach_mock(controller.lclick, "click")
        events.attach_mock(controller.typewrite, "type")
        with patch("ac.pag.hotkey") as hotkey, patch("ac.key_press") as key:
            events.attach_mock(hotkey, "hotkey")
            events.attach_mock(key, "key")
            controller.enter_login_credentials()
        self.assertEqual(events.mock_calls, [
            call.click(300, 190, yield_time=1), call.type("regjunk2"),
            call.click(300, 245, yield_time=1), call.type("pw"), call.key("enter"),
        ])
        hotkey.assert_not_called()

    def test_credentials_are_not_typed_if_game_cannot_be_focused(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.bring_to_front = Mock(return_value=False)
        controller.require_window = Mock()
        controller.lclick = Mock()
        controller.typewrite = Mock()
        with patch("ac.pag.hotkey") as hotkey, patch("ac.key_press") as key:
            with self.assertRaisesRegex(RuntimeError, "login credentials"):
                controller.enter_login_credentials()
        controller.lclick.assert_not_called()
        controller.typewrite.assert_not_called()
        hotkey.assert_not_called()
        key.assert_not_called()


if __name__ == "__main__":
    unittest.main()
