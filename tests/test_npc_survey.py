import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from diagnostics.survey_npc_vendors import NpcSurvey, write_catalog
from protocol.vendor_catalog import StockItem


class NpcSurveyTests(unittest.TestCase):
    def test_catalog_write_retries_a_reader_lock(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'catalog.json'
            original = Path.replace
            calls = 0
            def replace(source: Path, destination: Path) -> Path:
                nonlocal calls
                calls += 1
                if calls < 3:
                    raise PermissionError('reader lock')
                return original(source, destination)
            with patch.object(Path, 'replace', replace), patch('diagnostics.survey_npc_vendors.time.sleep'):
                write_catalog(path, {'state': 'complete'})
            self.assertIn('complete', path.read_text())
            self.assertEqual(calls, 3)

    def test_capture_is_closed_even_when_final_save_fails(self) -> None:
        survey = NpcSurvey.__new__(NpcSurvey)
        survey.proxy_pid = 1
        survey.capture_path = Mock()
        survey.capture_path.with_suffix.return_value.exists.return_value = True
        survey.query_ids = []
        survey.probe = Mock(return_value={'status': 'vendor'})
        survey.save = Mock(side_effect=[None, PermissionError('reader lock')])
        process = Mock()
        process.poll.return_value = None
        with patch('diagnostics.survey_npc_vendors.subprocess.Popen', return_value=process):
            with self.assertRaises(PermissionError):
                survey.run()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)

    def survey(self) -> NpcSurvey:
        survey = NpcSurvey.__new__(NpcSurvey)
        survey.close = Mock()
        survey.send = Mock(return_value=10.0)
        survey.wait = Mock(return_value=b'dialog')
        survey.player_id = 123
        survey.trade_open_at = 10.0
        survey.stock_at = 10.0
        survey.stock_known = True
        survey.items = [StockItem(0, 1317, 100)]
        return survey

    def test_introduction_can_open_trade_without_another_trade_selection(self) -> None:
        survey = self.survey()
        opening = {'text': 'I provide level 1 components',
                   'replies': [{'selection': 3, 'text': 'Hail Lucuis, my name is Test.'}]}
        trading = {'text': 'Let me show you what I can offer you.',
                   'replies': [{'selection': 4, 'text': 'Goodbye'}]}
        with patch('diagnostics.survey_npc_vendors.decode_talk_tree', side_effect=[opening, trading]):
            record = survey.probe(274)
        self.assertEqual(record['status'], 'vendor')
        self.assertEqual(record['item_count'], 1)
        self.assertEqual(survey.send.call_count, 2)

    def test_unrelated_dialogue_does_not_select_a_quest_branch(self) -> None:
        survey = self.survey()
        dialog = {'text': 'Do you want work?', 'replies': [{'selection': 2, 'text': 'Yes, please.'}]}
        with patch('diagnostics.survey_npc_vendors.decode_talk_tree', return_value=dialog):
            record = survey.probe(173)
        self.assertEqual(record['status'], 'no_trade_choice')
        self.assertEqual(survey.send.call_count, 1)


if __name__ == '__main__':
    unittest.main()
