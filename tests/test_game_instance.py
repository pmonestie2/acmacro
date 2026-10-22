from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from game_instance import GameInstance
from player import PlayerSettings
import chatproc


def settings(name="Test", directory=r"C:\games\second"):
    return PlayerSettings(name, "je", 0, "unused", 1, False, False, "unused", directory, 100, 200)


class InstanceTests(unittest.TestCase):
    def test_three_clients_share_install_but_discover_only_owned_proxy_child(self) -> None:
        for index in range(3):
            instance = GameInstance(settings(name=f'Toon{index}'), shared_install=True)
            self.assertFalse(instance.automatic)
            instance.proxy_pid, instance.proxy_born = 200 + index, 'proxy-born'
            with patch.object(instance, 'valid', return_value=False), \
                    patch('game_instance.process_identity', return_value='proxy-born'), \
                    patch('game_instance.win32gui.EnumWindows', side_effect=lambda callback, arg: [callback(hwnd, arg) for hwnd in (10, 11, 12)]), \
                    patch('game_instance.win32gui.IsWindowVisible', return_value=True), \
                    patch('game_instance.win32gui.GetWindowText', return_value='Earth & Beyond'), \
                    patch('game_instance.win32process.GetWindowThreadProcessId', side_effect=lambda hwnd: (1, 100 + hwnd - 10)), \
                    patch('game_instance.parent_process_id', side_effect=lambda pid: pid + 100), \
                    patch('game_instance.client_path', return_value=instance.client), \
                    patch('game_instance.win32gui.GetClassName', return_value='GameMain'), \
                    patch.object(instance, 'layout'):
                self.assertEqual(instance.discover(), 10 + index)
                self.assertEqual(instance.pid, 100 + index)

    def test_shared_install_does_not_adopt_an_unnamed_foreign_client(self) -> None:
        instance = GameInstance(settings(), shared_install=True)
        with patch.object(instance, 'valid', return_value=False), \
                patch('game_instance.win32gui.EnumWindows', side_effect=lambda callback, arg: callback(10, arg)), \
                patch('game_instance.win32gui.IsWindowVisible', return_value=True), \
                patch('game_instance.win32gui.GetWindowText', return_value='Earth & Beyond'), \
                patch('game_instance.win32process.GetWindowThreadProcessId', return_value=(1, 100)), \
                patch('game_instance.client_path', return_value=instance.client):
            self.assertIsNone(instance.discover())
            self.assertIsNone(instance.pid)

    def test_shared_window_slots_use_different_positions_and_honor_overrides(self) -> None:
        from dataclasses import replace
        config = replace(settings(), window_x=None, window_y=None)
        positions = []
        for slot in range(3):
            instance = GameInstance(config, shared_install=True)
            instance.slot, instance.window = slot, 10
            with patch('game_instance.win32gui.SetWindowText'), \
                    patch('game_instance.win32api.MonitorFromPoint', return_value=1), \
                    patch('game_instance.win32api.GetMonitorInfo', return_value={'Work': (0, 0, 3072, 1768)}), \
                    patch('game_instance.win32gui.GetWindowRect', return_value=(0, 0, 1286, 816)), \
                    patch('game_instance.visible_position', side_effect=lambda h, x, y: (x, y)), \
                    patch('game_instance.win32gui.SetWindowPos') as move:
                instance.layout()
                positions.append(move.call_args.args[2:4])
        self.assertEqual(positions, [(0, 0), (1786, 0), (0, 952)])
        instance = GameInstance(settings(), shared_install=True)
        instance.slot, instance.window = 2, 10
        with patch('game_instance.win32gui.SetWindowText'), \
                patch('game_instance.visible_position', side_effect=lambda h, x, y: (x, y)), \
                patch('game_instance.win32gui.SetWindowPos') as move:
            instance.layout()
        self.assertEqual(move.call_args.args[2:4], (100, 200))

    def test_auto_slot_pairs_copy_chat_log_and_desktop_corner(self):
        from config import GAME_CLIENTS
        config = PlayerSettings("Test", "je", 0, "unused", 1, False, False, "unused")
        instance = GameInstance(config)
        instance.assign_slot(1)
        self.assertEqual(instance.client, GAME_CLIENTS[1])
        self.assertEqual(instance.chat_log, GAME_CLIENTS[1].parent / "chat.log")
        instance.window = 10
        with patch("game_instance.win32gui.SetWindowText"), \
             patch("game_instance.win32api.MonitorFromPoint", return_value=1), \
             patch("game_instance.win32api.GetMonitorInfo", return_value={"Work": (0, 0, 3072, 1768)}), \
             patch("game_instance.win32gui.GetWindowRect", return_value=(0, 0, 1286, 816)), \
             patch("game_instance.visible_position", side_effect=lambda h, x, y: (x, y)), \
             patch("game_instance.win32gui.SetWindowPos") as move:
            instance.layout()
        self.assertEqual(move.call_args.args[2:4], (1786, 952))

    def test_layout_clamps_full_window_inside_monitor_work_area(self):
        instance = GameInstance(settings())
        instance.window, instance.x, instance.y = 10, 1800, 997
        with patch("game_instance.win32gui.SetWindowText"), \
             patch("arrange_windows.win32gui.GetWindowRect", return_value=(0, 0, 1286, 816)), \
             patch("arrange_windows.win32api.MonitorFromRect", return_value=1), \
             patch("arrange_windows.win32api.GetMonitorInfo", return_value={"Work": (0, 0, 3072, 1768)}), \
             patch("game_instance.win32gui.SetWindowPos") as move:
            instance.layout()
        self.assertEqual(move.call_args.args[2:4], (1786, 952))

    def test_explicit_executable_controls_client_and_chat_path(self):
        from dataclasses import replace
        config = replace(settings(), client_exe=r"C:\games\third\release\client.exe")
        instance = GameInstance(config)
        self.assertEqual(instance.client, Path(config.client_exe))
        self.assertEqual(instance.chat_log, Path(r"C:\games\third\release\chat.log"))

    def test_popup_is_renamed_without_moving(self):
        instance = GameInstance(settings())
        instance.window, instance.popup = 10, True
        with patch("game_instance.win32gui.SetWindowText") as rename, \
             patch("game_instance.win32gui.SetWindowPos") as move:
            instance.layout()
        rename.assert_called_once_with(10, "EBPopup")
        move.assert_not_called()

    def test_popup_transition_rebinds_only_same_live_process(self):
        instance = GameInstance(settings())
        instance.window, instance.pid, instance.born = 10, 123, "born"
        instance.popup, instance.proxy_pid = True, 456
        with patch.object(instance, "valid", return_value=False), \
             patch("game_instance.process_identity", return_value="born"), \
             patch("game_instance.win32gui.EnumWindows", side_effect=lambda callback, arg: [callback(hwnd, arg) for hwnd in (20, 30)]), \
             patch("game_instance.win32gui.IsWindowVisible", return_value=True), \
             patch("game_instance.win32gui.GetWindowText", return_value="Earth & Beyond"), \
             patch("game_instance.win32process.GetWindowThreadProcessId", side_effect=lambda hwnd: (1, 123 if hwnd == 20 else 999)), \
             patch("game_instance.client_path", return_value=instance.client), \
             patch("game_instance.win32gui.GetClassName", return_value="GameMain"), \
             patch.object(instance, "layout") as layout:
            self.assertEqual(instance.discover(), 20)
        self.assertFalse(instance.popup)
        self.assertEqual(instance.pid, 123)
        layout.assert_called_once()

    def test_paths_are_isolated_and_lost_client_is_not_rebound(self):
        instance = GameInstance(settings())
        self.assertEqual(instance.chat_log, Path(r"C:\games\second\release\chat.log"))
        self.assertEqual(instance.title, "ENB_Test")
        instance.pid, instance.born, instance.window = 123, "old", 10
        with patch("game_instance.win32gui.IsWindow", return_value=False), \
             patch("game_instance.win32gui.EnumWindows") as enumerate_windows:
            self.assertIsNone(instance.discover())
        enumerate_windows.assert_not_called()

    def test_stop_terminates_only_matching_owned_processes(self):
        instance = GameInstance(settings())
        instance.pid, instance.born = 111, "client-born"
        instance.proxy_pid, instance.proxy_born = 222, "proxy-born"
        handles = [Mock(), Mock()]
        with patch("game_instance.process_identity", side_effect=["client-born", "proxy-born"]), \
             patch("game_instance.win32api.OpenProcess", side_effect=handles), \
             patch("game_instance.win32process.GetProcessTimes", side_effect=[
                 {"CreationTime": "client-born"}, {"CreationTime": "proxy-born"}]), \
             patch("game_instance.win32api.TerminateProcess") as terminate, \
             patch("win32event.WaitForSingleObject"):
            instance.stop()
        self.assertEqual([call.args[0] for call in terminate.call_args_list], handles)
        self.assertIsNone(instance.pid)

    def test_pid_reuse_is_not_terminated(self):
        instance = GameInstance(settings())
        instance.pid, instance.born = 111, "original-born"
        with patch("game_instance.process_identity", return_value="different-born"), \
             patch("game_instance.win32api.OpenProcess") as open_process:
            instance.stop()
        open_process.assert_not_called()

    def test_client_exiting_during_shutdown_still_stops_owned_proxy(self):
        import win32api
        instance = GameInstance(settings())
        instance.pid, instance.born = 111, "client-born"
        instance.proxy_pid, instance.proxy_born = 222, "proxy-born"
        handle = Mock()
        with patch("game_instance.process_identity", side_effect=["client-born", "proxy-born"]), \
             patch("game_instance.win32api.OpenProcess", side_effect=[
                 win32api.error(87, "OpenProcess", "Process exited"), handle]), \
             patch("game_instance.win32process.GetProcessTimes", return_value={"CreationTime": "proxy-born"}), \
             patch("game_instance.win32api.TerminateProcess") as terminate, \
             patch("win32event.WaitForSingleObject"):
            instance.stop()
        terminate.assert_called_once_with(handle, 0)

    def test_chat_reads_configured_path_instead_of_import_time_default(self):
        old_path = chatproc.CHAT_LOG_PATH
        try:
            chatproc.configure_chat_path(r"C:\games\second\release\chat.log")
            with patch("chatproc.require_admin"), patch("chatproc._read_snapshot", return_value="test") as read:
                self.assertEqual(chatproc.read_chat_log(), "test")
            read.assert_called_once_with(Path(r"C:\games\second\release\chat.log"))
        finally:
            chatproc.configure_chat_path(old_path)


if __name__ == "__main__":
    unittest.main()
