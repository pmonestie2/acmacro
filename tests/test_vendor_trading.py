from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from ac import AcController
from player import Player
from protocol.vendor_trading import ROUTES, VendorCapture, VendorPrivateMessage, VendorReply, VendorSession, VendorTrader
from protocol.vendor_cargo import CargoItem, VendorCargo
from protocol.client_cargo import ClientCargo
from window_health import WindowProbeTimeout


SESSION = VendorSession(100, 'client-born', 200, 'proxy-born', 0x4003c204)


class VendorTradingTests(unittest.TestCase):
    def test_parallel_sales_submit_all_distinct_slots_before_waiting(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.cargo.slots = {7: CargoItem(6549, 1), 8: CargoItem(6549, 2),
                                  9: CargoItem(3115, 1)}
        def submit(opcode: int, body: bytes) -> None:
            _, source, slot, destination, _, quantity = struct.unpack('>6I', body)
            self.assertEqual((source, destination, quantity), (1, 4, 1))
            self.trader.before_submit()
            previous = self.trader.cargo.slots[slot].quantity
            self.trader.cargo.slots[slot] = (CargoItem(6549, previous - 1) if previous > 1
                                           else CargoItem(0xffffffff, 0))
        self.trader.send = Mock(side_effect=submit)
        def receive(timeout: float = 0) -> list[VendorReply]:
            self.assertEqual(self.trader.send.call_count, 2)
            return []
        self.capture.read.side_effect = receive
        confirmed: list[int] = []
        count = self.trader.sell_many({7: 1, 8: 2}, on_confirmed=confirmed.append)
        self.assertEqual(count, 2)
        self.assertEqual(confirmed, [7, 8])
        self.assertEqual(self.trader.sale_submissions, 2)
        self.assertEqual(self.trader.cargo.slots[9], CargoItem(3115, 1))
        self.assertIsNone(self.trader.sale_guard)

    def test_parallel_sales_reject_unrelated_item_before_any_submission(self) -> None:
        self.trader.cargo.slots = {7: CargoItem(6549, 1), 8: CargoItem(3115, 1)}
        self.trader.send = Mock()
        with self.assertRaisesRegex(RuntimeError, 'unverified'):
            self.trader.sell_many({7: 1, 8: 1})
        self.trader.send.assert_not_called()

    def test_parallel_partial_acknowledgement_is_reported_without_retry(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.cargo.slots = {7: CargoItem(6549, 1), 8: CargoItem(6549, 1)}
        def submit(opcode: int, body: bytes) -> None:
            slot = struct.unpack('>6I', body)[2]
            if slot == 8:
                self.trader.cargo.slots[8] = CargoItem(0xffffffff, 0)
        self.trader.send = Mock(side_effect=submit)
        self.controller.active_time = Mock(side_effect=[0, 0, 11])
        confirmed: list[int] = []
        with self.assertRaisesRegex(TimeoutError, 'Parallel sale unconfirmed'):
            self.trader.sell_many({7: 1, 8: 1}, on_confirmed=confirmed.append)
        self.assertEqual(confirmed, [8])
        self.assertEqual(self.trader.send.call_count, 2)

    def test_parallel_cancellation_stops_further_submissions(self) -> None:
        self.trader.cargo.slots = {7: CargoItem(6549, 1), 8: CargoItem(6549, 1)}
        self.trader.send = Mock(side_effect=InterruptedError('stop'))
        with self.assertRaises(InterruptedError):
            self.trader.sell_many({7: 1, 8: 1})
        self.trader.send.assert_called_once()
        self.assertIsNone(self.trader.sale_guard)

    def setUp(self) -> None:
        with patch.object(AcController, 'init_window'):
            self.controller = AcController('unit-test')
        self.controller.player = Player()
        self.controller.player.toon_name = 'VendorUnitTest'
        self.controller._focus_wait = Mock()
        health = patch('protocol.vendor_trading.check_window_responsive')
        self.window_health = health.start()
        self.addCleanup(health.stop)
        self.addCleanup(self.controller.close)
        self.trader = VendorTrader(self.controller, SESSION, ROUTES['raw-data'], 22, 2)
        self.capture = Mock(spec=VendorCapture)
        self.trader.capture = self.capture
        self.trader.cargo = VendorCargo(SESSION.game_id)
        self.trader.inventory = Mock(spec=ClientCargo)
        self.trader.inventory.capacity.return_value = 40
        self.trader.inventory.snapshot.side_effect = lambda **kwargs: {
            **{slot: CargoItem(0xffffffff, 0) for slot in range(40)},
            **self.trader.cargo.slots}
        self.capture.read.return_value = []
        self.capture.recent_server_messages.return_value = ()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.trader.stop_path = Path(temporary.name) / 'stop'

    def test_existing_mixed_hold_sells_all_matching_ids_without_buying(self) -> None:
        self.trader.open_trade = Mock()
        self.trader.checkpoint = Mock()
        slots = list(range(10, 32))
        self.trader.cargo.slots = {n: CargoItem(6549, 1) for n in slots}
        self.trader.cargo.slots.update({0: CargoItem(3115, 1), 3: CargoItem(5258, 1), 2: CargoItem(6549, 2)})

        def submit(opcode: int, body: bytes) -> None:
            if opcode != 0x27:
                return
            _, source, slot, _, _, _ = struct.unpack('>6I', body)
            self.assertEqual(source, 1, 'Existing matching cargo requires no purchase')
            previous = self.trader.cargo.slots[slot].quantity
            self.assertIsNotNone(previous)
            self.trader.cargo.require(slot, 6549, previous)
            self.trader.cargo.slots[slot] = CargoItem(6549, previous - 1) if previous > 1 else CargoItem(0xffffffff, 0)

        self.trader.send = Mock(side_effect=submit)
        with patch('protocol.vendor_trading.log_event'):
            self.trader.round()
        self.trader.open_trade.assert_called_once_with(636, b'Progen Combine Fleet')
        transfers = [struct.unpack('>6I', event.args[1]) for event in self.trader.send.call_args_list if event.args[0] == 0x27]
        self.assertEqual(transfers, [(SESSION.game_id, 1, slot, 4, 0, 1) for slot in [*reversed(slots), 2, 2]])
        self.assertEqual(self.trader.cargo.slots[0], CargoItem(3115, 1))
        self.assertEqual(self.trader.cargo.slots[3], CargoItem(5258, 1))
        self.assertEqual((self.trader.completed, self.trader.buy_submissions, self.trader.sale_submissions), (1, 0, 24))

    def test_run_delays_only_between_rounds_and_closes_capture(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.close_trade = Mock()
        self.trader.pause_between_rounds = Mock()
        self.trader.open_trade = Mock()
        self.trader.send = Mock()
        def complete_round() -> None:
            self.trader.completed += 1
        self.trader.round = Mock(side_effect=complete_round)
        with patch('protocol.vendor_trading.log_event') as logged:
            self.trader.run()
        self.assertEqual(self.trader.completed, 2)
        self.trader.pause_between_rounds.assert_called_once_with()
        self.capture.open.assert_called_once_with()
        self.capture.close.assert_called_once_with()
        self.trader.inventory.close.assert_called_once_with()
        self.assertIn('count=2, target=2', logged.call_args.args[0])

    def test_empty_hold_buys_once_then_sells_only_the_configured_item(self) -> None:
        self.trader.open_trade = Mock()
        self.trader.checkpoint = Mock()
        def submit(opcode: int, body: bytes) -> None:
            if opcode != 0x27:
                return
            _, source, slot, _, _, quantity = struct.unpack('>6I', body)
            if source == 4:
                self.trader.cargo.slots = {8: CargoItem(6549, 1), 35: CargoItem(6549, 1), 0: CargoItem(3115, 1)}
            else:
                self.trader.cargo.require(slot, 6549, quantity)
                self.trader.cargo.slots[slot] = CargoItem(0xffffffff, 0)
        self.trader.send = Mock(side_effect=submit)
        with patch('protocol.vendor_trading.log_event'):
            self.trader.round()
        self.assertEqual(self.trader.open_trade.call_args_list,
                         [call(126, b'trade goods?'), call(636, b'Progen Combine Fleet')])
        self.assertEqual((self.trader.buy_submissions, self.trader.sale_submissions), (1, 2))
        self.assertEqual(self.trader.cargo.slots[0], CargoItem(3115, 1))

    def test_unknown_purchase_sends_no_sales_and_is_not_retried(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.open_trade = Mock()
        self.trader.send = Mock()
        self.controller.active_time = Mock(side_effect=[0, 11])
        with self.assertRaisesRegex(TimeoutError, 'no sales sent'):
            self.trader.round()
        transfers = [event for event in self.trader.send.call_args_list if event.args[0] == 0x27]
        self.assertEqual(len(transfers), 1)
        self.assertEqual(struct.unpack('>6I', transfers[0].args[1])[1], 4)

    def test_purchase_is_capped_by_capacity_and_unrelated_cargo(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.open_trade = Mock()
        self.trader.send = Mock()
        self.trader.inventory.capacity.return_value = 18
        self.trader.cargo.slots = {0: CargoItem(3115, 1), 1: CargoItem(None, None),
                                   2: CargoItem(0xfffffffe, 0)}
        self.trader.wait_cargo = Mock(side_effect=TimeoutError('test ends after one buy'))
        with patch('protocol.vendor_trading.log_event'), self.assertRaises(TimeoutError):
            self.trader.round()
        transfers = [struct.unpack('>6I', event.args[1]) for event in self.trader.send.call_args_list
                     if event.args[0] == 0x27]
        self.assertEqual(transfers, [(SESSION.game_id, 4, 3, 1, 0xffffffff, 15)])
        self.trader.wait_cargo.assert_called_once_with(accept_purchase_reply=True, expected_quantity=15)
        self.assertIsNone(self.trader.purchase_guard)

    def test_full_hold_sends_no_purchase(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.open_trade = Mock()
        self.trader.send = Mock()
        self.trader.inventory.capacity.return_value = 18
        self.trader.cargo.slots = {slot: CargoItem(3115, 1) for slot in range(18)}
        with patch('protocol.vendor_trading.log_event'), self.assertRaisesRegex(RuntimeError, 'No verified free'):
            self.trader.round()
        self.trader.send.assert_not_called()

    def test_eighteen_slot_character_repeats_rounds_at_eighteen_items(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.open_trade = Mock()
        self.trader.inventory.capacity.return_value = 18
        purchases: list[int] = []
        def submit(opcode: int, body: bytes) -> None:
            if opcode != 0x27:
                return
            _, source, slot, _, _, quantity = struct.unpack('>6I', body)
            if source == 4:
                purchases.append(quantity)
                self.trader.cargo.slots = {index: CargoItem(6549, 1) for index in range(quantity)}
            else:
                self.trader.cargo.require(slot, 6549, 1)
                self.trader.cargo.slots[slot] = CargoItem(0xffffffff, 0)
        self.trader.send = Mock(side_effect=submit)
        with patch('protocol.vendor_trading.log_event'):
            self.trader.round()
            self.trader.round()
        self.assertEqual(purchases, [18, 18])
        self.assertEqual(self.trader.completed, 2)
        self.assertEqual(self.trader.sale_submissions, 18)

    def test_lost_cargo_space_during_native_preflight_blocks_purchase(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.inventory.capacity.return_value = 18
        self.trader.purchase_guard = 18
        def preflight(args: object, report: dict[str, object], *, frame: bytes,
                      before_submit: Callable[[], None]) -> None:
            self.trader.cargo.slots[0] = CargoItem(3115, 1)
            before_submit()
            self.fail('A purchase exceeding free space must not be submitted')
        with patch('protocol.vendor_trading.proxy_commands.submit', side_effect=preflight):
            with self.assertRaisesRegex(RuntimeError, 'space changed'):
                self.trader.send(0x27, struct.pack('>6I', SESSION.game_id, 4, 3, 1, 0xffffffff, 18))

    def purchase_reply(self) -> VendorReply:
        fixture = json.loads((Path(__file__).parent / 'fixtures' / 'vendor_raw_data_purchase.json').read_text())
        return VendorReply(0x1b, bytes.fromhex(fixture['body_hex']))

    def test_server_purchase_confirmation_allows_opening_seller_with_stale_client(self) -> None:
        self.trader.checkpoint = Mock()
        self.capture.read.return_value = [self.purchase_reply()]
        self.controller.active_time = Mock(return_value=0)
        with patch('protocol.vendor_trading.log_event'):
            self.trader.wait_cargo(accept_purchase_reply=True)
        self.assertEqual(self.trader.cargo.matching(6549), {}, 'Server replies do not overwrite the sale identity checks')

    def test_server_purchase_cannot_authorize_sales_while_client_stays_empty(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.open_trade = Mock()
        self.trader.send = Mock()
        # Drain before buy, fresh purchase acknowledgment, then stale client at seller.
        self.capture.read.side_effect = [[], [self.purchase_reply()], []]
        self.controller.active_time = Mock(side_effect=[0, 0, 11])
        with patch('protocol.vendor_trading.log_event'), self.assertRaisesRegex(TimeoutError, 'no sales sent'):
            self.trader.round()
        self.assertEqual(self.trader.open_trade.call_args_list,
                         [call(126, b'trade goods?'), call(636, b'Progen Combine Fleet')])
        transfers = [event for event in self.trader.send.call_args_list if event.args[0] == 0x27]
        self.assertEqual(len(transfers), 1)
        self.assertEqual(struct.unpack('>6I', transfers[0].args[1])[1], 4)
        self.assertEqual(self.trader.completed, 0)

    def test_server_confirmed_purchase_sells_after_seller_refreshes_client(self) -> None:
        self.trader.checkpoint = Mock()
        def open_trade(npc: int, dialog: bytes) -> None:
            if npc == 636:
                self.trader.cargo.slots = {slot: CargoItem(6549, 1) for slot in range(22)}
                self.trader.cargo.slots[30] = CargoItem(3115, 1)
        self.trader.open_trade = Mock(side_effect=open_trade)
        self.capture.read.side_effect = [[], [self.purchase_reply()]] + [[]] * 22
        def submit(opcode: int, body: bytes) -> None:
            if opcode == 0x27:
                _, source, slot, _, _, _ = struct.unpack('>6I', body)
                if source == 1:
                    self.trader.cargo.require(slot, 6549, 1)
                    self.trader.cargo.slots[slot] = CargoItem(0xffffffff, 0)
        self.trader.send = Mock(side_effect=submit)
        with patch('protocol.vendor_trading.log_event'):
            self.trader.round()
        self.assertEqual((self.trader.completed, self.trader.buy_submissions, self.trader.sale_submissions), (1, 1, 22))
        self.assertEqual(self.trader.cargo.slots[30], CargoItem(3115, 1))

    def test_other_character_purchase_reply_does_not_confirm_our_purchase(self) -> None:
        reply = self.purchase_reply()
        foreign = struct.pack('<I', SESSION.game_id + 1) + reply.body[4:]
        self.capture.read.return_value = [VendorReply(0x1b, foreign)]
        self.trader.checkpoint = Mock()
        self.controller.active_time = Mock(side_effect=[0, 11])
        with patch('protocol.vendor_trading.log_event'), self.assertRaises(TimeoutError):
            self.trader.wait_cargo(accept_purchase_reply=True)

    def test_stale_purchase_reply_is_discarded_before_buy_submission(self) -> None:
        self.capture.read.side_effect = [[self.purchase_reply()], []]
        self.trader.checkpoint = Mock()
        self.trader.open_trade = Mock()
        self.trader.send = Mock()
        self.controller.active_time = Mock(side_effect=[0, 11])
        with patch('protocol.vendor_trading.log_event'), self.assertRaises(TimeoutError):
            self.trader.round()
        self.assertEqual(self.trader.open_trade.call_count, 1)

    def test_slot_changed_to_equipment_blocks_sale(self) -> None:
        self.trader.cargo.slots[7] = CargoItem(3115, 1)
        self.trader.send = Mock()
        with self.assertRaisesRegex(RuntimeError, 'unverified'):
            self.trader.sell_one(7, 1)
        self.trader.send.assert_not_called()

    def test_identity_is_rechecked_after_native_preflight(self) -> None:
        self.trader.cargo.slots[7] = CargoItem(6549, 1)
        self.trader.checkpoint = Mock()
        def preflight(args: object, report: dict[str, object], *, frame: bytes,
                      before_submit: Callable[[], None]) -> None:
            self.trader.cargo.slots[7] = CargoItem(3115, 1)
            before_submit()
            self.fail('Changed slot must not be submitted')
        with patch('protocol.vendor_trading.proxy_commands.submit', side_effect=preflight):
            with self.assertRaisesRegex(RuntimeError, 'unverified'):
                self.trader.sell_one(7, 1)
        self.assertIsNone(self.trader.sale_guard)

    def test_sale_timeout_does_not_retry_or_sell_next_slot(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.cargo.slots[7] = CargoItem(6549, 1)
        self.trader.send = Mock()
        self.controller.active_time = Mock(side_effect=[0, 11])
        with self.assertRaisesRegex(TimeoutError, 'without retry'):
            self.trader.sell_one(7, 1)
        self.trader.send.assert_called_once()

    def test_matching_existing_cargo_needs_no_new_purchase_confirmation(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.cargo.slots[35] = CargoItem(6549, 9)
        with patch('protocol.vendor_trading.log_event'):
            self.trader.wait_cargo()
        self.assertEqual(self.trader.cargo.matching(6549), {35: 9})

    def test_pause_or_stop_during_sale_confirmation_sends_nothing_more(self) -> None:
        self.trader.cargo.slots[7] = CargoItem(6549, 1)
        self.trader.send = Mock()
        self.trader.checkpoint = Mock(side_effect=InterruptedError('stop requested'))
        with self.assertRaises(InterruptedError):
            self.trader.sell_one(7, 1)
        self.trader.send.assert_called_once()
        self.assertIsNone(self.trader.sale_guard)

    def test_random_delay_bounds_and_pause_clock(self) -> None:
        self.trader.checkpoint = Mock()
        self.controller.active_time = Mock(side_effect=[10, 10, 10.2, 10.5])
        with patch('protocol.vendor_trading.random.uniform', return_value=.5) as duration:
            self.trader.pause_between_rounds()
        duration.assert_called_once_with(.3, .8)
        self.assertEqual(self.capture.read.call_count, 2)
        self.assertTrue(all(0 <= event.args[0] <= .05 for event in self.capture.read.call_args_list))

    def test_failure_closes_capture_and_does_not_retry_round(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.close_trade = Mock()
        self.trader.round = Mock(side_effect=RuntimeError('uncertain submission'))
        with patch('protocol.vendor_trading.log_event') as logged:
            with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                self.trader.run()
        self.trader.round.assert_called_once_with()
        self.capture.close.assert_called_once_with()
        self.assertIn('reason=RuntimeError: uncertain submission', logged.call_args.args[0])
        self.trader.inventory.close.assert_called_once_with()

    def test_failed_capture_start_still_closes_capture(self) -> None:
        self.trader.checkpoint = Mock()
        self.capture.open.side_effect = OSError('capture unavailable')
        self.trader.send = Mock()
        with patch('protocol.vendor_trading.log_event'), self.assertRaises(OSError):
            self.trader.run()
        self.trader.send.assert_not_called()
        self.capture.close.assert_called_once_with()

    def test_stop_file_blocks_submission(self) -> None:
        self.trader.stop_path.touch()
        with patch.object(VendorSession, 'check'), patch('protocol.vendor_trading.proxy_commands.submit') as native:
            with self.assertRaises(InterruptedError):
                self.trader.send(0x55, struct.pack('<IB', 246276, 0))
        native.assert_not_called()

    def test_private_message_blocks_next_native_trade_submission(self) -> None:
        self.trader.capture_active = True
        self.capture.poll.side_effect = VendorPrivateMessage('Someone')
        with patch('protocol.vendor_trading.proxy_commands.submit') as native:
            with self.assertRaisesRegex(VendorPrivateMessage, 'Someone'):
                self.trader.send(0x27, bytes(24))
        native.assert_not_called()

    def test_private_message_during_native_preflight_blocks_submission(self) -> None:
        self.trader.capture_active = True
        def preflight(args: object, report: dict[str, object], *, frame: bytes,
                      before_submit: Callable[[], None]) -> None:
            self.capture.poll.side_effect = VendorPrivateMessage('Someone')
            before_submit()
            self.fail('A private message must prevent submission')
        with patch.object(VendorSession, 'check'), \
                patch('protocol.vendor_trading.proxy_commands.submit', side_effect=preflight):
            with self.assertRaises(VendorPrivateMessage):
                self.trader.send(0x27, bytes(24))

    def test_private_message_stop_logs_sender_and_closes_owned_resources(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.close_trade = Mock()
        self.trader.round = Mock(side_effect=VendorPrivateMessage('Someone'))
        with patch('protocol.vendor_trading.log_event') as logged, self.assertRaises(VendorPrivateMessage):
            self.trader.run()
        self.capture.close.assert_called_once_with()
        self.trader.inventory.close.assert_called_once_with()
        self.assertFalse(self.trader.capture_active)
        self.assertIn('Private message from Someone', logged.call_args.args[0])

    def test_unresponsive_owned_window_blocks_packets_and_does_not_rebind(self) -> None:
        self.controller.window = 123
        self.window_health.side_effect = RuntimeError('Owned game window is unresponsive')
        with patch.object(VendorSession, 'check'), patch('protocol.vendor_trading.proxy_commands.submit') as native:
            with self.assertRaisesRegex(RuntimeError, 'unresponsive'):
                self.trader.send(0x55, struct.pack('<IB', 246276, 0))
        self.window_health.assert_called_once_with(123, SESSION.client_pid, SESSION.client_born)
        native.assert_not_called()

    def test_window_dying_during_preflight_blocks_native_submission(self) -> None:
        self.window_health.side_effect = [None, RuntimeError('window disappeared')]
        submitted: list[bool] = []

        def preflight(args: object, report: dict[str, object], *, frame: bytes,
                      before_submit: Callable[[], None]) -> None:
            before_submit()
            submitted.append(True)

        with patch.object(VendorSession, 'check'), patch('protocol.vendor_trading.proxy_commands.submit', side_effect=preflight):
            with self.assertRaisesRegex(RuntimeError, 'disappeared'):
                self.trader.send(0x55, struct.pack('<IB', 246276, 0))
        self.assertEqual(submitted, [])

    def test_short_window_probe_delay_recovers_before_trade_submission(self) -> None:
        self.window_health.side_effect = [WindowProbeTimeout('1460'), None]
        self.trader.check_controls = Mock()
        with patch('protocol.vendor_trading.time.sleep'), patch('protocol.vendor_trading.log_event') as logged:
            self.trader.checkpoint()
        self.assertEqual(self.window_health.call_count, 2)
        self.assertEqual(self.trader.check_controls.call_count, 2)
        self.assertTrue(any('responding again' in event.args[0] for event in logged.call_args_list))

    def test_continuously_unresponsive_window_stops_at_grace_deadline(self) -> None:
        self.window_health.side_effect = WindowProbeTimeout('1460')
        self.trader.check_controls = Mock()
        self.controller.active_time = Mock(side_effect=[0, 5])
        with patch('protocol.vendor_trading.log_event'), \
                patch('protocol.vendor_trading.proxy_commands.submit') as native:
            with self.assertRaisesRegex(RuntimeError, 'unresponsive for 5 seconds'):
                self.trader.send(0x27, bytes(24))
        native.assert_not_called()

    def test_pm_while_waiting_for_delayed_window_stops_without_trade_submission(self) -> None:
        self.window_health.side_effect = WindowProbeTimeout('1460')
        self.trader.check_controls = Mock(side_effect=[None, VendorPrivateMessage('Someone')])
        with patch('protocol.vendor_trading.log_event'), \
                patch('protocol.vendor_trading.proxy_commands.submit') as native:
            with self.assertRaises(VendorPrivateMessage):
                self.trader.send(0x27, bytes(24))
        native.assert_not_called()

    def test_disappeared_window_is_not_retried_as_temporary_delay(self) -> None:
        self.window_health.side_effect = RuntimeError('window disappeared')
        self.trader.check_controls = Mock()
        with patch('protocol.vendor_trading.time.sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'disappeared'):
                self.trader.checkpoint()
        self.window_health.assert_called_once()
        sleep.assert_not_called()

    def test_stop_after_native_preflight_blocks_actual_submission(self) -> None:
        submitted: list[bool] = []

        def preflight(args: object, report: dict[str, object], *, frame: bytes, before_submit: Callable[[], None]) -> None:
            self.trader.stop_path.touch()
            before_submit()
            submitted.append(True)
            report['state'] = 'handler_returned'

        with patch.object(VendorSession, 'check'), patch('protocol.vendor_trading.proxy_commands.submit', side_effect=preflight):
            with self.assertRaises(InterruptedError):
                self.trader.send(0x55, struct.pack('<IB', 246276, 0))
        self.assertEqual(submitted, [])

    def test_native_unknown_result_is_not_retried(self) -> None:
        self.trader.checkpoint = Mock()

        def unknown(args: object, report: dict[str, object], **kwargs: object) -> None:
            report['state'] = 'unknown'

        with patch('protocol.vendor_trading.proxy_commands.submit', side_effect=unknown) as native:
            with self.assertRaisesRegex(RuntimeError, 'without retry'):
                self.trader.send(0x27, bytes(24))
        native.assert_called_once()

    def test_vendor_wait_ignores_wrong_dialogue_and_requires_exact_trade_ack(self) -> None:
        self.trader.checkpoint = Mock()
        self.trader.send = Mock()
        self.capture.read.side_effect = [[], [VendorReply(0x54, b'wrong vendor')],
                                         [VendorReply(0x54, b'How about trade goods?')], [],
                                         [VendorReply(0x56, struct.pack('<i', 3) + b'x')],
                                         [VendorReply(0x56, struct.pack('<i', 3))]]
        self.trader.open_trade(126, b'trade goods?')
        self.assertEqual(self.trader.send.call_args_list,
                         [call(0x4e, struct.pack('<IIB', 246276, 126, 4)),
                          call(0x55, struct.pack('<IB', 246276, 2))])

    def test_vendor_deadline_expires_without_retransmission(self) -> None:
        self.trader.checkpoint = Mock()
        self.controller.active_time = Mock(side_effect=[0, 1, 11])
        self.trader.send = Mock()
        with self.assertRaises(TimeoutError):
            self.trader.open_trade(126, b'trade goods?')
        self.trader.send.assert_called_once()

    def test_vendor_reply_received_during_checkpoint_remains_available_to_waiter(self) -> None:
        for opcode, expected in ((0x54, b'trade goods?'), (0x56, struct.pack('<i', 3))):
            with self.subTest(opcode=opcode):
                capture = VendorCapture(SESSION)
                capture.pending.append(VendorReply(opcode, expected))
                capture.poll = Mock()  # No additional packets; reply arrived during an earlier poll.
                self.trader.capture = capture
                self.trader.capture_active = True
                with patch.object(VendorSession, 'check'):
                    self.trader.wait_reply(opcode, expected, exact=True)
                self.assertEqual(list(capture.pending), [])
                self.assertGreaterEqual(capture.poll.call_count, 3)

    def test_passive_poll_retains_vendor_replies_until_read(self) -> None:
        capture = VendorCapture(SESSION)
        sock = Mock()
        capture.sockets = [sock]
        reply = VendorReply(0x56, struct.pack('<i', 3))
        capture.decode = Mock(return_value=[reply])
        sock.recv.side_effect = [b'packet', BlockingIOError()]
        with patch.object(VendorSession, 'check'), \
                patch('protocol.vendor_trading.udp_ports', return_value={1234}), \
                patch('protocol.vendor_trading.select.select', side_effect=[([sock], [], []), ([], [], []), ([], [], [])]):
            capture.poll()
            capture.poll()
            self.assertEqual(list(capture.pending), [reply])
            self.assertEqual(capture.read(), [reply])
        self.assertEqual(list(capture.pending), [])

    def test_reused_client_or_proxy_identity_is_rejected(self) -> None:
        for identities in (['new-client', 'proxy-born'], ['client-born', 'new-proxy']):
            with self.subTest(identities=identities), patch('protocol.vendor_trading.process_identity', side_effect=identities):
                with self.assertRaisesRegex(RuntimeError, 'exited or changed'):
                    SESSION.check()

    def test_capture_opens_all_sockets_or_cleans_up(self) -> None:
        capture = VendorCapture(SESSION)
        sock = Mock()
        sock.bind.side_effect = OSError('bind failed')
        with patch.object(VendorSession, 'check'), \
                patch('protocol.vendor_trading.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('10.0.0.1', 0))]), \
                patch('protocol.vendor_trading.socket.socket', return_value=sock):
            with self.assertRaises(OSError):
                capture.open()
        sock.close.assert_called_once_with()
        self.assertEqual(capture.sockets, [])

    def test_recorded_dialogues_decode_only_for_owned_port_and_deduplicate(self) -> None:
        recorded = json.loads((Path(__file__).parent / 'fixtures/vendor_trade_opening.json').read_text())
        capture = VendorCapture(SESSION)
        replies: list[VendorReply] = []
        for row in recorded:
            source, sport = row['source'].rsplit(':', 1)
            destination, dport = row['destination'].rsplit(':', 1)
            payload = bytes.fromhex(row['payload_hex'])
            datagram = struct.pack('!HHHH', int(sport), int(dport), len(payload) + 8, 0) + payload
            packet = struct.pack('!BBHHHBBH4s4s', 0x45, 0, len(datagram) + 20, 1, 0, 64, 17, 0,
                                 socket.inet_aton(source), socket.inet_aton(destination)) + datagram
            capture.addresses = {destination}
            capture.ports = {int(dport) + 1}
            self.assertEqual(capture.decode(packet), [])
            capture.ports = {int(dport)}
            replies.extend(capture.decode(packet))
            self.assertEqual(capture.decode(packet), [])
        self.assertTrue(any(reply.opcode == 0x54 and b'trade goods?' in reply.body for reply in replies))
        self.assertTrue(any(reply.opcode == 0x54 and b'Progen Combine Fleet' in reply.body for reply in replies))
        self.assertTrue(any(reply.opcode == 0x56 and reply.body == struct.pack('<i', 3) for reply in replies))

    def test_private_chat_is_filtered_by_proxy_port_and_channel_and_latched(self) -> None:
        capture = VendorCapture(SESSION)
        capture.addresses = {'10.0.0.2'}
        capture.ports = {1234}
        def chat_packet(channel: str, event: int = 3) -> bytes:
            body = struct.pack('<II', event, 0)
            for value in ('Someone [GM]', 'Someone [GM]', '', channel, 'hello'):
                raw = value.encode('ascii')
                body += struct.pack('<H', len(raw)) + raw
            body += bytes(6)
            inner = struct.pack('<HH', len(body) + 4, 0xa5) + body
            payload = struct.pack('<HHII', len(inner) + 12, 0x2016, SESSION.game_id, 1) + inner
            datagram = struct.pack('!HHHH', 3518, 1234, len(payload) + 8, 0) + payload
            return struct.pack('!BBHHHBBH4s4s', 0x45, 0, len(datagram) + 20, 1, 0, 64, 17, 0,
                               socket.inet_aton('10.0.0.1'), socket.inet_aton('10.0.0.2')) + datagram
        self.assertEqual(capture.decode(chat_packet('New Players')), [])
        self.assertEqual(capture.decode(chat_packet('Private', event=1)), [])
        capture.ports = {4321}
        self.assertEqual(capture.decode(chat_packet('PRIVATE')), [])
        capture.ports = {1234}
        with self.assertRaisesRegex(VendorPrivateMessage, 'Someone'):
            capture.decode(chat_packet('PRIVATE'))
        with self.assertRaises(VendorPrivateMessage):
            capture.read()


if __name__ == '__main__':
    unittest.main()
