"""File-only focus telemetry. Never records input text or account settings."""
from collections import deque
from datetime import datetime
import json
from pathlib import Path
import sys
import time


class FocusTrace:
    def __init__(self, path, toon):
        self.stream = Path(path).open("a", encoding="utf-8")
        self.toon = toon
        self.recent = deque()
        self.summary_at = time.monotonic()

    def record(self, event, **fields):
        now = time.monotonic()
        self.recent.append((now, event, fields))
        while self.recent and self.recent[0][0] < now - 60:
            self.recent.popleft()
        frame = sys._getframe(1)
        context = []
        while frame:
            name = Path(frame.f_code.co_filename).name
            if name in {"ac.py", "pluto.py", "trade.py"} and frame.f_code.co_name != "run":
                context.append(f"{name}:{frame.f_lineno}:{frame.f_code.co_name}")
            frame = frame.f_back
        self.write(event, context=list(reversed(context)), **fields)
        if now - self.summary_at >= 60:
            acquisitions = [entry[2] for entry in self.recent if entry[1] == "acquired"]
            releases = [entry[2] for entry in self.recent if entry[1] == "released"]
            self.write("summary", window_seconds=60,
                acquisitions=len(acquisitions),
                handoffs=sum(bool(item.get("handoff")) for item in acquisitions),
                requests=sum(entry[1] == "requested" for entry in self.recent),
                wait_seconds=round(sum(item.get("wait_seconds", 0) for item in acquisitions), 3),
                average_hold_seconds=round(sum(item.get("held_seconds", 0) for item in releases) / max(1, len(releases)), 3))
            self.summary_at = now

    def write(self, event, **fields):
        self.stream.write(json.dumps({"time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "toon": self.toon, "event": event, **fields}) + "\n")
        self.stream.flush()

    def close(self):
        self.stream.close()
