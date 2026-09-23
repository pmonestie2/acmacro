import contextlib
import io
import logging
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

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
        self.addCleanup(lambda: [handler.close() for handler in self.logger.handlers])
        util.configure_event_log(self.path)

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


if __name__ == "__main__":
    unittest.main()
