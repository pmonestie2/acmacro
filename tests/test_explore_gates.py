from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from explore_gates import ExplorationCoordinator, GateCancelled, GateExplorer, RouteLeg, SectorGraph, gate_label
from protocol.vendor_trading import VendorSession
from protocol.world_tracker import WorldTracker


class SectorGraphTests(unittest.TestCase):
    def test_markdown_is_a_generated_view_and_cannot_change_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sector_graph.json'
            graph = SectorGraph(path)
            graph.gate('A', 1, 'Gate to B')
            graph.confirm('A', 1, 'B', 1.)
            graph.gate('B', 2, 'Gate to A')
            blocked = graph.gate('B', 3, 'Gate | closed')
            blocked.unavailable_reason = 'Closed\nby server'
            graph.save()
            report = path.with_suffix('.md')
            text = report.read_text(encoding='utf-8')
            self.assertIn('JSON is the source of truth', text)
            self.assertIn('Inferred return', text)
            self.assertIn('Gate \\| closed', text)
            self.assertIn('Closed by server', text)
            before = path.read_bytes()
            report.write_text('User-edited report; no routing authority')
            restored = SectorGraph(path)
            self.assertEqual(restored.sectors['A'][1].destination, 'B')
            restored.write_markdown()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(report.read_text(encoding='utf-8'), text)

    def test_shared_graph_save_merges_independent_writers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sector_graph.json'
            first, second = SectorGraph(path), SectorGraph(path)
            first.gate('A', 1, 'Gate to B')
            first.confirm('A', 1, 'B', 1.)
            second.gate('C', 2, 'Gate to D')
            second.confirm('C', 2, 'D', 2.)
            first.confirm('A', 1, 'B', 3.)
            saved = SectorGraph(path)
            self.assertEqual(saved.sectors['A'][1].confirmed_at, 3.)
            self.assertEqual(saved.sectors['C'][2].destination, 'D')

    def test_matching_server_cancel_skips_gate_and_is_shared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            explorer = GateExplorer.__new__(GateExplorer)
            explorer.session = VendorSession(1, 'client', 2, 'proxy', 0x40000007)
            explorer.world = WorldTracker()
            explorer.world.sector = 'A'
            explorer.graph = SectorGraph(Path(directory) / 'own.json')
            gate = explorer.graph.gate('A', 100, 'Gate to Prison')
            explorer.graph.gate('A', 101, 'Gate to B')
            explorer.pending = ('A', 100)
            explorer.pending_submitted_at = 1.
            explorer.cancelled_transition = None
            explorer.paused_seconds = 0.
            explorer.last_message = 'Current target canceled.'
            explorer.last_message_time = 2.
            def closed(target: int, when: float) -> dict:
                return dict(time=when, proxy=2, born='proxy', outgoing=False,
                    payload_hex=struct.pack('<HHII', 12, 0x31, target, 3).hex())
            explorer.observe(closed(99, 3.))
            self.assertEqual(explorer.pending, ('A', 100))
            explorer.last_message_time = .5
            explorer.observe(closed(100, 3.))
            self.assertEqual(explorer.pending, ('A', 100))
            explorer.last_message_time = 2.
            explorer.observe(closed(100, 3.))
            self.assertIsNone(explorer.pending)
            self.assertIsNotNone(gate.unavailable_reason)
            with self.assertRaises(GateCancelled):
                explorer.wait_for_transition(1.)
            peer = SectorGraph(Path(directory) / 'peer.json', (explorer.graph.path,))
            self.assertEqual(peer.next_gate('A').item_id, 101)

    def test_requested_route_stops_at_destination_and_checks_each_arrival(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            explorer = GateExplorer.__new__(GateExplorer)
            explorer.world = Mock(sector='A')
            explorer.graph = SectorGraph(Path(directory) / 'graph.json')
            gate = explorer.graph.gate('A', 100, 'System Gate to B')
            route = [RouteLeg('A', 'to B', 'B')]
            with patch.object(explorer, 'checkpoint'), \
                    patch.object(explorer, 'traverse', side_effect=lambda _: setattr(explorer.world, 'sector', 'B')) as traverse:
                explorer.follow_route(route)
            traverse.assert_called_once_with(gate)
            explorer.world.sector = 'Wrong'
            with patch.object(explorer, 'checkpoint'), patch.object(explorer, 'traverse') as traverse:
                with self.assertRaisesRegex(RuntimeError, 'Route expected'):
                    explorer.follow_route(route)
            traverse.assert_not_called()

    def test_reverse_gate_is_inferred_without_traversing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = SectorGraph(Path(directory) / 'graph.json')
            graph.gate('A', 1, 'Gate to B')
            graph.confirm('A', 1, 'B', 1.)
            reverse = graph.gate('B', 2, 'Sector Gate to A')
            onward = graph.gate('B', 3, 'Gate to C')
            self.assertEqual(reverse.destination, 'A')
            self.assertTrue(reverse.inferred_reverse)
            self.assertIsNone(reverse.confirmed_at)
            self.assertIs(graph.next_gate('B'), onward)
            graph.confirm('B', 2, 'A', 2.)
            self.assertFalse(reverse.inferred_reverse)

    def test_named_gate_pair_infers_local_reverse_id_and_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'graph.json'
            graph = SectorGraph(path)
            graph.gate("Vishao's Cove", 100164, "Vishao's Gate")
            graph.confirm("Vishao's Cove", 100164, 'Antares Frontier', 1.)
            reverse = graph.gate('Antares Frontier', 100010, "Vishao's Gate")
            self.assertEqual(reverse.destination, "Vishao's Cove")
            self.assertTrue(reverse.inferred_reverse)
            self.assertIsNone(reverse.confirmed_at)
            self.assertEqual(reverse.item_id, 100010)
            graph.save()
            restored = SectorGraph(path)
            self.assertEqual(restored.sectors['Antares Frontier'][100010].destination, "Vishao's Cove")
            graph.confirm('Antares Frontier', 100010, "Vishao's Cove", 2.)
            self.assertFalse(reverse.inferred_reverse)

    def test_named_return_requires_unique_incoming_sector_and_unique_local_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for ambiguous_origin in (False, True):
                with self.subTest(ambiguous_origin=ambiguous_origin):
                    graph = SectorGraph(Path(directory) / f'{ambiguous_origin}.json')
                    graph.gate('A', 1, 'Shared Gate')
                    graph.confirm('A', 1, 'B', 1.)
                    reverse = graph.gate('B', 3, 'Shared Gate')
                    self.assertEqual(reverse.destination, 'A')
                    if ambiguous_origin:
                        graph.gate('C', 4, 'Shared Gate')
                        graph.confirm('C', 4, 'B', 2.)
                    else:
                        graph.gate('B', 2, 'Shared Gate')
                    self.assertIsNone(graph.sectors['B'][3].destination)

    def test_peer_completion_removes_duplicate_frontier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            peer = SectorGraph(Path(directory) / 'peer.json')
            peer.gate('A', 1, 'Gate to B')
            peer.confirm('A', 1, 'B', 1.)
            graph = SectorGraph(Path(directory) / 'own.json', (peer.path,))
            graph.gate('A', 1, 'Gate to B')
            graph.gate('A', 2, 'Gate to C')
            self.assertEqual(graph.next_gate('A').item_id, 2)
            self.assertEqual(graph.sectors['A'][1].destination, 'B')

    def test_parallel_explorers_reserve_different_branches_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
                patch('explore_gates.process_identity', return_value='birth'):
            root = Path(directory)
            graph = SectorGraph(root / 'graph.json')
            graph.gate('Io', 1, 'Gate to Capella')
            graph.gate('Io', 2, 'Gate to Jupiter')
            first = ExplorationCoordinator('JE', root / 'claims')
            second = ExplorationCoordinator('TT', root / 'claims')
            self.assertEqual(first.choose(graph, 'Io').item_id, 1)
            self.assertEqual(second.choose(graph, 'Io').item_id, 2)
            first.release()
            self.assertEqual(second.choose(graph, 'Io').item_id, 1)

    def test_navigation_marker_is_not_a_gate(self) -> None:
        self.assertFalse(gate_label('Nav Capella Gateway'))
        self.assertFalse(gate_label('Nav Gateways'))
        self.assertTrue(gate_label('Sector Gate to Capella'))

    def test_frontier_uses_shortest_confirmed_directed_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = SectorGraph(Path(directory) / 'graph.json')
            graph.gate('A', 1, 'Gate to B')
            graph.gate('B', 2, 'Gate to C')
            graph.gate('C', 3, 'Unexplored gate')
            graph.confirm('A', 1, 'B', 1.)
            graph.confirm('B', 2, 'C', 2.)
            self.assertEqual(graph.next_gate('A').item_id, 1)
            self.assertEqual(graph.next_gate('B').item_id, 2)
            graph.gate('A', 4, 'Local unexplored gate')
            self.assertEqual(graph.next_gate('A').item_id, 4)
            self.assertIsNone(graph.next_gate('Unreachable'))

    def test_ids_are_sector_scoped_and_persist_without_invented_reverse_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'graph.json'
            graph = SectorGraph(path)
            graph.gate('A', 1, 'Gate to B')
            graph.gate('B', 1, 'Gate elsewhere')
            graph.confirm('A', 1, 'B', 1.)
            restored = SectorGraph(path)
            self.assertEqual(restored.sectors['A'][1].destination, 'B')
            self.assertIsNone(restored.sectors['B'][1].destination)
            with self.assertRaisesRegex(RuntimeError, 'destination changed'):
                restored.confirm('A', 1, 'C', 2.)

    def test_only_pinned_outgoing_activation_and_server_arrival_confirm_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            explorer = GateExplorer.__new__(GateExplorer)
            explorer.session = VendorSession(1, 'client', 2, 'proxy', 0x40000007)
            explorer.world = WorldTracker()
            explorer.world.sector = 'A'
            explorer.graph = SectorGraph(Path(directory) / 'graph.json')
            explorer.graph.gate('A', 100, 'Gate to B')
            explorer.pending = None
            body = struct.pack('<6I', 0x40000007, 123, 0x40000007, 18, 100, 0)
            row = dict(time=1., proxy=2, born='wrong', outgoing=True,
                       payload_hex=(struct.pack('<HH', 28, 0x2c) + body).hex())
            explorer.observe(row)
            self.assertIsNone(explorer.pending)
            row['born'] = 'proxy'
            explorer.observe(row)
            self.assertEqual(explorer.pending, ('A', 100))
            self.assertIsNone(explorer.graph.sectors['A'][100].destination)
            with patch.object(explorer.world, 'apply', side_effect=lambda *args: setattr(explorer.world, 'sector', 'B')):
                explorer.observe(dict(row, outgoing=False, payload_hex='04000000', time=2.))
            self.assertEqual(explorer.graph.sectors['A'][100].destination, 'B')
            self.assertIsNone(explorer.pending)

    def test_changed_target_cancels_native_submission(self) -> None:
        explorer = GateExplorer.__new__(GateExplorer)
        explorer.pending = ('A', 100)
        explorer.cancelled_transition = None
        explorer.world = Mock(sector='A')
        explorer.cargo = Mock(inventory=0x20000)
        explorer.cargo.memory.return_value.u32.return_value = 101
        explorer.selection = None
        with patch.object(explorer, 'checkpoint'):
            with self.assertRaisesRegex(RuntimeError, 'Selected gate changed'):
                explorer.command_checkpoint()

    def test_gate_catalog_waits_for_live_objects_even_when_graph_has_old_gates(self) -> None:
        explorer = GateExplorer.__new__(GateExplorer)
        explorer.world = Mock(sector='B', objects={})
        explorer.graph = Mock(sectors={'B': {100: Mock()}})
        def receive_catalog() -> None:
            explorer.world.objects[100] = dict(id='0x64', sector='B', kind='map_object',
                navigation_present=True, nav_type=1, object_type=11, name='Sector Gate to C')
        with patch.object(explorer, 'active_time', side_effect=[0., 0., 0., 1.]), \
                patch('explore_gates.time.sleep'), \
                patch.object(explorer, 'checkpoint', side_effect=receive_catalog) as checkpoint:
            explorer.wait_for_gate_catalog()
        self.assertEqual(checkpoint.call_count, 2)

    def test_gate_catalog_timeout_does_not_claim_frontier_exhausted(self) -> None:
        explorer = GateExplorer.__new__(GateExplorer)
        explorer.world = Mock(sector='B', objects={})
        with patch.object(explorer, 'active_time', side_effect=[0., 0., 31.]), \
                patch.object(explorer, 'checkpoint'):
            with self.assertRaises(TimeoutError):
                explorer.wait_for_gate_catalog()

    def test_explicit_pause_time_is_excluded_and_stop_is_checked(self) -> None:
        explorer = GateExplorer.__new__(GateExplorer)
        explorer.session = Mock()
        explorer.stop = Mock()
        explorer.stop.exists.return_value = False
        explorer.stop_requested = Mock()
        explorer.stop_requested.is_set.return_value = False
        explorer.control = Mock()
        explorer.control.requested.side_effect = [True, True, False]
        explorer.paused_seconds = 3.
        with patch.object(explorer, 'poll'), patch('explore_gates.time.sleep'), \
                patch('explore_gates.time.monotonic', side_effect=[10., 15.]):
            explorer.checkpoint()
        self.assertEqual(explorer.paused_seconds, 8.)
        self.assertEqual(explorer.control.acknowledge.call_args_list,
                         [unittest.mock.call(True), unittest.mock.call(False)])
        explorer.stop_requested.is_set.return_value = True
        with self.assertRaises(InterruptedError):
            explorer.checkpoint()


if __name__ == '__main__':
    unittest.main()
