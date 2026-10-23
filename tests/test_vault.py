from __future__ import annotations

import struct
import io
from collections.abc import Callable
import unittest
from unittest.mock import Mock, patch

from ac import AcController
from protocol.client_cargo import ClientCargo
from protocol.client_vault import ClientVault
from protocol.vendor_cargo import CargoItem
from protocol.vendor_trading import VendorCapture, VendorSession
from vault import VaultController, main


SESSION = VendorSession(1, 'client', 2, 'proxy', 0x4003c204)
EMPTY = CargoItem(0xffffffff, 0)


class VaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = Mock(spec=AcController)
        self.controller.focus = None
        self.controller.active_time = Mock(return_value=0)
        self.worker = VaultController(self.controller, SESSION)
        self.worker.checkpoint = Mock()
        self.worker.cargo = Mock(spec=ClientCargo)
        self.worker.vault = Mock(spec=ClientVault)
        self.worker.capture = Mock(spec=VendorCapture)
        self.worker.capture.read.return_value = []
        self.worker.capture.recent_server_messages.return_value = ()
        self.hull = {0: CargoItem(775, 700), 1: CargoItem(6549, 1), 2: CargoItem(816, 1)}
        self.secure = {0: CargoItem(775, 1), 1: EMPTY, 2: EMPTY}
        self.worker.cargo.capacity.return_value = 3
        self.worker.cargo.snapshot.side_effect = lambda **kwargs: dict(self.hull)
        self.worker.vault.snapshot.side_effect = lambda *args: dict(self.secure)
        logger = patch('vault.log_event')
        self.logged = logger.start()
        self.addCleanup(logger.stop)
        names = patch('vault.item_names', return_value={775: 'White Dragon Laser M2'})
        names.start()
        self.addCleanup(names.stop)
        definitions = patch('vault.replay_item_definition')
        self.replay_definition = definitions.start()
        self.addCleanup(definitions.stop)

    def submit(self, args: object, report: dict[str, object], *, frame: bytes,
               before_submit: Callable[[], None]) -> None:
        self.worker.before_submit()
        game, source, slot, target_inv, target, quantity = struct.unpack('>6I', frame[4:])
        self.assertEqual((game, source, target_inv), (SESSION.game_id, 1, 3))
        self.assertEqual(self.secure[target], EMPTY)
        self.assertEqual(self.hull[slot].quantity, quantity)
        self.secure[target] = self.hull[slot]
        self.hull[slot] = EMPTY
        report['state'] = 'handler_returned'

    def test_unloads_full_stacks_into_empty_slots_and_preserves_trade_goods(self) -> None:
        with patch('vault.proxy_commands.submit', side_effect=self.submit) as send:
            self.worker.run()
        self.assertEqual((self.worker.moved, self.worker.skipped), (2, 1))
        self.assertEqual(self.secure[0], CargoItem(775, 1))
        self.assertEqual(self.secure[1], CargoItem(775, 700))
        self.assertEqual(self.hull[1], CargoItem(6549, 1))
        self.assertEqual(send.call_count, 2)
        self.worker.cargo.close.assert_called_once()
        self.worker.capture.close.assert_called_once()

    def test_list_prints_vault_ids_without_transfers_or_packet_capture(self) -> None:
        output = io.StringIO()
        with patch('vault.proxy_commands.submit') as send, patch('sys.stdout', output):
            self.worker.run(list_only=True)
        send.assert_not_called()
        self.assertEqual(self.worker.moved, 0)
        self.assertIn('775', output.getvalue())
        self.assertIn('White Dragon Laser M2', output.getvalue())
        self.worker.capture.open.assert_not_called()

    def test_deposit_dry_run_sends_nothing(self) -> None:
        with patch('vault.proxy_commands.submit') as send:
            self.worker.run(dry_run=True)
        send.assert_not_called()

    def test_grep_deposits_only_matching_named_items(self) -> None:
        with patch('vault.proxy_commands.submit', side_effect=self.submit) as send:
            self.worker.run(grep='WHITE dragn')
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.hull[0], EMPTY)
        self.assertEqual(self.hull[2], CargoItem(816, 1))
        self.assertEqual(self.secure[1], CargoItem(775, 700))

    def test_grep_exact_id_can_select_items_with_unknown_names(self) -> None:
        with patch('vault.proxy_commands.submit', side_effect=self.submit) as send:
            self.worker.run(grep='816')
        send.assert_called_once()
        self.assertEqual(self.hull[0], CargoItem(775, 700))
        self.assertEqual(self.hull[2], EMPTY)

    def test_grep_no_match_does_not_fall_back_to_all(self) -> None:
        for query in ('unicorn', '77', 'unknown'):
            with patch('vault.proxy_commands.submit') as send:
                self.worker.run(grep=query)
            send.assert_not_called()
        self.assertIn('no hull items matched', self.logged.call_args.args[0])

    def test_grep_listing_filters_vault_contents_without_transfers(self) -> None:
        self.secure[1] = CargoItem(816, 1)
        output = io.StringIO()
        with patch('vault.proxy_commands.submit') as send, patch('sys.stdout', output):
            self.worker.run(list_only=True, grep='dragon')
        self.assertIn('775', output.getvalue())
        self.assertNotIn('816', output.getvalue())
        send.assert_not_called()

    def test_bare_cli_requires_selection_before_config_or_client_access(self) -> None:
        with patch('vault.load_player_config') as load, patch('sys.stderr', io.StringIO()):
            with self.assertRaises(SystemExit):
                main(['config_player_macboyje.txt'])
        load.assert_not_called()

    def test_blank_filter_fails_before_opening_reader(self) -> None:
        with self.assertRaises(ValueError):
            self.worker.run(grep='   ')
        self.worker.cargo.open.assert_not_called()

    def submit_withdraw(self, args: object, report: dict[str, object], *, frame: bytes,
                        before_submit: Callable[[], None]) -> None:
        before_submit()
        game, source_inv, source, target_inv, target, quantity = struct.unpack('>6I', frame[4:])
        self.assertEqual((game, source_inv, target_inv), (SESSION.game_id, 3, 1))
        previous = self.secure[source]
        self.assertEqual(self.hull[target], EMPTY)
        self.hull[target] = CargoItem(previous.item_id, quantity)
        self.secure[source] = CargoItem(previous.item_id, previous.quantity - quantity) if previous.quantity > quantity else EMPTY
        report['state'] = 'handler_returned'

    def test_withdraws_requested_units_by_id_and_confirms_both_inventories(self) -> None:
        self.hull[0] = self.hull[2] = EMPTY
        self.secure[0] = CargoItem(775, 3)
        with patch('vault.proxy_commands.submit', side_effect=self.submit_withdraw) as send:
            self.worker.run(withdraw_item=775, quantity=2)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.worker.moved, 2)
        self.assertEqual(self.secure[0], CargoItem(775, 1))
        self.assertEqual(self.hull[0], CargoItem(775, 2))
        self.assertEqual(self.hull[1], CargoItem(6549, 1))
        self.assertIsNone(self.worker.pending_withdrawal)
        self.replay_definition.assert_called_once_with(SESSION, 775, before_submit=self.worker.checkpoint)

    def test_definition_failure_stops_before_inventory_transfer(self) -> None:
        self.hull[0] = EMPTY
        self.replay_definition.side_effect = TimeoutError('definition unconfirmed; no retry')
        with patch('vault.proxy_commands.submit') as send:
            with self.assertRaisesRegex(TimeoutError, 'definition unconfirmed'):
                self.worker.run(withdraw_item=775)
        send.assert_not_called()
        self.assertEqual(self.worker.moved, 0)
        self.assertEqual(self.worker.loaded_definitions, set())
        self.assertIsNone(self.worker.pending_withdrawal)
        self.worker.capture.close.assert_called_once()

    def test_definition_replay_precedes_deposit(self) -> None:
        events: list[str] = []
        self.replay_definition.side_effect = lambda *args, **kwargs: events.append('definition')
        def transfer(*args: object, **kwargs: object) -> None:
            events.append('transfer')
            self.submit(*args, **kwargs)
        with patch('vault.proxy_commands.submit', side_effect=transfer):
            self.worker.deposit(0, 1, self.hull[0])
        self.assertEqual(events, ['definition', 'transfer'])

    def test_withdrawal_preview_sends_nothing(self) -> None:
        self.hull[0] = EMPTY
        with patch('vault.proxy_commands.submit') as send:
            self.worker.run(withdraw_item=775, dry_run=True)
        send.assert_not_called()
        self.replay_definition.assert_not_called()

    def test_whole_stack_uses_one_packet_and_one_empty_hull_slot(self) -> None:
        self.hull[0] = EMPTY
        self.secure[0] = CargoItem(775, 700)
        self.secure[2] = CargoItem(775, 3)
        with patch('vault.proxy_commands.submit', side_effect=self.submit_withdraw) as send:
            self.worker.run(withdraw_item=775, whole_stack=True)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.hull[0], CargoItem(775, 700))
        self.assertEqual(self.secure[0], EMPTY)
        self.assertEqual(self.secure[2], CargoItem(775, 3))
        self.assertEqual(self.worker.moved, 700)

    def test_requested_quantity_spans_source_stacks_without_splitting_units(self) -> None:
        self.hull[0] = self.hull[2] = EMPTY
        self.secure[0], self.secure[2] = CargoItem(775, 3), CargoItem(775, 4)
        with patch('vault.proxy_commands.submit', side_effect=self.submit_withdraw) as send:
            self.worker.run(withdraw_item=775, quantity=5)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.hull[0], CargoItem(775, 3))
        self.assertEqual(self.hull[2], CargoItem(775, 2))
        self.assertEqual(self.secure[2], CargoItem(775, 2))
        self.assertEqual(self.worker.moved, 5)

    def test_insufficient_slots_for_source_stacks_prevents_all_transfers(self) -> None:
        self.hull[0] = EMPTY
        self.secure[0], self.secure[2] = CargoItem(775, 3), CargoItem(775, 4)
        with patch('vault.proxy_commands.submit') as send:
            with self.assertRaisesRegex(ValueError, 'Need 2 empty cargo slots'):
                self.worker.run(withdraw_item=775, quantity=5)
        send.assert_not_called()
        self.replay_definition.assert_not_called()

    def test_stack_preview_does_not_replay_or_move_cargo(self) -> None:
        self.hull[0] = EMPTY
        self.secure[0] = CargoItem(775, 700)
        with patch('vault.proxy_commands.submit') as send:
            self.worker.run(withdraw_item=775, whole_stack=True, dry_run=True)
        send.assert_not_called()
        self.replay_definition.assert_not_called()
        self.assertEqual(self.secure[0], CargoItem(775, 700))

    def test_overlarge_quantity_is_rejected_before_replay_or_submission(self) -> None:
        with patch('vault.proxy_commands.submit') as send:
            with self.assertRaisesRegex(ValueError, 'source stack'):
                self.worker.withdraw_stack(0, 0, CargoItem(775, 3), 4)
        send.assert_not_called()
        self.replay_definition.assert_not_called()

    def test_missing_item_or_cargo_space_stops_before_transfers(self) -> None:
        with patch('vault.proxy_commands.submit') as send:
            with self.assertRaisesRegex(ValueError, 'Vault has'):
                self.worker.withdraw(9999, 1)
            with self.assertRaisesRegex(ValueError, 'empty cargo slots'):
                self.worker.withdraw(775, 1)
        send.assert_not_called()

    def test_withdrawal_changed_destination_blocks_submission(self) -> None:
        with patch('vault.proxy_commands.submit') as send:
            with self.assertRaisesRegex(RuntimeError, 'no longer empty'):
                self.worker.withdraw_stack(0, 0, self.secure[0], 1)
        send.assert_not_called()

    def test_withdrawal_timeout_never_retries(self) -> None:
        self.hull[0] = EMPTY
        self.controller.active_time.side_effect = [0, 11]
        with patch('vault.proxy_commands.submit') as send:
            send.side_effect = lambda args, report, **kwargs: report.update(state='handler_returned')
            with self.assertRaisesRegex(TimeoutError, 'no retry'):
                self.worker.run(withdraw_item=775)
        send.assert_called_once()
        self.worker.capture.close.assert_called_once()
        self.worker.cargo.close.assert_called_once()

    def test_full_vault_leaves_hull_untouched(self) -> None:
        self.secure = {0: CargoItem(775, 1)}
        with patch('vault.proxy_commands.submit') as send:
            self.worker.run()
        send.assert_not_called()
        self.assertEqual(self.hull[0], CargoItem(775, 700))
        self.assertIn('vault full', self.logged.call_args.args[0])

    def test_changed_destination_blocks_native_submission(self) -> None:
        self.secure[1] = CargoItem(816, 1)
        with patch('vault.proxy_commands.submit') as send, self.assertRaisesRegex(RuntimeError, 'no longer empty'):
            self.worker.deposit(0, 1, self.hull[0])
        send.assert_not_called()
        self.assertIsNone(self.worker.pending)

    def test_changed_source_blocks_native_submission(self) -> None:
        with patch('vault.proxy_commands.submit') as send, self.assertRaisesRegex(RuntimeError, 'source changed'):
            self.worker.deposit(0, 1, CargoItem(775, 1))
        send.assert_not_called()

    def test_timeout_stops_without_retry_or_next_item(self) -> None:
        self.controller.active_time.side_effect = [0, 11]
        with patch('vault.proxy_commands.submit') as send:
            send.side_effect = lambda args, report, **kwargs: report.update(state='handler_returned')
            with self.assertRaisesRegex(TimeoutError, 'no retry'):
                self.worker.run()
        self.assertEqual(send.call_count, 1)
        self.worker.cargo.close.assert_called_once()
        self.worker.capture.close.assert_called_once()

    def test_explicit_trade_rejection_is_skipped_without_retry(self) -> None:
        self.worker.capture.recent_server_messages.return_value = ('Cannot move trade item into vault.',)
        with patch('vault.proxy_commands.submit') as send:
            send.side_effect = lambda args, report, **kwargs: report.update(state='handler_returned')
            self.assertFalse(self.worker.deposit(0, 1, self.hull[0]))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.worker.moved, 0)

    def test_stop_prevents_submission(self) -> None:
        self.worker.checkpoint.side_effect = InterruptedError('stop')
        with patch('vault.proxy_commands.submit') as send, self.assertRaises(InterruptedError):
            self.worker.deposit(0, 1, self.hull[0])
        send.assert_not_called()

    def test_packets_are_checked_again_after_pause_returns(self) -> None:
        self.worker.capture_active = True
        self.worker.capture.poll.side_effect = [None, InterruptedError('private message during pause')]
        with patch.object(VendorSession, 'check'), self.assertRaises(InterruptedError):
            VaultController.checkpoint(self.worker)
        self.controller._focus_wait.assert_called_once()
        self.assertEqual(self.worker.capture.poll.call_count, 2)
