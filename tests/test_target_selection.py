from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from explore_gates import GateExplorer, SectorGraph, TargetSelection
from protocol.vendor_trading import VendorSession
from protocol.world_tracker import WorldTracker
from tests.test_verb_updates import availability


class TargetSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.explorer = GateExplorer.__new__(GateExplorer)
        self.explorer.session = VendorSession(1, 'client', 2, 'proxy', 0x40000007)
        self.explorer.world = WorldTracker()
        self.explorer.world.sector = 'Paramis'
        self.explorer.graph = SectorGraph(Path(self.directory.name) / 'graph.json')
        self.explorer.pending = None
        self.explorer.selection = TargetSelection('Paramis', 100000, 2., 0xffffffff)
        self.explorer.cargo = Mock(inventory=0x20000)
        self.explorer.cargo.memory.return_value.u32.return_value = 0xffffffff

    def row(self, opcode: int, body: bytes, when: float = 3., *, outgoing: bool = False,
            born: str = 'proxy') -> dict:
        return dict(time=when, proxy=2, born=born, outgoing=outgoing,
                    payload_hex=(struct.pack('<HH', len(body) + 4, opcode) + body).hex())

    def request(self, target: int = 100000) -> None:
        self.explorer.observe(self.row(0x17, struct.pack('<4I', 0x40000007, 123,
                                                      0x40000007, target), outgoing=True))

    def acknowledge(self, target: int = 100000, when: float = 3.1, born: str = 'proxy') -> None:
        self.explorer.observe(self.row(0x19, struct.pack('<II', target, 0xffffffff), when, born=born))

    def test_server_echo_confirms_gate_even_when_client_cache_stays_unselected(self) -> None:
        self.request()
        self.acknowledge()
        self.explorer.validate_selected_target('Paramis', 100000)
        self.assertEqual(self.explorer.selection.acknowledged_at, 3.1)
        self.assertEqual(self.explorer.cargo.memory.return_value.u32.return_value, 0xffffffff)

    def test_stale_ack_wrong_proxy_and_ack_before_request_do_not_confirm(self) -> None:
        self.acknowledge()
        self.assertIsNone(self.explorer.selection.acknowledged_at)
        self.request()
        self.acknowledge(when=1.)
        self.acknowledge(born='prior process')
        self.assertIsNone(self.explorer.selection.acknowledged_at)
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
            self.explorer.validate_selected_target('Paramis', 100000)

    def test_newer_target_request_invalidates_prior_echo_even_if_old_echo_repeats(self) -> None:
        self.request()
        self.acknowledge()
        self.request(100001)
        self.acknowledge()
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            self.explorer.validate_selected_target('Paramis', 100000)

    def test_newer_different_server_target_invalidates_echo(self) -> None:
        self.request()
        self.acknowledge()
        self.acknowledge(100001)
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            self.explorer.validate_selected_target('Paramis', 100000)

    def test_client_target_change_and_sector_change_still_abort(self) -> None:
        self.request()
        self.acknowledge()
        self.explorer.cargo.memory.return_value.u32.return_value = 100001
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            self.explorer.validate_selected_target('Paramis', 100000)
        self.explorer.cargo.memory.return_value.u32.return_value = 0xffffffff
        self.explorer.world.sector = 'Other'
        with self.assertRaisesRegex(RuntimeError, 'Sector changed'):
            self.explorer.validate_selected_target('Paramis', 100000)

    def test_select_target_returns_on_fresh_ack_without_waiting_for_client_memory(self) -> None:
        def send(opcode: int, body: bytes) -> None:
            self.assertEqual(opcode, 0x17)
            self.request()
            self.acknowledge()
        with patch.object(self.explorer, 'checkpoint'), patch.object(self.explorer, 'send', side_effect=send), \
                patch('explore_gates.time.time', return_value=2.):
            self.explorer.paused_seconds = 0.
            self.explorer.select_target('Paramis', 100000, 'Gate to BBW')
        self.assertEqual(self.explorer.selection.acknowledged_at, 3.1)

    def test_missing_echo_times_out_without_activation_or_retry(self) -> None:
        self.explorer.paused_seconds = 0.
        with patch.object(self.explorer, 'checkpoint'), patch.object(self.explorer, 'send') as send, \
                patch.object(self.explorer, 'active_time', side_effect=[0., 6.]):
            with self.assertRaisesRegex(RuntimeError, 'not confirmed by server'):
                self.explorer.select_target('Paramis', 100000, 'Gate to BBW')
            self.assertEqual([call.args[0] for call in send.call_args_list], [0x17, 0x5a])

    def probe(self, target: int = 100000, when: float = 3.2) -> None:
        body = (struct.pack('<II', 0x40000007, 124)
                + struct.pack('>II', 0x40000007, target) + struct.pack('<I', 1))
        self.explorer.observe(self.row(0x5a, body, when, outgoing=True))

    def test_fresh_probed_target_verbs_confirm_selection_when_identical_echo_is_omitted(self) -> None:
        def send(opcode: int, body: bytes) -> None:
            if opcode == 0x17:
                self.request()
            elif opcode == 0x5a:
                self.probe()
                self.explorer.observe(self.row(0x5c, availability(100000, 2), 3.3))
            else:
                self.fail(f'Unexpected mutation {opcode:#x}')
        self.explorer.paused_seconds = 0.
        with patch.object(self.explorer, 'checkpoint'), patch.object(self.explorer, 'send', side_effect=send) as sender, \
                patch('explore_gates.time.time', return_value=2.):
            self.explorer.select_target('Paramis', 100000, 'Gate to BBW')
        self.assertEqual([call.args[0] for call in sender.call_args_list], [0x17, 0x5a])
        self.assertEqual(self.explorer.selection.evidence, 'server selected-target verbs 0x5c')
        self.explorer.validate_selected_target('Paramis', 100000)

    def test_unrequested_stale_malformed_or_other_target_verbs_do_not_confirm(self) -> None:
        self.request()
        self.explorer.observe(self.row(0x5c, availability(100000, 0), 3.3))
        self.assertIsNone(self.explorer.selection.acknowledged_at)
        self.explorer.selection.probe_requested_at = 3.1
        self.probe(100001)
        self.explorer.observe(self.row(0x5c, availability(100000, 0), 3.3))
        self.assertIsNone(self.explorer.selection.acknowledged_at)
        self.probe()
        self.explorer.observe(self.row(0x5c, availability(100000, 0), 3.))
        self.explorer.observe(self.row(0x5c, b'\0', 3.3))
        self.assertIsNone(self.explorer.selection.acknowledged_at)
        self.explorer.observe(self.row(0x5c, availability(100001, 0), 3.3))
        self.assertTrue(self.explorer.selection.changed)
        self.assertIsNone(self.explorer.selection.acknowledged_at)


if __name__ == '__main__':
    unittest.main()
