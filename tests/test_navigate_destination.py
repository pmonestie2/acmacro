from __future__ import annotations

import json
from pathlib import Path
import struct
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

import enb
from explore_gates import GateExplorer, SectorGraph
from navigate_destination import (DestinationGraph, DestinationNavigator, NavigationCapture,
                                  NavigationLeg, ObservedVerbs, automatic_seed, resolve_sector, seed_rows)
from protocol.vendor_trading import VendorSession
from protocol.verb_updates import decode_verb_update
from tests.test_verb_updates import availability


class DestinationGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.graph = SectorGraph(self.root / 'graph.json')

    def connect(self, source: str, target: int, destination: str) -> None:
        self.graph.gate(source, target, f'Gate to {destination}')
        self.graph.confirm(source, target, destination, 1.)

    def test_shortest_route_and_sector_scoped_ids(self) -> None:
        self.connect('A', 1, 'B')
        self.connect('B', 1, 'C')
        self.connect('A', 2, 'C')
        self.connect('C', 1, 'D')
        route = DestinationGraph(self.graph).route('A', 'D')
        self.assertEqual([(leg.source, leg.target_id, leg.destination) for leg in route],
                         [('A', 2, 'C'), ('C', 1, 'D')])

    def test_inferred_reverse_uses_destination_sector_gate_id(self) -> None:
        self.connect('A', 1, 'B')
        self.graph.gate('B', 77, 'Gate to A')
        self.assertEqual(DestinationGraph(self.graph).route('B', 'A'),
                         [NavigationLeg('B', 'A', 77, 'Gate to A')])

    def test_return_route_from_named_gate_uses_antares_gate_id(self) -> None:
        self.graph.gate("Vishao's Cove", 100164, "Vishao's Gate")
        self.graph.confirm("Vishao's Cove", 100164, 'Antares Frontier', 1.)
        self.graph.gate('Antares Frontier', 100010, "Vishao's Gate")
        self.connect("Vishao's Cove", 2, 'Aragoth Prime')
        route = DestinationGraph(self.graph).route('Antares Frontier', 'Aragoth Prime')
        self.assertEqual([leg.target_id for leg in route], [100010, 2])

    def test_never_synthesizes_reverse_id_or_routes_through_unavailable_gates(self) -> None:
        self.connect('A', 1, 'B')
        graph = DestinationGraph(self.graph)
        with self.assertRaisesRegex(ValueError, 'No recorded route'):
            graph.route('B', 'A')
        self.graph.sectors['A'][1].unavailable_reason = 'closed'
        with self.assertRaisesRegex(ValueError, 'No recorded route'):
            graph.route('A', 'B')

    def test_labels_alone_are_not_confirmed_routes(self) -> None:
        self.graph.gate('A', 1, 'Gate to B')
        self.graph.sectors['B'] = {}
        with self.assertRaisesRegex(ValueError, 'No recorded route'):
            DestinationGraph(self.graph).route('A', 'B')

    def test_arrived_route_is_empty_and_unknown_or_ambiguous_name_fails(self) -> None:
        self.connect('Blackbeard\'s Wake', 1, 'Arduinne')
        self.assertEqual(DestinationGraph(self.graph).route('blackbeardswake', 'Blackbeard\'s Wake'), [])
        self.assertEqual(resolve_sector('blackbeard', set(self.graph.sectors)), 'Blackbeard\'s Wake')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            resolve_sector('mars', {'Mars Alpha', 'Mars Beta'})
        with self.assertRaisesRegex(ValueError, 'no recorded match'):
            DestinationGraph(self.graph).route('Arduinne', 'Unknown')

    def test_peer_progress_is_shared_before_planning(self) -> None:
        self.connect('A', 1, 'B')
        peer = SectorGraph(self.root / 'peer.json', (self.graph.path,))
        self.assertEqual(DestinationGraph(peer).route('A', 'B')[0].target_id, 1)

    def test_destination_typo_matching_and_partial_names(self) -> None:
        sectors = {'Nifleheim Cloud', 'Ragnarok', 'Adriel Prime', 'Margesi', 'Uranus',
                   'Dahin Planet', 'Mars', 'Mars Alpha', 'Mars Beta'}
        for query, expected in [('niffle cloud', 'Nifleheim Cloud'), ('ragnrok', 'Ragnarok'),
                                ('adriel', 'Adriel Prime'), ('magnesi', 'Margesi'),
                                ('uranue', 'Uranus'), ('dahing', 'Dahin Planet'), ('mars', 'Mars')]:
            with self.subTest(query=query):
                self.assertEqual(resolve_sector(query, sectors), expected)

    def test_typo_does_not_choose_between_similar_destinations(self) -> None:
        for query in ('mras', 'mars'):
            with self.subTest(query=query), self.assertRaisesRegex(ValueError, 'ambiguous'):
                resolve_sector(query, {'Mars Alpha', 'Mars Beta'})
        with self.assertRaisesRegex(ValueError, 'no recorded match'):
            resolve_sector('xy', {'Xipe Totec', 'Yokan'})

    def test_verified_planet_transfer_and_missing_confirmation(self) -> None:
        self.connect('A', 1, 'Arduinne')
        path = self.root / 'transfers.json'
        record = dict(source='Arduinne', destination='Arduinne Planet', target_id=99,
                      name='Arduinne', action=8, confirmed_at=1.)
        path.write_text(json.dumps([record]))
        route = DestinationGraph(self.graph, path).route('A', 'Arduinne Planet')
        self.assertEqual([leg.action for leg in route], [18, 8])
        del record['confirmed_at']
        path.write_text(json.dumps([record]))
        with self.assertRaisesRegex(ValueError, 'confirmed_at'):
            DestinationGraph(self.graph, path)


class CaptureAndNavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = VendorSession(1, 'client', 2, 'proxy', 0x40000007)

    def test_capture_filters_unrelated_ports_and_tags_exact_session(self) -> None:
        capture = NavigationCapture(self.session)
        capture.addresses = {'10.0.0.1'}
        capture.ports = {1234}
        capture.fragments = Mock()
        capture.fragments.feed.return_value = b'raw'
        packet = dict(source='10.0.0.2:5678', destination='10.0.0.1:1234', payload=b'data')
        with patch('navigate_destination.udp', return_value=packet):
            row = capture.decode(b'packet', 3.)
            self.assertEqual(row, dict(time=3., proxy=2, born='proxy', outgoing=False, payload_hex='64617461'))
            packet['destination'] = '10.0.0.1:9999'
            self.assertIsNone(capture.decode(b'packet', 3.))
            packet.update(source='10.0.0.1:1234', destination='10.0.0.2:5678')
            self.assertTrue(capture.decode(b'packet', 3.)['outgoing'])

    def test_seed_ignores_prior_proxy_lifetime_and_incomplete_tail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'capture.jsonl'
            rows = [dict(time=1., proxy=2, born='old', outgoing=False, payload_hex=''),
                    dict(time=2., proxy=2, born='proxy', outgoing=False, payload_hex='')]
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows) + '{partial')
            self.assertEqual(seed_rows(path, self.session), [rows[1]])

    def test_automatic_seed_requires_fresh_matching_recorder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'explore_macboytt.jsonl'
            path.touch()
            status = dict(proxy=2, born='proxy', state='running', updated=99.)
            path.with_suffix('.status.json').write_text(json.dumps(status))
            self.assertEqual(automatic_seed(root, 'macboytt', self.session, 100.), path)
            self.assertIsNone(automatic_seed(root, 'macboytt', self.session, 110.))
            status['born'] = 'old'
            path.with_suffix('.status.json').write_text(json.dumps(status))
            self.assertIsNone(automatic_seed(root, 'macboytt', self.session, 100.))

    def test_route_verifies_source_and_actual_arrival_without_retry(self) -> None:
        navigator = DestinationNavigator.__new__(DestinationNavigator)
        navigator.world = Mock(sector='A')
        navigator.graph = Mock(sectors={'A': {1: Mock()}})
        navigator.jumps = 0
        leg = NavigationLeg('A', 'B', 1, 'Gate to B')
        with patch.object(navigator, 'checkpoint'), patch.object(navigator, 'traverse') as traverse:
            with self.assertRaisesRegex(RuntimeError, 'Route arrived at A, expected B'):
                navigator.transfer(leg)
            self.assertEqual(traverse.call_count, 1)
            navigator.world.sector = 'Wrong'
            traverse.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'Route expected A'):
                navigator.transfer(leg)
            traverse.assert_not_called()

    def test_uncertain_gate_submission_is_not_retried(self) -> None:
        navigator = DestinationNavigator.__new__(DestinationNavigator)
        navigator.world = Mock(sector='A')
        navigator.graph = Mock(sectors={'A': {1: Mock()}})
        with patch.object(navigator, 'checkpoint'), \
                patch.object(navigator, 'traverse', side_effect=TimeoutError('uncertain')) as traverse:
            with self.assertRaises(TimeoutError):
                navigator.transfer(NavigationLeg('A', 'B', 1, 'Gate to B'))
            self.assertEqual(traverse.call_count, 1)

    def test_landing_preflight_rechecks_selected_target(self) -> None:
        navigator = DestinationNavigator.__new__(DestinationNavigator)
        navigator.world = Mock(sector='Arduinne')
        navigator.landing_guard = ('Arduinne', 99)
        navigator.docking_guard = None
        navigator.cargo = Mock(inventory=0x100000)
        navigator.cargo.memory.return_value.u32.return_value = 98
        navigator.selection = None
        with patch.object(GateExplorer, 'command_checkpoint'):
            with self.assertRaisesRegex(RuntimeError, 'Selected gate changed'):
                navigator.command_checkpoint()

    def test_plan_and_cancellation_close_capture_and_controls_without_retry(self) -> None:
        for plan_only in (True, False):
            with self.subTest(plan_only=plan_only), tempfile.TemporaryDirectory() as directory:
                navigator = DestinationNavigator.__new__(DestinationNavigator)
                navigator.session = self.session
                navigator.name = 'macboytt'
                navigator.capture_path = Path(directory) / 'capture.jsonl'
                navigator.capture_path.touch()
                navigator.graph = SectorGraph(Path(directory) / 'graph.json')
                navigator.graph.gate('A', 1, 'Gate to B')
                navigator.graph.confirm('A', 1, 'B', 1.)
                navigator.destination_graph = DestinationGraph(navigator.graph)
                navigator.world = Mock(sector=None)
                navigator.pending = None
                navigator.receiver = Mock()
                navigator.cargo = Mock()
                navigator.stream = Mock()
                navigator.control = Mock()
                navigator.focus = None
                navigator.stop_requested = Event()
                navigator.output = None
                navigator.jumps = 0
                with patch('navigate_destination.SharedFocus') as focus, \
                        patch.object(navigator, 'poll'), \
                        patch.object(navigator, 'transfer', side_effect=InterruptedError('pause/stop')) as transfer, \
                        patch('navigate_destination.log_event') as log, \
                        patch('builtins.print'):
                    if plan_only:
                        navigator.navigate('B', None, 'A', plan_only=True)
                        transfer.assert_not_called()
                    else:
                        with self.assertRaisesRegex(InterruptedError, 'pause/stop'):
                            navigator.navigate('B', None, 'A')
                        self.assertEqual(transfer.call_count, 1)
                    log.assert_any_call('Navigation plan: A -> B')
                    navigator.receiver.close.assert_called_once()
                    navigator.cargo.close.assert_called_once()
                    navigator.stream.close.assert_called_once()
                    navigator.control.close.assert_called_once()
                    focus.return_value.close.assert_called_once()
                    self.assertTrue(navigator.output.closed)

    def test_dispatch_forwards_destination_and_absolute_capture_graph_paths(self) -> None:
        args = enb.parser().parse_args(['navigate', './mbboytt.conf', 'Arduinne Planet',
            '--from-sector', 'Inverness', '--seed', './capture.jsonl', '--graph', './graph.json', '--plan'])
        command = enb.command_line(args)
        self.assertEqual(Path(command[1]).name, 'navigate_destination.py')
        self.assertEqual(command[2:6], [str(Path('./mbboytt.conf').resolve()), 'Arduinne Planet',
                                      '--from-sector', 'Inverness'])
        self.assertIn(str(Path('./capture.jsonl').resolve()), command)
        self.assertIn(str(Path('./graph.json').resolve()), command)
        self.assertEqual(command[-1], '--plan')

    def test_live_sector_bootstraps_without_recorder_and_overrides_old_seed_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            navigator = DestinationNavigator.__new__(DestinationNavigator)
            navigator.session = self.session
            navigator.name = 'test'
            navigator.capture_path = Path(directory) / 'capture.jsonl'
            navigator.graph = SectorGraph(Path(directory) / 'graph.json')
            navigator.graph.gate('A', 1, 'Gate to B')
            navigator.graph.confirm('A', 1, 'B', 1.)
            navigator.destination_graph = DestinationGraph(navigator.graph)
            navigator.world = Mock(sector='Old sector')
            navigator.pending = None
            navigator.receiver = Mock()
            navigator.cargo = Mock()
            navigator.stream = Mock()
            navigator.control = Mock()
            navigator.focus = None
            navigator.stop_requested = Event()
            navigator.output = None
            navigator.jumps = 0
            with patch('navigate_destination.SharedFocus'), patch.object(navigator, 'poll'), \
                    patch('navigate_destination.read_current_sector', return_value='A') as read, \
                    patch.object(navigator, 'transfer') as transfer, \
                    patch('navigate_destination.log_event') as log:
                navigator.navigate('B', None, None, plan_only=True)
                read.assert_called_once_with(self.session.client_pid, self.session.client_born,
                                             self.session.game_id)
                self.assertEqual(navigator.world.sector, 'A')
                log.assert_any_call('Navigation plan: A -> B')
                transfer.assert_not_called()
                navigator.receiver.open.assert_called_once()
                navigator.receiver.close.assert_called_once()
                read.side_effect = RuntimeError('Client identity changed')
                with self.assertRaisesRegex(RuntimeError, 'Client identity changed'):
                    navigator.navigate('B', None, None)
                transfer.assert_not_called()
                self.assertEqual(navigator.receiver.close.call_count, 2)
                self.assertEqual(navigator.cargo.close.call_count, 2)

    def test_landing_requires_current_group_enabled_for_exact_target_sector_and_fresh_time(self) -> None:
        navigator = DestinationNavigator.__new__(DestinationNavigator)
        navigator.latest_verbs = ObservedVerbs('Zweihander', 9., decode_verb_update(availability(100000, 0)))
        with patch('navigate_destination.time.time', return_value=10.):
            self.assertEqual(navigator.landing_attribute('Zweihander', 100000, 8.), 0)
            self.assertIsNone(navigator.landing_attribute('Other', 100000, 8.))
            self.assertIsNone(navigator.landing_attribute('Zweihander', 100001, 8.))
            self.assertIsNone(navigator.landing_attribute('Zweihander', 100000, 9.5))
        with patch('navigate_destination.time.time', return_value=13.):
            self.assertIsNone(navigator.landing_attribute('Zweihander', 100000, 8.))

    def test_planet_approach_sends_one_warp_and_waits_through_too_far_before_enabled(self) -> None:
        navigator = DestinationNavigator.__new__(DestinationNavigator)
        navigator.session = self.session
        navigator.landing_guard = None
        navigator.landing_ready_after = None
        with patch.object(navigator, 'checkpoint'), patch.object(navigator, 'validate_selected_target'), \
                patch.object(navigator, 'send') as send, \
                patch.object(navigator, 'landing_attribute', side_effect=[2, 0]), \
                patch.object(navigator, 'active_time', return_value=0.), patch('navigate_destination.time.sleep'):
            navigator.approach_planet(NavigationLeg('Zweihander', 'Zweihander Planet', 100000, 'Zweihander', 8))
        self.assertEqual([call.args[0] for call in send.call_args_list], [0x9b, 0x5a])
        self.assertEqual(navigator.landing_guard, ('Zweihander', 100000))

    def test_landing_approach_unavailable_or_timeout_sends_no_landing(self) -> None:
        for attribute in (3, None):
            with self.subTest(attribute=attribute):
                navigator = DestinationNavigator.__new__(DestinationNavigator)
                navigator.session = self.session
                with patch.object(navigator, 'checkpoint'), patch.object(navigator, 'validate_selected_target'), \
                        patch.object(navigator, 'send') as send, \
                        patch.object(navigator, 'landing_attribute', return_value=attribute), \
                        patch.object(navigator, 'active_time', side_effect=[0., 241.]):
                    with self.assertRaises((RuntimeError, TimeoutError)):
                        navigator.approach_planet(NavigationLeg('A', 'A Planet', 100000, 'A', 8))
                    self.assertEqual([call.args[0] for call in send.call_args_list], [0x9b])

    def test_native_landing_preflight_rejects_new_too_far_update(self) -> None:
        navigator = DestinationNavigator.__new__(DestinationNavigator)
        navigator.world = Mock(sector='Zweihander')
        navigator.cargo = Mock(inventory=1)
        navigator.landing_guard = ('Zweihander', 100000)
        navigator.docking_guard = None
        navigator.landing_ready_after = 8.
        navigator.latest_verbs = ObservedVerbs('Zweihander', 9., decode_verb_update(availability(100000, 2)))
        with patch.object(GateExplorer, 'command_checkpoint'), patch.object(navigator, 'validate_selected_target'), \
                patch('navigate_destination.time.time', return_value=10.):
            with self.assertRaisesRegex(RuntimeError, 'no longer enabled'):
                navigator.command_checkpoint()

    def test_verb_observation_filters_proxy_lifetime_and_invalidates_malformed_latest(self) -> None:
        navigator = DestinationNavigator.__new__(DestinationNavigator)
        navigator.session = self.session
        navigator.world = Mock(sector='Zweihander')
        navigator.latest_verbs = None
        body = availability(100000, 0)
        row = dict(time=9., proxy=2, born='old', outgoing=False,
                   payload_hex=(struct.pack('<HH', len(body) + 4, 0x5c) + body).hex())
        with patch.object(GateExplorer, 'observe'):
            navigator.observe(row)
            self.assertIsNone(navigator.latest_verbs)
            row['born'] = 'proxy'
            navigator.observe(row)
            self.assertEqual(navigator.latest_verbs.update.attribute(2), 0)
            row['payload_hex'] = '05005c0000'
            navigator.observe(row)
            self.assertIsNone(navigator.latest_verbs)


if __name__ == '__main__':
    unittest.main()
