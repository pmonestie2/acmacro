import contextlib
import io
import logging
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import util


class EventLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "macro.log"
        self.logger = logging.Logger("test.events", level=logging.INFO)
        patcher = patch.object(util, "_event_logger", self.logger)
        patcher.start()
        self.addCleanup(patcher.stop)
        prefix = patch.object(util, '_player_prefix', '')
        prefix.start()
        self.addCleanup(prefix.stop)
        self.addCleanup(lambda: [handler.close() for handler in self.logger.handlers])
        util.configure_event_log(self.path)

    def test_player_name_prefixes_console_and_file_including_worker_logs(self) -> None:
        util.configure_event_log(self.path, player='macboyje')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            util.log('routine position check')
            util.log_event('Vendor failed: timeout')
            worker = threading.Thread(target=util.log_event, args=('Worker stopped',))
            worker.start()
            worker.join()
        for line in output.getvalue().splitlines():
            self.assertRegex(line, r'^\[\d{2}:\d{2}:\d{2}\] \[macboyje\] ')
        lines = self.path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all('] [macboyje] ' in line for line in lines))

    def test_reconfiguring_without_player_resets_the_prefix(self) -> None:
        util.configure_event_log(self.path, player='macboyje')
        util.configure_event_log(self.path)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            util.log_event('Generic macro')
        self.assertNotIn('macboyje', output.getvalue() + self.path.read_text(encoding='utf-8'))

    def test_appends_flushes_and_excludes_routine_diagnostics(self):
        with contextlib.redirect_stdout(io.StringIO()):
            util.log_event("Login started")
            self.assertIn("Login started", self.path.read_text(encoding="utf-8"))
            util.log("routine position check")
            util.configure_event_log(self.path)
            util.log_event("Private message from Alice: hello\nagain")
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] Login started$")
        self.assertIn("hello\\nagain", lines[1])
        self.assertNotIn("routine position check", "\n".join(lines))

    def test_concurrent_event_writes_are_complete_lines(self):
        def write(prefix):
            for index in range(20):
                util.log_event(f"{prefix}-{index}")
        with contextlib.redirect_stdout(io.StringIO()):
            threads = [threading.Thread(target=write, args=(prefix,)) for prefix in ("chat", "login")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        lines = self.path.read_text(encoding="utf-8").splitlines()
        messages = [line.split("] ", 1)[1] for line in lines]
        self.assertCountEqual(messages, [f"{prefix}-{index}" for prefix in ("chat", "login") for index in range(20)])

    def test_job_counts_and_pickup_progress_are_written_to_macro_file(self):
        import ac
        import pluto
        from player import Player
        controller = ac.AcController.__new__(ac.AcController)
        controller.start_packet_capture = Mock()
        controller._packet_monitor = Mock()
        controller._packet_monitor.mission_snapshot.return_value = {
            'count': 1, 'missions': [{'slot': 0, 'name': 'Promotion'}]}
        runner = pluto.PlutoRunner(low_job=True)
        runner.controller = Mock()
        runner.controller.count_mission.side_effect = [1, 2]
        runner.controller.polling_time.return_value = 0
        runner.open_job = runner.sort_job = runner.pickBatchJob = Mock()
        player = Player()
        player.mission_count = 2
        with contextlib.redirect_stdout(io.StringIO()), patch.object(pluto, 'PLAYER', player):
            controller.count_mission()
            runner.pick_job()
        contents = self.path.read_text(encoding='utf-8')
        for message in ('Mission count=1 source=packet', '1:Promotion',
                        'Job pickup started: count=1, target=2',
                        'Job pickup batch 1: before=1, after=2, change=1',
                        'Job pickup finished: count=2, target=2, target_met=True'):
            self.assertIn(message, contents)


if __name__ == "__main__":
    unittest.main()
