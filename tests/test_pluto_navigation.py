"""Behavioral checks for Pluto's borrowed packet-navigation lifecycle."""
from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import ac
from explore_gates import SectorGraph
from navigate_destination import DestinationGraph
from navigation.stations import Station, StationCatalog
from player import Player
from pluto_navigation import PlutoPacketNavigator
from protocol.vendor_trading import VendorSession
from protocol.world_tracker import WorldTracker
from watchdog import GameWindowLost, StopMacro


class PlutoNavigationTests(unittest.TestCase):
    def controller(self) -> ac.AcController:
        with patch.object(ac.AcController, 'init_window'):
            controller = ac.AcController('test')
        controller.player = Player()
        return controller

    def test_packet_mode_uses_destination_engine_and_never_clicks(self) -> None:
        controller = self.controller()
        controller.packet_route_enabled = True
        controller.gate = Mock()
        with patch('pluto_navigation.PlutoPacketNavigator') as factory:
            controller.travel_one('pluto', 'charonS')
        factory.assert_called_once_with(controller)
        factory.return_value.travel.assert_called_once_with('pluto', 'charonS')
        factory.return_value.close.assert_called_once()
        controller.gate.assert_not_called()
        self.assertIsNone(controller._active_packet_navigator)

    def test_uncertain_packet_stops_without_fallback_and_closes_capture(self) -> None:
        controller = self.controller()
        controller.packet_route_enabled = True
        controller.gate = Mock()
        with patch('pluto_navigation.PlutoPacketNavigator') as factory:
            factory.return_value.travel.side_effect = TimeoutError('arrival unknown')
            with self.assertRaisesRegex(StopMacro, 'no click fallback or retry'):
                controller.travel_one('earth', 'aba')
        factory.return_value.travel.assert_called_once()
        factory.return_value.close.assert_called_once()
        controller.gate.assert_not_called()

    def test_lost_window_keeps_existing_crash_recovery_exception(self) -> None:
        controller = self.controller()
        controller.packet_route_enabled = True
        with patch('pluto_navigation.PlutoPacketNavigator') as factory:
            factory.return_value.travel.side_effect = GameWindowLost('lost')
            with self.assertRaises(GameWindowLost):
                controller.travel_one('earth', 'aba')
        factory.return_value.close.assert_called_once()

    def test_terminate_and_kill_close_navigation_before_client_stop(self) -> None:
        controller = self.controller()
        navigator = Mock()
        controller._active_packet_navigator = navigator
        controller.instance = Mock()
        controller._yield = Mock()
        controller.instance.stop.side_effect = lambda: navigator.close.assert_called_once()
        controller.killenb()
        controller.instance.stop.assert_called_once()
        navigator.reset_mock()
        controller._active_packet_navigator = navigator
        controller.terminate()
        navigator.close.assert_called_once()

    def test_old_predeparture_warp_is_disabled_only_for_destination_mode(self) -> None:
        controller = self.controller()
        controller.packet_route_enabled = True
        controller.shortest_path = Mock()
        controller.prepare_gate = Mock()
        controller.prepare_departure('earth')
        controller.shortest_path.assert_not_called()
        controller.prepare_gate.assert_not_called()

    def test_packet_route_cannot_hold_an_active_ui_focus_operation(self) -> None:
        controller = self.controller()
        controller.packet_route_enabled = True
        controller.focus = Mock(depth=1)
        with patch('pluto_navigation.PlutoPacketNavigator') as factory:
            with self.assertRaisesRegex(StopMacro, 'UI input operation'):
                controller.travel_one('earth', 'aba')
        factory.return_value.travel.assert_not_called()
        factory.return_value.close.assert_called_once()

    def test_background_thread_cannot_construct_a_route(self) -> None:
        errors: list[Exception] = []
        def attempt() -> None:
            try:
                PlutoPacketNavigator(Mock())
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=attempt)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], StopMacro)

    def engine(self, folder: Path) -> PlutoPacketNavigator:
        navigator = PlutoPacketNavigator.__new__(PlutoPacketNavigator)
        navigator.controller = Mock()
        navigator.controller.player = Player()
        navigator.controller.player.set_pos('saturn')
        navigator.session = VendorSession(1, 'client', 2, 'proxy', 0x40000007)
        navigator.graph = SectorGraph(folder / 'graph.json')
        navigator.graph.gate('Saturn', 5, 'Gate to Uranus')
        navigator.graph.confirm('Saturn', 5, 'Uranus', 1.)
        navigator.graph.sectors['Earth'] = {}
        catalog = StationCatalog()
        catalog.record(Station('Earth', 'Earth Station', 100032, 1., 'test'))
        navigator.destination_graph = DestinationGraph(navigator.graph, station_catalog=catalog)
        navigator.capture_path = folder / 'capture.jsonl'
        navigator.capture_path.touch()
        navigator.receiver = Mock()
        navigator.world = WorldTracker()
        navigator.checkpoint = Mock()
        return navigator

    def test_delivery_milestone_is_published_only_after_confirmed_arrival(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            navigator = self.engine(Path(directory))
            def transfer(leg: object) -> None:
                self.assertEqual(navigator.controller.player.current_position, 'saturn')
            navigator.transfer = Mock(side_effect=transfer)
            with patch('pluto_navigation.read_current_sector', return_value='Saturn'):
                navigator.travel('saturn', 'uranus')
            self.assertEqual(navigator.controller.player.current_position, 'uranus')
            self.assertEqual(navigator.transfer.call_count, 1)
            navigator.output.close()

    def test_station_milestone_uses_docking_leg_and_preserves_state_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            navigator = self.engine(Path(directory))
            navigator.controller.player.set_pos('earth')
            navigator.transfer = Mock(side_effect=TimeoutError('no station confirmation'))
            with patch('pluto_navigation.read_current_sector', return_value='Earth'):
                with self.assertRaises(TimeoutError):
                    navigator.travel('earth', 'earthS')
            self.assertEqual(navigator.transfer.call_args.args[0].action, 7)
            self.assertEqual(navigator.transfer.call_args.args[0].target_id, 100032)
            self.assertEqual(navigator.controller.player.current_position, 'earth')
            navigator.output.close()

    def test_live_sector_mismatch_stops_before_opening_capture_or_sending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            navigator = self.engine(Path(directory))
            navigator.transfer = Mock()
            with patch('pluto_navigation.read_current_sector', return_value='Earth'):
                with self.assertRaisesRegex(StopMacro, 'differs from live sector'):
                    navigator.travel('saturn', 'uranus')
            navigator.receiver.open.assert_not_called()
            navigator.transfer.assert_not_called()

    def test_checkpoint_uses_parent_pause_clock_and_stops_before_poll_on_cancel(self) -> None:
        navigator = PlutoPacketNavigator.__new__(PlutoPacketNavigator)
        navigator.controller = Mock()
        navigator.controller.active_time.return_value = 123.
        navigator.controller._yield.side_effect = StopMacro('paused then stopped')
        navigator.owner_thread = threading.get_ident()
        navigator.closed = False
        navigator.stop_requested = threading.Event()
        navigator.session = Mock()
        navigator.poll = Mock()
        with tempfile.TemporaryDirectory() as directory:
            navigator.stop = Path(directory) / 'stop'
            self.assertEqual(navigator.active_time(), 123.)
            with self.assertRaises(StopMacro):
                navigator.checkpoint()
        navigator.poll.assert_not_called()
        navigator.session.check.assert_not_called()

    def test_close_is_idempotent_and_does_not_close_borrowed_focus_or_control(self) -> None:
        navigator = PlutoPacketNavigator.__new__(PlutoPacketNavigator)
        navigator.closed = False
        navigator.stop_requested = threading.Event()
        navigator.controller = Mock()
        navigator.control = navigator.controller._pause_control
        for name in ('receiver', 'cargo', 'stream', 'output', 'graph'):
            setattr(navigator, name, Mock())
        navigator.close()
        navigator.close()
        navigator.receiver.close.assert_called_once()
        navigator.cargo.close.assert_called_once()
        navigator.control.close.assert_not_called()
        navigator.controller.focus.close.assert_not_called()

    def test_private_messages_keep_plutos_existing_chat_decision_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            navigator = self.engine(Path(directory))
            navigator.world.sector = 'Saturn'
            navigator.pending = navigator.selection = None
            row = dict(time=10., proxy=2, born='proxy', outgoing=False, payload_hex='0400a500')
            decoded = {'frames': [{'opcode': '0x00a5', 'chat': {'event': 3, 'channel': 'private'}}]}
            with patch('explore_gates.decode_datagram', return_value=decoded), \
                    patch.object(navigator.world, 'apply'):
                navigator.observe(row)
                navigator.stop_on_private_message = True
                with self.assertRaisesRegex(InterruptedError, 'private message'):
                    navigator.observe(row)


if __name__ == '__main__':
    unittest.main()
