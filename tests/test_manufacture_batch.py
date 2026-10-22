from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from manufacture_batch import BuildState, ManufactureBatch, run_window
from protocol.proxy_commands import build_stub, validate_command


class ManufacturePacketTests(unittest.TestCase):
    def test_only_observed_recipe_and_actions_are_allowed(self) -> None:
        for opcode, value in ((0x7b, 3199), (0x7e, 0), (0x7e, 2)):
            frame = struct.pack('<HH', 12, opcode) + struct.pack('>II', 246276, value)
            self.assertEqual(validate_command(frame), (opcode, 246276, ()))
        for opcode, entity, value in ((0x7b, 246276, 3200), (0x7e, 246276, 1),
                                      (0x7e, 0x4003c204, 2), (0x7e, 0, 2)):
            with self.assertRaises(ValueError):
                validate_command(struct.pack('<HH', 12, opcode) + struct.pack('>II', entity, value))

    def test_manufacture_uses_normal_forwarding_handler(self) -> None:
        for opcode in (0x7b, 0x7e):
            stub = build_stub(0x1000000, 0x2000000, 0x3000000, 0x4000000, 8, 246276, opcode)
            self.assertIn(bytes.fromhex('b820f24000ffd0'), stub)
            self.assertNotIn(bytes.fromhex('b820284000ffd0'), stub)


class ManufactureBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trader = Mock()
        self.trader.session.game_id = 0x4003c204
        self.trader.session.proxy_pid = 10
        self.trader.controller.active_time.return_value = 0
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.batch = ManufactureBatch(self.trader, output=Path(self.directory.name) / 'batch.json')

    def test_no_sales_when_build_fails_before_hull_full(self) -> None:
        self.batch.snapshot = Mock(return_value=BuildState(0, 10))
        self.batch.build_one = Mock(side_effect=RuntimeError('missing component'))
        report = self.batch.run()
        self.assertEqual(report['state'], 'stopped')
        self.trader.open_trade.assert_not_called()
        self.trader.sell_one.assert_not_called()

    def test_full_hull_sells_matching_products_manufactured_before_script(self) -> None:
        full = BuildState(2, 0)
        self.batch.snapshot = Mock(side_effect=[full, full, BuildState(0, 2)])
        self.trader.cargo.matching.return_value = {10: 1, 11: 1}
        report = self.batch.run()
        self.assertEqual(report['state'], 'completed')
        self.assertEqual(report['sold'], 2)
        self.assertEqual(report['built'], 0)
        self.trader.cargo.matching.assert_called_once_with(3199)
        self.trader.open_trade.assert_called_once_with(36, b'I stock level II components')
        self.assertEqual([call.args for call in self.trader.sell_one.call_args_list], [(11, 1), (10, 1)])

    def test_full_hull_without_products_does_not_open_seller(self) -> None:
        self.batch.snapshot = Mock(return_value=BuildState(0, 0))
        self.assertEqual(self.batch.run()['state'], 'stopped')
        self.trader.open_trade.assert_not_called()

    def test_sale_parallelism_is_half_manufacturing_with_minimum_one(self) -> None:
        for manufacture, sales in ((1, 1), (2, 1), (3, 1), (4, 2), (8, 4), (9, 4)):
            batch = ManufactureBatch(self.trader, output=self.batch.output, parallel=manufacture)
            self.assertEqual(batch.sale_parallel, sales)

    def test_parallel_sales_group_matching_slots_and_keep_one_pending_per_stack(self) -> None:
        batch = ManufactureBatch(self.trader, output=self.batch.output, parallel=4)
        self.trader.cargo.matching.return_value = {7: 2, 8: 1, 9: 1}
        groups: list[dict[int, int]] = []
        def sell_group(slots: dict[int, int], *, on_confirmed: Callable[[int], None]) -> int:
            groups.append(dict(slots))
            for slot in slots:
                on_confirmed(slot)
            return len(slots)
        self.trader.sell_many.side_effect = sell_group
        batch.sell_products()
        self.assertEqual(groups, [{9: 1, 8: 1}, {7: 2}, {7: 1}])
        self.assertEqual(batch.sold, 4)
        self.trader.sell_one.assert_not_called()

    def test_native_preflight_rejects_changed_product_count(self) -> None:
        self.batch.pending = BuildState(0, 1)
        self.batch.snapshot = Mock(return_value=BuildState(1, 1))
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            self.batch.before_build()

    def test_build_confirmation_requires_only_product_creation(self) -> None:
        before = BuildState(0, 2)
        after = BuildState(1, 1)
        self.batch.snapshot = Mock(side_effect=[before, before, after])
        self.trader.capture.read.side_effect = [[], [Mock(opcode=0x1d, body=b'Callipus M2 requires:')], []]
        with patch('manufacture_batch.proxy_commands.submit') as submit:
            submit.side_effect = lambda args, report, **kwargs: report.update(state='handler_returned')
            self.batch.build_one()
        self.assertEqual(self.batch.built, 1)
        self.assertIsNone(self.batch.pending)
        submit.assert_called_once()

    def test_unknown_submission_stops_without_retry(self) -> None:
        self.batch.snapshot = Mock(return_value=BuildState(0, 2))
        self.trader.capture.read.side_effect = [[], [Mock(opcode=0x1d, body=b'Callipus M2 requires:')]]
        with patch('manufacture_batch.proxy_commands.submit') as submit:
            submit.side_effect = lambda args, report, **kwargs: report.update(state='unknown')
            with self.assertRaisesRegex(RuntimeError, 'Uncertain'):
                self.batch.build_one()
        submit.assert_called_once()
        self.assertEqual(self.batch.built, 0)

    def test_parallel_submits_two_requests_then_confirms_both_creations(self) -> None:
        before = BuildState(7, 11)
        after = BuildState(9, 9)
        self.batch.recipe_selected = True
        self.batch.snapshot = Mock(side_effect=[before, before, before, after])
        self.trader.capture.read.return_value = []
        with patch('manufacture_batch.proxy_commands.submit') as submit:
            submit.side_effect = lambda args, report, **kwargs: report.update(state='handler_returned')
            self.batch.build_many(2)
        self.assertEqual(submit.call_count, 2)
        self.assertEqual(self.batch.built, 2)
        self.assertEqual(self.batch.report['products_in_hull'], 9)

    def test_parallel_partial_creation_times_out_without_resubmission(self) -> None:
        before = BuildState(0, 2)
        self.batch.recipe_selected = True
        self.batch.snapshot = Mock(side_effect=[before, before, before, BuildState(1, 1)])
        self.trader.controller.active_time.side_effect = [0, 16]
        with patch('manufacture_batch.proxy_commands.submit') as submit:
            submit.side_effect = lambda args, report, **kwargs: report.update(state='handler_returned')
            with self.assertRaisesRegex(TimeoutError, 'requested=2, created=1'):
                self.batch.build_many(2)
        self.assertEqual(submit.call_count, 2)
        self.assertEqual(self.batch.built, 1)
        self.trader.sell_one.assert_not_called()

    def test_four_submissions_allow_earlier_creations_while_sending(self) -> None:
        self.batch.recipe_selected = True
        before = BuildState(0, 8)
        self.batch.snapshot = Mock(side_effect=[before, before, BuildState(1, 7),
                                               BuildState(2, 6), BuildState(3, 5), BuildState(4, 4)])
        with patch('manufacture_batch.proxy_commands.submit') as submit:
            submit.side_effect = lambda args, report, **kwargs: report.update(state='handler_returned')
            self.batch.build_many(4)
        self.assertEqual(submit.call_count, 4)
        self.assertEqual(self.batch.built, 4)

    def test_requested_parallelism_is_capped_by_free_hull_slots(self) -> None:
        self.batch.parallel = 100
        full = BuildState(3, 0)
        self.batch.snapshot = Mock(side_effect=[BuildState(0, 3), full, full, BuildState(0, 3)])
        self.batch.build_many = Mock()
        self.trader.cargo.matching.return_value = {10: 3}
        self.assertEqual(self.batch.run()['state'], 'completed')
        self.batch.build_many.assert_called_once_with(3)

    def test_nonpositive_parallelism_is_rejected(self) -> None:
        for count in (0, -1):
            with self.assertRaises(ValueError):
                ManufactureBatch(self.trader, parallel=count)

    def test_recipe_selection_is_reused_between_confirmed_builds(self) -> None:
        self.batch.recipe_selected = True
        before, after = BuildState(0, 2), BuildState(1, 1)
        self.batch.snapshot = Mock(side_effect=[before, before, after])
        with patch('manufacture_batch.proxy_commands.submit') as submit:
            submit.side_effect = lambda args, report, **kwargs: report.update(state='handler_returned')
            self.batch.build_one()
        self.trader.send.assert_not_called()

    def test_cancellation_stops_and_closes_capture(self) -> None:
        self.trader.checkpoint.side_effect = InterruptedError('stop')
        report = self.batch.run()
        self.assertEqual(report['state'], 'stopped')
        self.trader.send.assert_not_called()
        self.trader.capture.close.assert_called_once()
        self.trader.inventory.close.assert_called_once()


class ManufactureRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'diagnostics').mkdir()
        targets = ['manufacture_batch.load_player_config', 'manufacture_batch.PLAYER.configure',
                   'manufacture_batch.configure_event_log', 'manufacture_batch.AcController',
                   'manufacture_batch.VendorSession.from_instance', 'manufacture_batch.VendorTrader',
                   'manufacture_batch.ManufactureBatch', 'gui.find_window_title',
                   'focus.process_identity', 'win32process.GetWindowThreadProcessId']
        self.mocks: dict[str, Mock] = {}
        for target in targets:
            patcher = patch(target)
            self.mocks[target] = patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('manufacture_batch.ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mocks['manufacture_batch.load_player_config'].return_value.name = 'macboyje'
        self.controller = self.mocks['manufacture_batch.AcController'].return_value
        self.controller.startup_popup = False
        self.controller.window = 10
        self.mocks['gui.find_window_title'].return_value = 10
        self.mocks['win32process.GetWindowThreadProcessId'].return_value = (1, 100)
        self.mocks['focus.process_identity'].return_value = 'birth'
        session = self.mocks['manufacture_batch.VendorSession.from_instance'].return_value
        session.client_pid = 100
        session.client_born = 'birth'

    def batch(self, state: str = 'completed') -> Mock:
        batch = Mock()
        batch.report = {}
        batch.built = batch.sold = 18
        batch.run.return_value = {'state': state}
        return batch

    def test_default_runs_exactly_one_batch_and_closes_controller(self) -> None:
        self.mocks['manufacture_batch.ManufactureBatch'].return_value = self.batch()
        report = run_window('ENB_macboyje')
        self.assertEqual(report['completed_batches'], 1)
        self.assertEqual(report['total_sold'], 18)
        self.mocks['manufacture_batch.ManufactureBatch'].assert_called_once()
        self.controller.close.assert_called_once()

    def test_requested_batch_limit_is_honored(self) -> None:
        factory = self.mocks['manufacture_batch.ManufactureBatch']
        factory.side_effect = [self.batch(), self.batch()]
        report = run_window('ENB_macboyje', batches=2)
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(report['total_built'], 36)

    def test_continuous_stops_on_first_failed_batch_without_retry(self) -> None:
        factory = self.mocks['manufacture_batch.ManufactureBatch']
        factory.side_effect = [self.batch(), self.batch('stopped')]
        report = run_window('ENB_macboyje', batches=0)
        self.assertEqual(report['state'], 'stopped')
        self.assertEqual(factory.call_count, 2)
        self.controller.close.assert_called_once()

    def test_window_identity_change_submits_no_batch(self) -> None:
        self.controller.window = 999
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            run_window('ENB_macboyje')
        self.mocks['manufacture_batch.ManufactureBatch'].assert_not_called()
        self.controller.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
