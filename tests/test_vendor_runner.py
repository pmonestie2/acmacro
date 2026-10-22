from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch

from player import PlayerSettings, load_player_config
from tests.test_player_config import CONFIG
import vendor


SETTINGS = PlayerSettings('VendorUnit', 'je', 0, 'unused', 8, False, False, 'unused')


class VendorRunnerTests(unittest.TestCase):
    def test_cmd_wrapper_validates_spaced_config_from_another_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix='vendor config ') as folder:
            path = Path(folder) / 'player with spaces.txt'
            path.write_text(CONFIG)
            result = subprocess.run(['cmd.exe', '/d', '/c', str(Path(vendor.__file__).with_suffix('.cmd')),
                                     str(path), '--check'], cwd=folder, text=True,
                                    capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('TestToon: vendor_route=raw-data', result.stdout)
        self.assertNotIn('test%#;value', result.stdout + result.stderr)

    def test_config_defaults_and_route_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'player.txt'
            path.write_text(CONFIG)
            loaded = load_player_config(path)
            self.assertEqual((loaded.vendor_route, loaded.vendor_quantity, loaded.vendor_cycles), ('raw-data', 22, 0))
            for route in ('raw-data', 'chocolate', 'charon-earth'):
                path.write_text(CONFIG + f'vendor_route = {route}\nvendor_cycles = 50\nvendor_quantity = 1\n')
                loaded = load_player_config(path)
                self.assertEqual((loaded.vendor_route, loaded.vendor_quantity, loaded.vendor_cycles), (route, 1, 50))
            for setting in ('vendor_route = invalid', 'vendor_cycles = -1', 'vendor_cycles = invalid',
                            'vendor_quantity = 0', 'vendor_quantity = 41', 'vendor_quantity = invalid'):
                path.write_text(CONFIG + setting + '\n')
                with self.subTest(setting=setting), self.assertRaises(ValueError):
                    load_player_config(path)

    def test_check_does_not_launch_or_submit(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'player.txt'
            path.write_text(CONFIG)
            with patch.object(vendor.VendorRunner, 'run') as run:
                self.assertEqual(vendor.main([str(path), '--check']), 0)
            run.assert_not_called()

    def test_slot_override_and_config_fallback_reach_runner(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'player.txt'
            path.write_text(CONFIG + 'vendor_quantity = 17\n')
            for slots in (None, 1, 18, 23, 40):
                with self.subTest(slots=slots), patch.object(vendor, 'VendorRunner') as runner:
                    arguments = [str(path)]
                    if slots is not None:
                        arguments.extend(['--slots', str(slots)])
                    self.assertEqual(vendor.main(arguments), 0)
                    self.assertEqual(runner.call_args.args[0].vendor_quantity,
                                     17 if slots is None else slots)
                    runner.return_value.run.assert_called_once_with()

    def test_invalid_slots_do_not_start_runner(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'player.txt'
            path.write_text(CONFIG)
            for slots in (0, -1, 41):
                with self.subTest(slots=slots), patch.object(vendor, 'VendorRunner') as runner, \
                        patch.object(vendor, 'log_event'):
                    self.assertEqual(vendor.main([str(path), '--slots', str(slots)]), 1)
                    runner.assert_not_called()

    def test_missing_client_logs_in_once_and_releases_controller(self) -> None:
        self.run_with_client(missing=True)

    def test_named_client_is_reused_without_relogin(self) -> None:
        self.run_with_client(missing=False)

    def run_with_client(self, *, missing: bool) -> None:
        controller = Mock()
        controller.window = None if missing else 123
        controller.startup_popup = False
        settings = replace(SETTINGS, vendor_cycles=50)
        with patch('vendor.require_admin'), patch('vendor.configure_event_log') as configure_log, patch('vendor.log_event'), \
                patch('vendor.AcController', return_value=controller) as factory, \
                patch('vendor.VendorSession.from_instance', return_value='session'), \
                patch('vendor.VendorTrader') as trader:
            vendor.VendorRunner(settings).run()
        factory.assert_called_once_with(vendor.WINDOW_TITLE, settings=settings, shared_install=True)
        configure_log.assert_called_once_with(Path(vendor.__file__).parent / f'macro_{settings.name}.log',
                                              player=settings.name)
        self.assertEqual(controller.relogin.call_count, int(missing))
        trader.assert_called_once_with(controller, 'session', vendor.ROUTES['raw-data'], 22, 50)
        trader.return_value.run.assert_called_once_with()
        controller.terminate.assert_called_once_with()
        controller.close.assert_called_once_with()

    def test_trader_failure_closes_controller_without_relogin_retry(self) -> None:
        controller = Mock(window=123, startup_popup=False)
        with patch('vendor.require_admin'), patch('vendor.configure_event_log'), patch('vendor.log_event'), \
                patch('vendor.AcController', return_value=controller), \
                patch('vendor.VendorSession.from_instance'), patch('vendor.VendorTrader') as trader:
            trader.return_value.run.side_effect = RuntimeError('uncertain purchase')
            with self.assertRaises(RuntimeError):
                vendor.VendorRunner(SETTINGS).run()
        controller.relogin.assert_not_called()
        controller.close.assert_called_once_with()

    def test_private_message_notifies_peers_then_closes_owned_game_and_macro(self) -> None:
        controller = Mock(window=123, startup_popup=False)
        controller.focus.broadcast_logoff.return_value = [('Macboypw', None)]
        events = Mock()
        events.attach_mock(controller.focus.broadcast_logoff, 'notify_peers')
        events.attach_mock(controller.instance.stop, 'close_owned_game')
        events.attach_mock(controller.close, 'close_controller')
        with patch('vendor.require_admin'), patch('vendor.configure_event_log'), patch('vendor.log_event') as logged, \
                patch('vendor.AcController', return_value=controller), \
                patch('vendor.VendorSession.from_instance'), patch('vendor.VendorTrader') as trader:
            trader.return_value.run.side_effect = vendor.VendorPrivateMessage('Someone')
            with self.assertRaises(vendor.VendorPrivateMessage):
                vendor.VendorRunner(SETTINGS).run()
        self.assertEqual([event[0] for event in events.mock_calls],
                         ['notify_peers', 'close_owned_game', 'close_controller'])
        self.assertIsNone(controller.window)
        self.assertTrue(any('Macboypw: queued' in event.args[0] for event in logged.call_args_list))
        controller.relogin.assert_not_called()

    def test_peer_broadcast_failure_still_closes_owned_game(self) -> None:
        controller = Mock()
        controller.focus.broadcast_logoff.side_effect = OSError('peer unavailable')
        with patch('vendor.log_event') as logged:
            vendor.VendorRunner(SETTINGS).logoff_instances(controller)
        controller.instance.stop.assert_called_once_with()
        self.assertTrue(any('peer unavailable' in event.args[0] for event in logged.call_args_list))

    def test_peer_individual_delivery_failures_are_logged_and_local_game_still_closes(self) -> None:
        controller = Mock()
        controller.focus.broadcast_logoff.return_value = [('Macboypw', 'offline'), ('Macboytt', None)]
        with patch('vendor.log_event') as logged:
            vendor.VendorRunner(SETTINGS).logoff_instances(controller)
        controller.instance.stop.assert_called_once_with()
        messages = [event.args[0] for event in logged.call_args_list]
        self.assertTrue(any('Macboypw: failed: offline' in message for message in messages))
        self.assertTrue(any('Macboytt: queued' in message for message in messages))

    def test_local_close_failure_is_reported_after_peer_notifications_and_controller_cleanup(self) -> None:
        controller = Mock(window=123, startup_popup=False)
        controller.focus.broadcast_logoff.return_value = [('Macboypw', None)]
        controller.instance.stop.side_effect = RuntimeError('owned client could not close')
        with patch('vendor.require_admin'), patch('vendor.configure_event_log'), patch('vendor.log_event'), \
                patch('vendor.AcController', return_value=controller), \
                patch('vendor.VendorSession.from_instance'), patch('vendor.VendorTrader') as trader:
            trader.return_value.run.side_effect = vendor.VendorPrivateMessage('Someone')
            with self.assertRaisesRegex(RuntimeError, 'could not close'):
                vendor.VendorRunner(SETTINGS).run()
        controller.focus.broadcast_logoff.assert_called_once_with()
        controller.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
