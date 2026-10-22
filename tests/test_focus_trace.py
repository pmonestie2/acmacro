import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from focus_trace import FocusTrace


class FocusTraceTests(unittest.TestCase):
    def test_events_and_minute_summary_record_handoffs_and_hold_times(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "focus.jsonl"
            clock = [0.0]
            with patch("focus_trace.time.monotonic", side_effect=lambda: clock[0]):
                trace = FocusTrace(path, "Test")
                trace.record("acquired", handoff=True, wait_seconds=0.4)
                clock[0] = 3
                trace.record("released", reason="waiting peer", held_seconds=3)
                clock[0] = 60
                trace.record("requested", owner="Other")
                trace.close()
            rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(rows[-1]["event"], "summary")
        self.assertEqual(rows[-1]["handoffs"], 1)
        self.assertEqual(rows[-1]["average_hold_seconds"], 3)
        self.assertEqual(rows[-1]["requests"], 1)
        self.assertEqual(rows[1]["reason"], "waiting peer")
