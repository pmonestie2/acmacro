from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from npc_trade import NpcCatalog, NpcRecord, matches, open_npc, trade_choice


class CatalogTests(unittest.TestCase):
    def test_lenient_matching(self) -> None:
        for query, text in [('orb wever', 'ML-X4 "Orb Weaver" Mk. II'),
                            ('JULIE bristl', 'Julie Bristol'),
                            ('hneydoo', "Dr. B’nzen H’neydoo"),
                            ('mlx4', 'ML-X4 Orb Weaver')]:
            self.assertTrue(matches(query, text), (query, text))
        self.assertFalse(matches('', 'anything'))
        self.assertFalse(matches('kraken', 'Orb Weaver'))

    def test_catalog_searches_npc_names_and_items_without_legacy_roster_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'protocol').mkdir()
            (root / 'diagnostics/npc_source').mkdir(parents=True)
            (root / 'protocol/npc_vendor_catalog.json').write_text(json.dumps({'npcs': [
                {'npc_id': 32, 'items': [{'name': 'ML-X4 Orb Weaver'}],
                 'dialog': {'text': 'Weapons?', 'replies': [{'selection': 2, 'text': 'Trade.'}]}},
                {'npc_id': 28, 'items': [], 'dialog': {'text': 'Julie', 'replies': []}},
                {'npc_id': 600, 'items': [{'name': 'Reactor'}], 'dialog': {'text': 'Choose',
                    'replies': [{'selection': 2, 'text': 'No'}, {'selection': 3, 'text': 'Trade!'}]}}
            ]}))
            (root / 'diagnostics/npc_source/sigseg1v_db_mysql_net7.sql').write_text(
                "INSERT INTO `starbase_npcs` VALUES ('28', 'Julie', 'Bristol', '7');\n"
                "INSERT INTO `starbase_npcs` VALUES ('32', 'Weapon', 'Seller', '7');\n")
            (root / 'protocol/npc_name_catalog.json').write_text(json.dumps({'observations': [
                {'kind': 'historical_station_npc', 'npc_id': 3, 'name': 'Julie Bristol'}]}))
            catalog = NpcCatalog(root)
            self.assertEqual([r.npc_id for r in catalog.search('julie bristl')], [28])
            self.assertEqual([r.npc_id for r in catalog.search('orb wever')], [32])
            self.assertEqual(catalog.require(600).trade_choice, 3)
            self.assertIsNone(catalog.require(28).trade_choice)
            with self.assertRaises(ValueError):
                catalog.require(9999)

    def test_trade_choice_does_not_choose_no_thanks(self) -> None:
        self.assertEqual(trade_choice([{'selection': 2, 'text': 'No thanks'},
                                      {'selection': 3, 'text': 'Trade!'}]), 3)


class OpenNpcTests(unittest.TestCase):
    def setUp(self) -> None:
        targets = ['ac.AcController', 'arrange_windows.client_path', 'chatproc.require_admin',
                   'focus.process_identity', 'focus.SharedFocus', 'gui.find_window_title',
                   'game_instance.parent_process_id', 'macro_control.PauseControl',
                   'protocol.dialog_capture.decode_talk_tree',
                   'protocol.vendor_trading.VendorSession.from_processes',
                   'protocol.vendor_trading.VendorTrader', 'util.configure_event_log',
                   'util.log_event', 'win32process.GetWindowThreadProcessId']
        self.mocks: dict[str, Mock] = {}
        for target in targets:
            patcher = patch(target)
            self.mocks[target] = patcher.start()
            self.addCleanup(patcher.stop)
        self.mocks['gui.find_window_title'].return_value = 10
        self.mocks['win32process.GetWindowThreadProcessId'].return_value = (1, 100)
        self.mocks['focus.process_identity'].side_effect = lambda pid: 'client-born' if pid == 100 else 'a-proxy-born'
        self.mocks['game_instance.parent_process_id'].return_value = 200
        self.mocks['arrange_windows.client_path'].side_effect = lambda pid: Path('client.exe' if pid == 100 else 'net7proxy.exe')
        self.controller = self.mocks['ac.AcController'].return_value
        self.controller.window = 10
        self.controller.instance.pid = 100
        self.controller.instance.born = 'birth'
        self.controller.active_time.return_value = 0
        self.mocks['protocol.vendor_trading.VendorSession.from_processes'].return_value.game_id = 0x40000001
        self.trader = self.mocks['protocol.vendor_trading.VendorTrader'].return_value
        reply = Mock(opcode=0x54, body=b'greeting')
        self.trader.capture.read.side_effect = [[], [reply], []]
        self.dialog = {'text': 'Greeting', 'replies': [{'selection': 3, 'text': 'Trade!'}]}
        self.mocks['protocol.dialog_capture.decode_talk_tree'].return_value = self.dialog
        self.record = NpcRecord(600, 'Vendor', trade_choice=3, trade_text='Trade!')

    def test_opens_recorded_trade_choice_and_leaves_open(self) -> None:
        open_npc('ENB_macboytt', self.record)
        self.assertEqual([call.args[0] for call in self.trader.send.call_args_list], [0x4e, 0x55])
        self.assertEqual(self.trader.send.call_args_list[1].args[1][-1], 3)
        self.trader.wait_reply.assert_called_once()
        self.trader.close_trade.assert_called_once()  # Only closes the previous session.
        self.trader.capture.close.assert_called_once()
        self.controller.close.assert_called_once()

    def test_dialogue_only_does_not_select_reply(self) -> None:
        open_npc('ENB_macboytt', self.record, dialogue_only=True)
        self.assertEqual(self.trader.send.call_count, 1)

    def test_uncataloged_trade_choice_uses_explicit_live_trade_reply(self) -> None:
        self.record.trade_choice = None
        open_npc('ENB_macboytt', self.record)
        self.assertEqual(self.trader.send.call_args_list[-1].args[1][-1], 3)

    def test_no_trade_reply_leaves_dialogue_open(self) -> None:
        self.record.trade_choice = None
        self.dialog['replies'][0]['text'] = 'Tell me about yourself'
        open_npc('ENB_macboytt', self.record)
        self.assertEqual(self.trader.send.call_count, 1)

    def test_changed_reply_stops_without_selecting_or_retry(self) -> None:
        self.dialog['replies'][0]['text'] = 'Accept dangerous mission'
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            open_npc('ENB_macboytt', self.record)
        self.assertEqual(self.trader.send.call_count, 1)
        self.controller.close.assert_called_once()

    def test_changed_window_identity_sends_nothing(self) -> None:
        self.controller.window = 999
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            open_npc('ENB_macboytt', self.record)
        self.mocks['protocol.vendor_trading.VendorTrader'].assert_not_called()
        self.controller.close.assert_called_once()

    def test_timeout_does_not_retry_and_closes_capture(self) -> None:
        self.controller.active_time.side_effect = [0, 11]
        with self.assertRaises(TimeoutError):
            open_npc('ENB_macboytt', self.record)
        self.assertEqual(self.trader.send.call_count, 1)
        self.trader.capture.close.assert_called_once()

    def test_capture_failure_cleans_up(self) -> None:
        self.trader.capture.open.side_effect = OSError('capture failed')
        with self.assertRaises(OSError):
            open_npc('ENB_macboytt', self.record)
        self.controller.close.assert_called_once()
        self.trader.capture.close.assert_called_once()

    def test_arbitrary_named_window_needs_no_config(self) -> None:
        open_npc('ENB_a', self.record)
        self.mocks['ac.AcController'].assert_called_once_with('ENB_a')
        self.mocks['macro_control.PauseControl'].assert_called_once_with('a')


class SessionBindingTests(unittest.TestCase):
    def test_config_free_binding_resolves_entity_and_closes_memory_handle(self) -> None:
        from protocol.vendor_trading import VendorSession
        identities = {100: 'client-born', 200: 'proxy-born'}
        with patch('protocol.vendor_trading.process_identity', side_effect=identities.get), \
                patch('protocol.vendor_trading.proxy_commands.Process') as memory:
            memory.return_value.u32.side_effect = [0x1000, 0x2000, 0x3c204]
            session = VendorSession.from_processes(100, 'client-born', 200, 'proxy-born')
        self.assertEqual(session.game_id, 0x4003c204)
        memory.assert_called_once_with(200)
        memory.return_value.k.CloseHandle.assert_called_once_with(memory.return_value.handle)

    def test_replaced_proxy_prevents_memory_access(self) -> None:
        from protocol.vendor_trading import VendorSession
        with patch('protocol.vendor_trading.process_identity', side_effect=['client-born', 'different-birth']), \
                patch('protocol.vendor_trading.proxy_commands.Process') as memory:
            with self.assertRaisesRegex(RuntimeError, 'exited or changed'):
                VendorSession.from_processes(100, 'client-born', 200, 'proxy-born')
        memory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
